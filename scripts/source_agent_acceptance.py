"""Run the already-exposed real report through the native Harness in source mode.

No distribution build, asset extraction or mutation of the user's saved profile.
Development quality statistics are deliberately separate from human gold scores.
"""
from __future__ import annotations

import argparse
import sys
import json
import threading
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.import_integrity import assert_application_origin
assert_application_origin(ROOT)

from mingjing import __version__
from mingjing.agent import FinancialAgent, write_json
from mingjing.agent_replay import replay_agent
from mingjing.config import SecretStore, app_root, load_config
from mingjing.evidence import sha256_file
from mingjing.providers import resolve_route
from mingjing.replay import replay_run
from mingjing.http_transport import safe_error_message
from scripts.source_web_acceptance import new_output
from scripts.source_acceptance_inputs import (EVENT_KINDS, open_credentials, finish_receipt,
                                             page_evidence_diagnostic, positive_integer, profile_hashes, verify_seed_materials)


def validate_args(args):
    if args.task_type not in {'check', 'analysis'}:
        raise ValueError('task_type must be check or analysis')
    if not isinstance(args.expect_event, list) or any(kind not in EVENT_KINDS for kind in args.expect_event):
        raise ValueError('expect_event must use a registered accounting event kind: '+', '.join(EVENT_KINDS))
    if args.expect_event and args.task_type != 'analysis':
        raise ValueError('expect_event requires task_type=analysis')
    for field in ('model_calls', 'tool_calls', 'tokens', 'timeout'):
        positive_integer(getattr(args, field), field)
    if args.followup_tokens is not None:
        positive_integer(args.followup_tokens, 'followup_tokens')
    if args.scope_declaration is not None:
        from mingjing.scope import validate_scope_declaration
        validate_scope_declaration(args.scope_declaration)
    for field in ('state', 'out'):
        if not isinstance(getattr(args, field), Path):
            raise ValueError(field+' must be a path')
    return args


def execute(args):
    validate_args(args)
    out = new_output(args.out)
    cfg, credentials, agent = {}, None, None
    before = {}
    started = time.monotonic()
    finished = threading.Event()
    receipt = {'version':__version__,'status':'running','scope':'development_report_not_independent_financial_gold',
               'mode':'native_harness_source', 'task_type':args.task_type,
               'model_run': {'status': 'not_executed'}, 'usage': 'unknown',
               'kernel_replay': {'status': 'not_executed'}, 'agent_replay': {'status': 'not_executed'},
               'expected_events': args.expect_event, 'user_profile_unchanged': None,
               'external_acceptance':{'human_gold':'not_executed','D3':'not_executed','clean_windows_vm':'not_executed','Kimi':'not_executed','GPT':'not_executed'},
               'limitations':['Processing coverage is not measured financial mention recall','Cost unknown unless supplier billing is independently available']}
    write_json(out/'source_acceptance.json',receipt)
    try:
        receipt['phase'] = 'read_source_materials'
        state = json.loads(args.state.read_text(encoding='utf-8'))
        request = dict(state['request'])
        source_root = args.state.resolve().parents[2]
        for key in ('draft', 'corpus', 'facts'):
            if request.get(key):
                source = Path(request[key])
                request[key] = str((source_root/source).resolve() if not source.is_absolute() else source.resolve())
        receipt['seed_material_integrity'] = verify_seed_materials(args.state.resolve().parent,
            [request.get(key) for key in ('draft', 'corpus', 'facts')])
        request = {key:value for key,value in request.items() if key not in {'out','run_id'}}
        request.update(mode='harness',task_type=args.task_type)
        if args.scope_declaration is not None:
            request['scope_declaration'] = args.scope_declaration
        if args.corpus:
            request['corpus'] = str(args.corpus.resolve())
        if getattr(args, 'corpus_addition', None):
            request['corpus_additions'] = [*request.get('corpus_additions', []),
                                          *(str(path.resolve()) for path in args.corpus_addition)]
        if args.knowledge_root:
            request['knowledge_root'] = str(args.knowledge_root.resolve())
        if args.task_type == 'analysis':
            intent = out/'analysis-intent.md'
            intent.write_text('对锁定财报材料执行登记财务指标分析，所有数值由确定性内核和原始披露证据提供。', encoding='utf-8')
            request['draft'] = str(intent)
        receipt['materials'] = {'draft_sha256':sha256_file(request['draft']), 'corpus_path':request['corpus']}
        receipt['phase'] = 'read_saved_configuration'
        saved_root = app_root()
        before = profile_hashes(saved_root)
        cfg = load_config(saved_root/'config.json').to_dict()
        route = resolve_route(cfg)
        receipt.update(model=route['model'], provider=route['provider'], scope_declaration=request.get('scope_declaration') or None)
        receipt['phase'] = 'resolve_credentials'
        credentials = open_credentials(SecretStore, saved_root)
        if not credentials.get(route['credential_ref']):
            raise ValueError('Saved default route has no readable credential')
        credentials.read_configured(cfg)
        cfg['agent']['enabled'] = True
        cfg['agent']['cross_provider_fallback'] = False
        cfg['agent']['budget'].update(max_model_calls=args.model_calls,max_tool_calls=args.tool_calls,
                                      max_total_tokens=args.tokens,max_runtime_s=args.timeout)
        if args.followup_tokens is not None:
            cfg['agent'].setdefault('followup_budget', {})['max_total_tokens'] = args.followup_tokens
        cfg['harness'].update(runtime_mode='embedded',enabled=False,executable='',home='',patch_file='',working_dir='')
        receipt.update(budget=cfg['agent']['budget'], followup_budget=cfg['agent'].get('followup_budget'))
        receipt['phase'] = 'construct_agent'
        agent = FinancialAgent(request,cfg,credentials.get,out,run_id='source-real-'+out.name)
        last_tool = [0.0]
        def emit(event,**fields):
            if event=='tool' and time.monotonic()-last_tool[0]>15:
                last_tool[0]=time.monotonic()
                print(json.dumps({'stage':'tool','role':fields.get('role'),'tool':fields.get('tool'),
                                  'requests':agent.native_request_count,'tokens':agent.total_tokens if agent.usage_known else 'unknown'},ensure_ascii=False),flush=True)
        agent.emit = emit
        def heartbeat():
            while not finished.wait(30):
                print(json.dumps({'stage':'heartbeat','elapsed_s':round(time.monotonic()-started),
                      'requests':agent.native_request_count,'tokens':agent.total_tokens if agent.usage_known else 'unknown',
                      'bindings_A':len(agent.dispatcher.bindings['planner']) if hasattr(agent,'dispatcher') else 0,
                      'bindings_B':len(agent.dispatcher.bindings['verifier']) if hasattr(agent,'dispatcher') else 0}),flush=True)
        threading.Thread(target=heartbeat,daemon=True).start()
        receipt['phase'] = 'execute_native_task'
        data = agent.run()
        coverage = data['coverage']
        receipt.update(summary=data['summary'],model_run=data['model'],partial=data['partial'],
                       elapsed_s=round(time.monotonic()-started,2),claims=len(data['claims']),facts=len(data['facts']),
                       coverage={key:value for key,value in coverage.items() if key not in {'pages','blocks','mentions'}},
                       successful_tools=dict(Counter(row['tool'] for row in data['tool_trace'] if row['status']=='success')),
                       abstention_codes=dict(Counter(row.get('abstention_code') for row in data['results'] if row['status']=='abstained')),
                       evidence_references=sum(len(row.get('evidence',[])) for row in data['results']))
        from mingjing.execution import project_execution
        receipt['execution'] = project_execution(data)
        receipt['financial_coverage'] = data.get('coverage_statistics')
        receipt['financial_coverage_target_met'] = bool((data.get('coverage_statistics') or {}).get('abstention_target_met'))
        receipt['kernel_replay'] = replay_run(out)
        if data['model']['status']=='success':
            receipt['agent_replay'] = replay_agent(out)
        else:
            receipt['agent_replay'] = {'status':'not_executed','reason':'original_not_native_success'}
        numeric = [row for row in data['results'] if row['status'] in {'verified','violated'}]
        analysis = data.get('analysis') or {}
        receipt['financial_analysis'] = {
            'metric_statuses': dict(Counter(row['status'] for row in analysis.get('metrics', []))),
            'signals': len(analysis.get('signals', [])),
            'event_kinds': dict(Counter(row['kind'] for row in analysis.get('events', [])))}
        if args.task_type == 'analysis':
            numeric += [row for row in (data.get('analysis') or {}).get('metrics', []) if row['status']=='verified']
        receipt['numeric_conclusion_count'] = len(numeric)
        receipt['numeric_evidence_complete'] = (all(row.get('evidence') and
            row.get('operands') and row.get('formula') and
            (row.get('expected') is not None or row.get('value') is not None) for row in numeric) if numeric else None)
        resources = data.get('evidence_resources') or {}
        receipt['expected_events'] = args.expect_event
        receipt['expected_events_present'] = all(
            any(row.get('kind') == kind for row in analysis.get('events', [])) for kind in args.expect_event)
        receipt['event_page_evidence_available'] = all(
            row.get('evidence') and all(identifier in resources and (out/resources[identifier]['image']).is_file()
                for identifier in row['evidence']) for row in analysis.get('events', []) if row.get('kind') in args.expect_event)
        receipt['numeric_page_evidence_diagnostic'] = page_evidence_diagnostic(out, numeric, resources)
        receipt['numeric_page_evidence_available'] = (receipt['numeric_page_evidence_diagnostic']['all_available'] if numeric else None)
        receipt['numeric_company_scope_consistent'] = (all(operand.get('company') == data['company'] and
            (not row.get('scope') or operand.get('scope') == row['scope']) for row in numeric for operand in row.get('operands', []))
            if numeric else None)
        tools = receipt['successful_tools']
        receipt['native_financial_chain'] = (bool(tools.get('mj_analyze') and tools.get('mj_get_rule') and tools.get('mj_list_reports'))
            if args.task_type=='analysis' else bool((tools.get('mj_get_facts') or tools.get('mj_get_fact')) and
                (tools.get('mj_verify_bindings') or tools.get('mj_verify')) and tools.get('mj_get_rule')))
        receipt['status'] = 'technical_flow_passed_quality_pending' if (data['model']['status']=='success' and
            receipt['kernel_replay']['status']=='passed' and receipt['agent_replay']['status']=='passed' and
            receipt['native_financial_chain'] and receipt['numeric_evidence_complete'] and
            receipt['numeric_page_evidence_available'] and receipt['numeric_company_scope_consistent'] and
            receipt['expected_events_present'] and receipt['event_page_evidence_available'] and not data['partial']) else 'needs_improvement'
    except BaseException as exc:
        values = credentials.values if credentials is not None else set()
        receipt.update(status='failed', error_type=type(exc).__name__,
                       error=safe_error_message(exc, values), elapsed_s=round(time.monotonic()-started,2))
    finally:
        finished.set()
        receipt['elapsed_s'] = round(time.monotonic()-started, 2)
        if agent is not None:
            receipt['usage'] = {'model_requests': getattr(agent, 'native_request_count', 'unknown'),
                                'tokens': getattr(agent, 'total_tokens', 'unknown')
                                if getattr(agent, 'usage_known', False) else 'unknown'}
            receipt['sessions'] = getattr(agent, 'session_records', [])
            requests = receipt['usage']['model_requests']
            if type(requests) is int and requests > 0 and receipt['model_run']['status'] == 'not_executed':
                receipt['model_run'] = {'status': 'failed', 'reason': 'task_failed_after_native_model_requests'}
        receipt = finish_receipt(receipt, out, before, credentials, filename='source_acceptance.json')
    print(json.dumps({key:receipt.get(key) for key in ('status','summary','model_run','partial','claims','facts','elapsed_s','user_profile_unchanged')},ensure_ascii=False),flush=True)
    return 0 if receipt['status']=='technical_flow_passed_quality_pending' and receipt['user_profile_unchanged'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--state',type=Path,default=Path('../mingjing/runs/native-real-report-freeze-07/agent_state.json'))
    parser.add_argument('--out',type=Path,default=Path('runs/source-real-next-01'))
    parser.add_argument('--task-type',choices=('check','analysis'),default='check')
    parser.add_argument('--expect-event', action='append', choices=EVENT_KINDS, default=[],
                        help='Repeat for a pre-reviewed accounting event required from these development materials')
    parser.add_argument('--corpus',type=Path,help='Optional already-exposed company corpus; never an independent D3 set')
    parser.add_argument('--corpus-addition', type=Path, action='append', default=[],
                        help='Additional frozen official source corpus; does not change the saved user configuration')
    parser.add_argument('--knowledge-root', type=Path,
                        help='Optional host-approved accounting material root; frozen for this task only')
    parser.add_argument('--scope-declaration', choices=('', '合并', '母公司'), default=None,
                        help='Only an explicitly supplied user clarification; never infer it for acceptance')
    parser.add_argument('--model-calls',type=int,default=512)
    parser.add_argument('--tool-calls',type=int,default=4000)
    parser.add_argument('--tokens',type=int,default=2000000)
    parser.add_argument('--followup-tokens', type=int,
                        help='Optional material-conversation budget in this task snapshot; saved settings are unchanged')
    parser.add_argument('--timeout',type=int,default=3600)
    args = parser.parse_args(argv)
    try:
        validate_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    return execute(args)


if __name__=='__main__':
    raise SystemExit(main())
