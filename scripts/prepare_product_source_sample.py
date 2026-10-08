"""Create complete original product paragraph/figure sample; no model/network."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mingjing.evidence import sha256_file
from scripts.source_customer_table_acceptance import extract_table_excerpt, protected_unchanged

DRAFT='7a70da91863feb0ce795748619f02b21a2b68ab6ec134c5520239b658e686a9d'
SOURCES={'595ba27f8900c41ceac19facbc2de5154b2c20bd71749b8affce1cc1f31dbeff',
         '9f351a9852deaf4b6365dafb9c8195debee341277352bf91b69060c27c069ed1'}
REGIONS=((7,(170,628,565,678)),(8,(170,70,560,247)))


def prepare(seed,out):
    seed,out=Path(seed).resolve(),Path(out).resolve()
    if out.exists() or out==seed or out.is_relative_to(seed) or seed.is_relative_to(out):
        raise ValueError('Sample output must be new and separate from historical seed')
    lock=seed/'agent_lock.json';result=seed/'results.json'
    data=json.loads(lock.read_text(encoding='utf8'))
    baseline=json.loads(result.read_text(encoding='utf8'))
    draft=[r for r in data['resources'] if r.get('id')=='draft' and r.get('sha256')==DRAFT]
    reports=[r for r in data['resources'] if r.get('sha256') in SOURCES and r.get('id','').startswith('report-')]
    if len(draft)!=1 or len(reports)!=2 or baseline.get('report_date')!='2024-04-22':
        raise ValueError('Original immutable draft/source identities or cutoff unavailable')
    protected={str(lock):sha256_file(lock),str(result):sha256_file(result)}
    for resource in [*draft,*reports]:
        path=Path(resource['path'])
        if not path.is_file() or sha256_file(path)!=resource['sha256']:
            raise ValueError('Original resource bytes differ from their historical lock')
        protected[str(path)]=resource['sha256']
    if any(not r.get('publish_date') or r['publish_date']>'2024-04-22' for r in reports):
        raise ValueError('Future or undated source cannot enter the historical sample')
    out.mkdir(parents=True)
    target=out/'product-original-pages-7-8.pdf'
    excerpt=extract_table_excerpt(draft[0]['path'],target,regions=REGIONS)
    excerpt.update(table_number=10,sample_kind='complete_original_product_income_paragraph_and_figure',
        source_original_occurrences=5,source_financial_recall='not_measured_requires_independent_gold')
    (out/'excerpt_manifest.json').write_text(json.dumps(excerpt,ensure_ascii=False,indent=2),encoding='utf8')
    primary=next(r for r in reports if r['sha256'].startswith('595'))
    prospectus=next(r for r in reports if r['sha256'].startswith('9f'))
    request={'draft':str(target),'corpus':primary['path'],'corpus_additions':[prospectus['path']],
        'company':baseline['company'],'period':'','report_date':'2024-04-22','mode':'harness',
        'task_type':'check','facts':'','knowledge_root':'','scope_declaration':''}
    state=out/'diagnostic-seed';state.mkdir()
    (state/'agent_state.json').write_text(json.dumps({'request':request},ensure_ascii=False,indent=2),encoding='utf8')
    receipt={'schema_version':'product-source-local-sample.v1','status':'prepared_not_executed',
        'full_report_acceptance':False,'real_provider_api':'not_executed',
        'kernel_replay':'not_executed','agent_replay':'not_executed','request':request,
        'protected_hashes':protected,'historical_seed_unchanged':protected_unchanged(protected),
        'source_resources':reports,'sample_manifest_sha256':sha256_file(out/'excerpt_manifest.json'),
        'selection_basis':'complete_predeclared_source_paragraph_and_figure_geometry_not_candidate_values',
        'all_excerpt_candidates_retained':True,'expected_source_financial_occurrences':5}
    if not receipt['historical_seed_unchanged']:
        raise ValueError('Historical resources changed during sample preparation')
    (out/'preparation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-run',type=Path,default=ROOT/'runs/source-real-stable-16-full')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    receipt=prepare(args.seed_run,args.out)
    print(json.dumps({key:receipt[key] for key in ('status','historical_seed_unchanged','expected_source_financial_occurrences')},ensure_ascii=False))
