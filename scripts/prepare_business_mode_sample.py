"""Prepare a provenance-checked p31 local probe; no API or credential access.

The fixed rectangle preserves the complete financial paragraph, the preceding
source boundary, figure 43, and its own first-inquiry source footer. All PDF
candidates remain visible. The official PDF stays at its locked external path.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mingjing.agent import write_json
from mingjing.coverage import CoverageLedger, partition_document
from mingjing.evidence import sha256_file
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.source_business_mode import extract_source_facts
from mingjing.source_semantics import freeze_source_dimensions
from mingjing.source_table_reference import link_quoted_tables, validated_table_reference
from scripts.source_customer_table_acceptance import (
    DRAFT_SHA256, CUTOFF, _locked_path, extract_table_excerpt, protected_unchanged,
)
from scripts.source_web_acceptance import new_output

SOURCE_SHA256 = 'ed4bf57ba31ee1d7e87c40052903ecfbd0d12791af1b82ac6ad3b28e673dc6dd'
REGIONS = ((31, (36.0, 260.0, 575.0, 590.0)),)


def prepare(seed, out):
    seed=seed.resolve()
    out=out.resolve()
    if out==seed or out.is_relative_to(seed) or seed.is_relative_to(out):
        raise ValueError('Diagnostic output must be separate from historical source material')
    locked=json.loads((seed/'agent_lock.json').read_text(encoding='utf-8'))
    result=json.loads((seed/'results.json').read_text(encoding='utf-8'))
    if result['report_date']!=CUTOFF:
        raise ValueError('Historical cutoff changed')
    drafts=[r for r in locked['resources'] if r['id']=='draft' and r['sha256']==DRAFT_SHA256]
    sources=[r for r in locked['resources'] if r.get('sha256')==SOURCE_SHA256]
    if len(drafts)!=1 or len(sources)!=1:
        raise ValueError('Original draft and nominated official first-inquiry resource are required')
    draft=_locked_path(drafts[0],expected=DRAFT_SHA256)
    source=_locked_path(sources[0],expected=SOURCE_SHA256)
    resource=sources[0]
    if resource.get('publish_date')!='2023-04-04':
        raise ValueError('Official disclosure date differs from original lock')
    protected={str(p):sha256_file(p) for p in [draft,source,seed/'agent_lock.json',seed/'results.json']}
    if resource.get('index_path'):
        index=_locked_path({'path':resource['index_path'],'sha256':resource['index_sha256']})
        protected[str(index)]=sha256_file(index)
    out=new_output(out)
    excerpt_path=out/'original-p31-business-mode.pdf'
    excerpt=extract_table_excerpt(draft,excerpt_path,regions=REGIONS)
    excerpt.pop('table_number',None)
    excerpt.update(selection_name='complete_original_paragraph_and_figure43_source',
                   nominated_source_sha256=SOURCE_SHA256,nominated_source_original_page=262)
    write_json(out/'excerpt_manifest.json',excerpt)
    request={'draft':str(excerpt_path),'corpus':str(source),'company':result['company'],
        'period':'','report_date':CUTOFF,'mode':'harness','task_type':'check','facts':'',
        'corpus_additions':[],'knowledge_root':'','scope_declaration':''}
    diagnostic_seed=out/'diagnostic-seed'
    diagnostic_seed.mkdir()
    write_json(diagnostic_seed/'agent_state.json',{'request':request})

    # A deterministic local development receipt, explicitly not a model run.
    # Read only the frozen real p262 fixture and revalidate its source identity.
    fixture_path=ROOT/'tests/fixtures/quoted_business_mode_p31.json'
    fixture=json.loads(fixture_path.read_text(encoding='utf-8'))
    if fixture['source']['sha256']!=SOURCE_SHA256 or fixture['draft_sha256']!=DRAFT_SHA256:
        raise ValueError('Real source fixture identity changed')
    facts=extract_source_facts(fixture['source']['pages'],path=source,company=result['company'],
        doc_sha=SOURCE_SHA256,disclosure_date=resource['publish_date'],disclosure_version='原披露')
    text,pages,blocks=partition_document(excerpt_path)
    ledger,claims=CoverageLedger.build(text,pages,blocks,result['company'],'2023Y')
    registry=[{'name':source.name,'companies':[result['company']],'sha256':SOURCE_SHA256,
        'publish_date':resource['publish_date'],'report_id':resource['id']}]
    claims,diagnostics=link_quoted_tables(claims,text,facts,registry,CUTOFF)
    quoted=[freeze_source_dimensions(replace(c,binding_status='agreed')) for c in claims
        if validated_table_reference(c)]
    judge=Judge(FactStore(facts),CUTOFF,require_dual_binding=True)
    outcomes=[judge.verify(c) for c in quoted]
    if len(quoted)!=6 or any(r.status!='verified' for r in outcomes):
        raise ValueError('Prepared original excerpt does not reproduce the six deterministic quoted cells')
    unchanged=protected_unchanged(protected)
    if not unchanged:
        raise ValueError('Historical source bytes changed during preparation')
    receipt={'status':'prepared_not_executed','scope':'p31_six_cell_development_probe',
        'real_provider_api':'not_executed','native_bindings':'not_executed',
        'full_report_acceptance':False,'independent_gold':False,'EXE_built':False,
        'request':request,'protected_hashes':protected,'historical_seed_unchanged':unchanged,
        'excerpt_manifest_sha256':sha256_file(out/'excerpt_manifest.json'),
        'fixture_sha256':sha256_file(fixture_path),'offline_summary':dict(Counter(r.status for r in outcomes)),
        'source_facts':len(facts),'claims_removed':0,'all_excerpt_claim_count':len(claims),
        'coverage':ledger.to_dict(),'selected_claims':[c.to_dict() for c in quoted],
        'results':[r.to_dict() for r in outcomes],'link_diagnostics':diagnostics,
        'limitations':['Three percentage checks only verify the explicitly cited disclosure table; '
            'the original paragraph does not independently define its denominator.',
            'Offline agreed bindings are diagnostic only, not actual model submissions.']}
    write_json(out/'preparation.json',receipt)
    print(json.dumps({key:receipt[key] for key in ('status','offline_summary','source_facts',
        'all_excerpt_claim_count','claims_removed','historical_seed_unchanged')},ensure_ascii=False))
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-run',type=Path,default=ROOT/'runs/source-real-stable-16-full')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    prepare(args.seed_run,args.out)


if __name__=='__main__':
    main()
