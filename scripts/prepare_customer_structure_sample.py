"""Prepare original customer structure regions without API or secret access."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mingjing.agent import write_json
from mingjing.coverage import CoverageLedger, partition_document
from mingjing.evidence import sha256_file
from mingjing.facts import FactStore
from mingjing.financial_notes import _extract_major_customer_facts
from mingjing.judge import Judge
from mingjing.source_customer_quotation import extract_source_facts, identity
from mingjing.source_semantics import freeze_source_dimensions
from mingjing.source_table_reference import link_quoted_tables, validated_table_reference
from scripts.source_customer_table_acceptance import DRAFT_SHA256,CUTOFF,_locked_path,extract_table_excerpt,protected_unchanged
from scripts.source_web_acceptance import new_output

SOURCE_SHA256='9f351a9852deaf4b6365dafb9c8195debee341277352bf91b69060c27c069ed1'
REGIONS={
    'type-six':((14,(36.0,522.0,575.0,775.0)),),
    'concentration-two':((13,(36.0,575.0,575.0,780.0)),(14,(36.0,70.0,575.0,283.0))),
    'complete-eight':((13,(36.0,575.0,575.0,780.0)),(14,(36.0,70.0,575.0,775.0))),
}


def prepare(seed,out,group):
    import fitz
    seed,out=seed.resolve(),out.resolve()
    if out==seed or out.is_relative_to(seed) or seed.is_relative_to(out):
        raise ValueError('Output must be separate from read-only historical evidence')
    lock=json.loads((seed/'agent_lock.json').read_text(encoding='utf-8'))
    original=json.loads((seed/'results.json').read_text(encoding='utf-8'))
    if original['report_date']!=CUTOFF:raise ValueError('Historical cutoff changed')
    drafts=[r for r in lock['resources'] if r['id']=='draft' and r['sha256']==DRAFT_SHA256]
    sources=[r for r in lock['resources'] if r.get('sha256')==SOURCE_SHA256]
    if len(drafts)!=1 or len(sources)!=1:raise ValueError('Required original draft and official prospectus are missing')
    draft,source=_locked_path(drafts[0],expected=DRAFT_SHA256),_locked_path(sources[0],expected=SOURCE_SHA256)
    resource=sources[0]
    if resource['publish_date']!='2023-10-16':raise ValueError('Official source date changed')
    protected={str(p):sha256_file(p) for p in [draft,source,seed/'agent_lock.json',seed/'results.json']}
    if resource.get('index_path'):
        index=_locked_path({'path':resource['index_path'],'sha256':resource['index_sha256']});protected[str(index)]=sha256_file(index)
    fixture_path=ROOT/'tests/fixtures/quoted_customer_structure_p13_p14.json'
    fixture=json.loads(fixture_path.read_text(encoding='utf-8'))
    if fixture['source']['sha256']!=SOURCE_SHA256 or fixture['draft_sha256']!=DRAFT_SHA256:
        raise ValueError('Original-source fixture changed identity')
    with fitz.open(source) as pdf:
        for page in fixture['source']['pages']:
            original_page=pdf[page['page']-1]
            if original_page.get_text('text')!=page['text']:
                raise ValueError('Fixture page does not match the nominated official PDF')
            native_blocks=[{'bbox':list(b[:4]),'text':b[4]} for b in original_page.get_text('blocks') if b[6]==0]
            if native_blocks!=page['blocks']:raise ValueError('Fixture evidence geometry differs from original blocks')
    out=new_output(out)
    excerpt_path=out/(group+'-original.pdf')
    excerpt=extract_table_excerpt(draft,excerpt_path,regions=REGIONS[group])
    excerpt.pop('table_number',None)
    excerpt.update(selection_name=group,nominated_source_sha256=SOURCE_SHA256,
        source_document_variant='申报稿',source_document_variant_basis='original_cover_text',
        source_filename_variant='注册稿',source_variant_metadata_conflict=True)
    write_json(out/'excerpt_manifest.json',excerpt)
    request={'draft':str(excerpt_path),'corpus':str(source),'company':original['company'],'period':'',
        'report_date':CUTOFF,'mode':'harness','task_type':'check','facts':'','corpus_additions':[],
        'knowledge_root':'','scope_declaration':''}
    diagnostic_seed=out/'diagnostic-seed';diagnostic_seed.mkdir()
    write_json(diagnostic_seed/'agent_state.json',{'request':request})
    kw={'path':str(source),'company':original['company'],'doc_sha':SOURCE_SHA256,
        'disclosure_date':resource['publish_date'],'disclosure_version':'原披露'}
    pages=fixture['source']['pages']
    facts=[*extract_source_facts(pages,**kw),*_extract_major_customer_facts(pages,effective_period='2023H1',**kw)]
    text,regions,blocks=partition_document(excerpt_path)
    ledger,claims=CoverageLedger.build(text,regions,blocks,original['company'],'2023Y')
    reports=[{'name':source.name,'sha256':SOURCE_SHA256,'companies':[original['company']],
        'report_id':resource['id'],'publish_date':resource['publish_date'],'source_document_variant':'申报稿'}]
    claims,diagnostics=link_quoted_tables(claims,text,facts,reports,CUTOFF)
    chosen=[freeze_source_dimensions(replace(c,binding_status='agreed')) for c in claims if identity(c) and validated_table_reference(c)]
    expected={'type-six':6,'concentration-two':2,'complete-eight':8}[group]
    results=[Judge(FactStore(facts),CUTOFF,require_dual_binding=True).verify(c) for c in chosen]
    if len(chosen)!=expected or any(r.status!='verified' for r in results):
        raise ValueError('Fixed original excerpt failed deterministic source-quotation preflight')
    # Position mapping retains the raw occurrence in the original task. The
    # chart body is source-local noncontiguous text, so its source page/label
    # spans are located by exact raw original text and physical rectangle.
    mappings=[]
    for claim,result in zip(chosen,results):
        observation=identity(claim);proof=claim.table_context['dimension_source']
        if observation['quotation_kind']=='customer_type_chart':
            local_page=next(p for p in regions if p['start']<=claim.start<claim.end<=p['end'])
            mapped_page=excerpt['pages'][local_page['page']-1]['original_page']
            official_page=next(p for p in original['coverage']['pages'] if p['page']==mapped_page)
            cell=proof['cell']
            candidates=[c for c in original['claims'] if official_page['start']<=c['start']<c['end']<=official_page['end']
                and c['numeric_text']==claim.numeric_text]
            # Unique original cell plus exact source geometry; no facts or
            # verdicts are consulted in matching the two raw draft instances.
            fixture_page=next(p for p in fixture['draft_pages'] if p['page']==mapped_page)
            positioned=[l for l in fixture_page['lines'] if l['text'].strip()==claim.numeric_text and l['bbox']==cell['bbox']]
            candidates=[c for c in candidates if any(c['start']==official_page['start']+line['start'] for line in positioned)]
        else:
            narrative=proof['narrative'];positions=[];at=0
            while True:
                position=original['draft_text'].find(narrative['text'],at)
                if position<0:break
                positions.append(position);at=position+len(narrative['text'])
            if len(positions)!=1:raise ValueError('Original narrative is not unique')
            left=positions[0]+claim.source_numeric_start
            candidates=[c for c in original['claims'] if c['start']==left and c['end']==left+len(claim.numeric_text)]
        if len(candidates)!=1:raise ValueError('Original financial occurrence is not uniquely located')
        old=candidates[0]
        mappings.append({'local_claim_id':claim.claim_id,'original_claim_id':old['claim_id'],
            'original_span':[old['start'],old['end']],'numeric_text':claim.numeric_text,
            'quotation_kind':observation['quotation_kind'],'status':result.status})
    if not protected_unchanged(protected):raise ValueError('Historical source changed during preparation')
    receipt={'status':'prepared_not_executed','group':group,'real_provider_api':'not_executed',
        'native_bindings':'not_executed','full_report_acceptance':False,'independent_gold':False,
        'claims_removed':0,'all_excerpt_claim_count':len(claims),'selected_claim_count':len(chosen),
        'offline_summary':dict(Counter(r.status for r in results)),'all_excerpt_ledger':ledger.to_dict(),
        'selected_claims':[c.to_dict() for c in chosen],'results':[r.to_dict() for r in results],
        'original_occurrence_mapping':mappings,'source_facts':len(facts),'request':request,
        'source_document_variant':'申报稿','source_filename_variant':'注册稿',
        'variant_metadata_conflict':True,'historical_seed_unchanged':True,'protected_hashes':protected,
        'fixture_sha256':sha256_file(fixture_path),'link_diagnostics':diagnostics,
        'limitations':['Source-defined customer-type shares use cloud-service revenue; original paragraphs '
            'do not independently specify that denominator. A4 remains.',
            'Offline agreed bindings are diagnostic only, not actual model submissions.']}
    write_json(out/'preparation.json',receipt)
    print(json.dumps({k:receipt[k] for k in ('status','group','all_excerpt_claim_count','selected_claim_count','offline_summary','historical_seed_unchanged')},ensure_ascii=False))
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-run',type=Path,default=ROOT/'runs/source-real-stable-16-full')
    parser.add_argument('--group',choices=REGIONS,default='type-six')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();prepare(args.seed_run,args.out,args.group)


if __name__=='__main__':main()
