"""Live model chooses a local-page tool; a frozen child applies its facts.

The draft is a disclosed-table transcription and the private index is
deliberately incomplete. Neither is an independent report-quality benchmark.
"""
from __future__ import annotations

import argparse
import sys
import json
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.import_integrity import assert_application_origin
assert_application_origin(ROOT)

from mingjing.agent import FinancialAgent, write_json
from mingjing.agent_replay import replay_agent
from mingjing.config import SecretStore, app_root, load_config
from mingjing.evidence import sha256_file
from mingjing.parser import extract_fact_window
from mingjing.providers import resolve_route
from mingjing.reparse import list_reparse_packets, resolve_reparse_packet
from mingjing.reparse_inputs import load_parent_context
from mingjing.replay import replay_run


def execute(args):
    source_run = json.loads((args.material_source/'parent-kernel'/'results.json').read_text(encoding='utf-8'))
    corpus = Path(source_run['corpus'])
    reports = list(corpus.glob('*.pdf'))
    if len(reports)!=1:
        raise ValueError('This development check requires one already-exposed PDF')
    source = reports[0]
    if any(row['account']=='REVENUE' for row in json.loads((corpus/'facts.json').read_text(encoding='utf-8'))):
        raise ValueError('Expected the documented private index with deliberately missing revenue')
    actual = extract_fact_window(source, pages=[83,84,85], company=source_run['company'],
        publish_date='2024-04-15', expected_sha256=sha256_file(source))
    fact = next(row for row in actual['facts'] if row.account=='REVENUE' and row.period=='2023Y')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    draft = out/'disclosed-table-transcription.md'
    draft.write_text('# 明镜局部补证开发样例\n\n本样例转写自锁定年报表格，使用明确口径，不是独立研报金标。\n\n'
        f'{fact.company}2023年合并营业收入{fact.value}元。\n', encoding='utf-8')
    saved = app_root()
    profile_hashes = {str(path):sha256_file(path) for path in (saved/'config.json',saved/'secrets.json') if path.is_file()}
    cfg = load_config(saved/'config.json').to_dict()
    secrets = SecretStore(saved)
    route = resolve_route(cfg)
    if not secrets.get(route['credential_ref']):
        raise ValueError('Saved route has no readable credential')
    cfg['agent']['enabled']=True
    cfg['agent']['cross_provider_fallback']=False
    cfg['agent']['budget'].update(max_model_calls=64,max_tool_calls=256,max_total_tokens=500000,max_runtime_s=1200)
    cfg['harness'].update(runtime_mode='embedded',enabled=False,executable='',home='',patch_file='',working_dir='')
    request={'draft':str(draft),'corpus':str(corpus),'company':fact.company,'period':'2023Y',
             'report_date':source_run['report_date'],'task_type':'check','scope_declaration':''}
    receipt={'status':'running','scope':'development_live_native_reparse_simulated_missing_index',
        'fixture_change':'Private index intentionally omits revenue; draft is an explicitly scoped table transcription',
        'provider':route['provider'],'model':route['model'],'source_sha256':sha256_file(source),
        'human_gold':'not_executed','D3':'not_executed','clean_windows_vm':'not_executed'}
    write_json(out/'acceptance.json',receipt)
    owners=[]
    def run_one(name, task_request):
        owner=FinancialAgent(task_request,cfg,secrets.get,out/name,run_id=args.out.name+'-'+name)
        owners.append(owner)
        def emit(event,**fields):
            if event=='tool':
                print(json.dumps({'task':name,'tool':fields.get('tool'),'role':fields.get('role'),
                    'requests':owner.native_request_count},ensure_ascii=False),flush=True)
        owner.emit=emit
        started=time.monotonic()
        data=owner.run()
        statistics={'model_run':data['model'],'summary':data['summary'],'partial':data['partial'],
            'elapsed_s':round(time.monotonic()-started,2),
            'native_tools':dict(Counter(row['tool'] for row in data['tool_trace'] if row['status']=='success')),
            'kernel_replay':replay_run(owner.out),
            'agent_replay':replay_agent(owner.out) if data['model']['status']=='success' else {'status':'not_executed'}}
        return owner,data,statistics
    try:
        owner,parent,receipt['parent']=run_one('native-parent',request)
        stages=[row for row in parent['tool_trace'] if row['tool']=='mj_request_reparse' and row['status']=='success'
                and row.get('output',{}).get('status')=='staged']
        if not stages:
            raise ValueError('Live Agent did not execute a useful local-page reparse')
        frozen={path:sha256_file(path) for path in owner.out.rglob('*') if path.is_file()}
        context=load_parent_context(owner.out)
        packet_root=owner.out.parent/'reparse'/owner.run_id
        proposals=list_reparse_packets(packet_root,expected_source_generation=context['source_generation'])
        proposal=next(row for row in proposals if row['status']=='ready')
        packet=resolve_reparse_packet(packet_root,proposal['packet_id'])
        receipt['published_packet']=proposal
        child_request=dict(request,reparse_parent_run=str(owner.out),_controlled_reparse_root=str(packet_root),
            reparse_packets=[{'path':str(packet),'packet_id':proposal['packet_id'],'sha256':sha256_file(packet),
                             'source_generation':context['source_generation']}])
        child_owner,child,receipt['child']=run_one('native-child',child_request)
        proved=[row for row in child['results'] if row['status']=='verified']
        receipt.update(parent_staged_calls=len(stages),
            parent_unchanged=all(path.is_file() and sha256_file(path)==sha for path,sha in frozen.items()),
            numeric_value_from_original_pdf=bool(proved) and all(row['expected']==str(fact.value) for row in proved),
            page_evidence_available=bool(proved) and all(identity in child['evidence_resources'] and
                (child_owner.out/child['evidence_resources'][identity]['image']).is_file() for row in proved for identity in row['evidence']),
            actual_native_staged_loop=True,
            source_unchanged=sha256_file(source)==receipt['source_sha256'])
        passed=(receipt['parent']['model_run']['status']==receipt['child']['model_run']['status']=='success'
            and not receipt['parent']['partial'] and not receipt['child']['partial']
            and receipt['parent']['summary']['verified']==0 and receipt['parent']['summary']['abstained']>0
            and receipt['child']['summary']['verified']>0 and receipt['child']['summary']['violated']==0
            and all(receipt[phase][mode]['status']=='passed' for phase in ('parent','child') for mode in ('kernel_replay','agent_replay'))
            and all(receipt[key] for key in ('parent_unchanged','numeric_value_from_original_pdf','page_evidence_available','source_unchanged')))
        receipt['status']='technical_native_workflow_passed_quality_pending' if passed else 'needs_improvement'
    except Exception as exc:
        receipt.update(status='failed',error=(owners[-1].redact(str(exc)) if owners else type(exc).__name__))
    finally:
        receipt['user_profile_unchanged']=all(Path(path).is_file() and sha256_file(path)==sha for path,sha in profile_hashes.items())
        keys=[secret.encode() for provider in cfg.get('providers',{}).values()
              if (secret:=secrets.get(provider.get('credential_ref','')))]
        receipt['plaintext_secret_found']=any(any(key in path.read_bytes() for key in keys)
            for path in out.rglob('*') if path.is_file() and path.suffix in {'.json','.jsonl','.md','.html','.log'})
        if not receipt['user_profile_unchanged'] or receipt['plaintext_secret_found']:
            receipt['status']='failed_profile_or_secret_scan'
        write_json(out/'acceptance.json',receipt)
    print(json.dumps({key:receipt.get(key) for key in ('status','parent','child','parent_staged_calls',
                    'parent_unchanged','user_profile_unchanged','error')},ensure_ascii=False),flush=True)
    return 0 if receipt['status']=='technical_native_workflow_passed_quality_pending' else 1


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--material-source',type=Path,default=Path('runs/source-real-reparse-next-01'))
    parser.add_argument('--out',type=Path,default=Path('runs/source-native-reparse-next-01'))
    return execute(parser.parse_args())


if __name__=='__main__':
    raise SystemExit(main())
