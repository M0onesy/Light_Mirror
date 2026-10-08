"""Exercise a real material follow-up with the frozen run's selected route.

Does not modify user settings, build an executable, or claim human gold scores.
"""
from __future__ import annotations

import argparse
import sys
import json
import math
import re
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.import_integrity import assert_application_origin
assert_application_origin(ROOT)

from mingjing.agent import write_json
from mingjing.config import SecretStore, app_root, load_config
from mingjing.evidence import sha256_file
from mingjing.sessions import FinancialSessions
from mingjing.http_transport import safe_error_message
from mingjing.providers import FollowupBudget
from dataclasses import asdict
from scripts.source_web_acceptance import new_output
from scripts.source_acceptance_inputs import (open_credentials, finish_receipt, nonempty_text,
                                             positive_integer, profile_hashes)


def validate_args(args):
    if args.expect not in {'numeric', 'source_quote', 'knowledge_quote'}:
        raise ValueError('expect must be numeric, source_quote or knowledge_quote')
    nonempty_text(args.question, 'question')
    if args.expect == 'knowledge_quote' and args.literal is None:
        raise ValueError('knowledge_quote requires an explicit --literal definition subject')
    if args.literal is not None:
        nonempty_text(args.literal, 'literal', maximum=300)
    elif args.expect == 'source_quote':
        args.literal = '会计政策'
    if not isinstance(args.source_contains, list):
        raise ValueError('source_contains must be a list of nonempty strings')
    for value in args.source_contains:
        nonempty_text(value, 'source_contains')
    if args.expect == 'numeric':
        from mingjing.financial_tools import ANALYSIS_METRICS
        if args.metric not in ANALYSIS_METRICS:
            raise ValueError('metric must be an exact registered financial metric')
        if not isinstance(args.period, str) or not re.fullmatch(r'\d{4}(?:Y|H1|9M|Q[1-4])', args.period):
            raise ValueError('period must be a registered period such as 2023Y or 2023Q1')
    if not isinstance(args.run, Path) or (args.out is not None and not isinstance(args.out, Path)):
        raise ValueError('run and out must be paths')
    return args


def execute(args):
    validate_args(args)
    run = args.run.resolve()
    out = new_output(args.out if args.out else run/'followup-acceptance')
    before, cfg = {}, {}
    credentials = sessions = None
    started = time.monotonic()
    sid = None
    receipt = {'scope': 'development_material_followup_not_independent_gold', 'status': 'running',
               'question': args.question, 'expected_kind': args.expect,
               'expected_metric': args.metric if args.expect == 'numeric' else None,
               'expected_period': args.period if args.expect == 'numeric' else None,
               'session_status': 'not_created', 'usage': 'unknown', 'answer_present': False,
               'message_agent_replays': [], 'message_replay_status': 'not_executed',
               'real_provider_api': 'not_executed', 'clean_windows': 'not_executed'}
    write_json(out/'acceptance.json', receipt)
    try:
        receipt['phase'] = 'read_parent_task'
        profile = app_root()
        before = profile_hashes(profile)
        native_lock = run/'agent_lock.json'
        cfg = (json.loads(native_lock.read_text(encoding='utf-8'))['config'] if native_lock.is_file()
               else load_config(profile/'config.json').to_dict())
        data = json.loads((run/'results.json').read_text(encoding='utf-8'))
        budget = {**asdict(FollowupBudget()), **cfg.get('agent', {}).get('followup_budget', {})}
        for name, value in budget.items():
            positive_integer(value, 'followup_budget.'+name)
        receipt.update(source_run=data['run_id'], source_company=data['company'],
            source_analysis_scope=(data.get('analysis') or {}).get('scope'),
            config_source='frozen_native_task' if native_lock.is_file() else 'user_profile_at_kernel_followup_creation',
            route=cfg['agent']['routing']['default'], budget=budget)

        class FrozenConfig:
            def get(self):
                return json.loads(json.dumps(cfg))

            def secret(self, reference):
                if credentials is None:
                    raise PermissionError('Credentials are unavailable before parent-lock validation')
                return credentials.get(reference)

        receipt['phase'] = 'construct_session_service'
        holder = SimpleNamespace(out=run)
        sessions = FinancialSessions(out/'sessions', lambda rid: holder if rid == data['run_id'] else None, FrozenConfig())
        receipt['phase'] = 'validate_parent_locks'
        sid = sessions.create(data['run_id'])['session_id']
        receipt.update(session_id=sid, session_status=sessions.get(sid)['status'])
        sessions.check_locks(sessions.get(sid))
        receipt['parent_lock_validation'] = 'passed'
        receipt['phase'] = 'resolve_credentials'
        credentials = open_credentials(SecretStore, profile)
        credentials.read_configured(cfg)
        receipt['phase'] = 'execute_material_followup'
        receipt['real_provider_api'] = 'requested'
        sessions.submit(sid, args.question)
        timeout = budget['max_runtime_s'] + 15
        while sid in sessions.inflight:
            if time.monotonic()-started > timeout:
                sessions.cancel(sid)
                raise TimeoutError('Follow-up exceeded its frozen runtime budget')
            worker = sessions.workers.get(sid)
            if worker is not None:
                worker.join(15)
            print(json.dumps({'stage': 'followup', 'elapsed_s': round(time.monotonic()-started),
                              'status': sessions.get(sid)['status']}, ensure_ascii=False), flush=True)
        state = sessions.get(sid)
        receipt.update(session_status=state['status'], usage=state.get('usage', 'unknown'),
                       messages=state.get('messages', []), native_sessions=state.get('native_sessions', []))
        tools = [event for event in state.get('events', []) if event.get('status') == 'success']
        analyzed = [event for event in tools if event.get('tool') == 'mj_analyze']
        conclusions = [item['record'] for event in analyzed for item in event['output'].get('analysis', [])
                       if item['record'].get('status') == 'verified']
        messages = [message for message in state['messages'] if message['role'] == 'assistant']
        for message in messages:
            if message.get('agent_message_manifest_sha256'):
                receipt['message_replay_status'] = 'running'
                receipt['message_agent_replays'].append(sessions.replay_message(sid, message['message_id']))
        receipt['message_replay_passed'] = bool(messages) and len(receipt['message_agent_replays']) == len(messages) and all(
            replay.get('status') == 'passed' and replay.get('real_harness_loop') is True and replay.get('network_calls') == 0
            for replay in receipt['message_agent_replays'])
        receipt['message_replay_status'] = ('passed' if receipt['message_replay_passed'] else
            'failed' if receipt['message_agent_replays'] else 'not_executed')
        requested = [item for item in conclusions if item.get('metric') == args.metric and item.get('period') == args.period]
        usage = state.get('usage') or {}
        budget_ok = all(type(usage.get(field)) in {int, float} and math.isfinite(usage[field]) and
                        0 <= usage[field] <= budget.get(limit, default)
                        for field, limit, default in [('model_requests', 'max_model_calls', 12),
                                                     ('tool_calls', 'max_tool_calls', 80),
                                                     ('tokens', 'max_total_tokens', FollowupBudget().max_total_tokens)])
        receipt.update(session_id=sid, session_status=state['status'], usage=state.get('usage'),
                       budget=budget, within_budget=budget_ok, requested_records=len(requested),
                       successful_tools=[event['tool'] for event in tools],
                       numeric_records=len(conclusions), answer_present=bool(messages),
                       numeric_company_scope_consistent=bool(conclusions) and all(
                           item.get('company') == data['company'] and item.get('scope') == receipt['source_analysis_scope']
                           for item in conclusions),
                       numeric_evidence_complete=bool(conclusions) and all(
                           item.get('operands') and item.get('formula') and item.get('evidence') for item in conclusions))
        if messages:
            (out/'answer.md').write_text(messages[-1]['text'], encoding='utf-8')
            from mingjing.presentation import escape_markdown
            receipt['requested_evidence_in_answer'] = bool(requested) and all(
                escape_markdown(identifier) in messages[-1]['text'] for item in requested for identifier in item.get('evidence', []))
        if args.expect in {'source_quote', 'knowledge_quote'}:
            references = messages[-1].get('source_references', []) if messages else []
            receipt['source_references'] = references
            receipt['source_quote_count'] = len(references)
            receipt['source_quote_labeled'] = bool(messages) and (
                ('外部会计材料原文' in messages[-1]['text'] and '尚未独立核验' in messages[-1]['text']
                 and '不属于签发规则' in messages[-1]['text']) if args.expect == 'knowledge_quote' else
                ('原文引用' in messages[-1]['text'] and '未作数字核查' in messages[-1]['text']))
            receipt['requested_source_section_present'] = bool(messages) and args.literal in messages[-1]['text']
            receipt['source_provenance_complete'] = bool(references) and all(
                row.get('evidence_id') and (row.get('page') or row.get('material_kind') == 'accounting_knowledge') and (row.get('doc_sha256') or row.get('document_sha256')) and
                row.get('quote_sha256') and row.get('block_sha256') for row in references)
            from mingjing.session_evidence import load_source_evidence
            evidence_info = [load_source_evidence(sessions.root, state, messages[-1]['message_id'],
                             row['evidence_id'], cfg) for row in references]
            receipt['source_evidence_verified_against_original'] = bool(evidence_info)
            receipt['requested_source_section_in_original'] = any(args.literal in row['quote_text'] for row in evidence_info)
            # Section names alone can match a table header. Acceptance may
            # require pre-reviewed substantive wording from the original
            # disclosure; compare the verified quotation, not model prose.
            source_text = ''.join(''.join(row['quote_text'].split()) for row in evidence_info)
            required_literals = getattr(args, 'source_contains', None) or []
            receipt['required_source_literals'] = required_literals
            receipt['substantive_source_content_present'] = all(
                ''.join(literal.split()) in source_text for literal in required_literals)
            receipt['source_page_images_available'] = bool(evidence_info) and all(
                row.get('image_available') and Path(row['_image_path']).is_file() for row in evidence_info)
            if args.expect == 'knowledge_quote':
                subject = re.escape(''.join(args.literal.split()))
                receipt['exact_definition_subject_present'] = bool(re.search(
                    r'(?<![\w\u3400-\u9fff])'+subject+r'[，,:：]*(?:是指|指的是|定义为)', source_text))
                receipt['knowledge_reference_guarded'] = bool(evidence_info) and all(
                    row.get('material_kind') == 'accounting_knowledge' and row.get('authorizes_judgement') is False
                    and (row.get('metadata') or {}).get('authority_status') == 'declared_unverified'
                    and row.get('knowledge_lock') for row in evidence_info)
                receipt['knowledge_original_locations_available'] = bool(evidence_info) and all(
                    (row.get('page') is None and not row.get('image_available')) or
                    (row.get('image_available') and Path(row['_image_path']).is_file()) for row in evidence_info)
            content_ok = (receipt['source_quote_labeled'] and receipt['requested_source_section_present'] and
                          receipt['source_provenance_complete'] and receipt['source_evidence_verified_against_original'] and
                          receipt['requested_source_section_in_original'] and
                          (receipt['source_page_images_available'] if args.expect == 'source_quote' else
                           receipt['knowledge_reference_guarded'] and receipt['knowledge_original_locations_available']
                           and receipt['exact_definition_subject_present']) and
                          receipt['substantive_source_content_present'])
        else:
            content_ok = (receipt['numeric_company_scope_consistent'] and receipt['numeric_evidence_complete'] and
                          requested and receipt.get('requested_evidence_in_answer'))
        receipt['status'] = ('technical_followup_passed_quality_pending' if state['status'] == 'ready' and
            receipt['answer_present'] and content_ok and budget_ok and receipt['message_replay_passed'] and
            any(event['tool'] == 'mj_submit_report' for event in tools) else 'needs_improvement')
    except Exception as exc:
        values = credentials.values if credentials is not None else set()
        receipt.update(status='failed', error_type=type(exc).__name__, error=safe_error_message(exc, values))
        if receipt.get('message_replay_status') == 'running':
            receipt['message_replay_status'] = 'failed'
    finally:
        if sessions is not None:
            try:
                sessions.cancel_running()
                receipt['workers_stopped'] = sessions.wait_running(3)
                if sid is not None:
                    state = sessions.get(sid)
                    receipt.update(session_status=state['status'], usage=state.get('usage', 'unknown'),
                                   messages=state.get('messages', []))
                    usage = state.get('usage')
                    requests = usage.get('model_requests') if isinstance(usage, dict) else None
                    if receipt['phase'] == 'execute_material_followup' and type(requests) in {int, float} and math.isfinite(requests):
                        receipt['real_provider_api'] = True if requests > 0 else 'not_executed'
                    elif receipt['real_provider_api'] == 'requested':
                        receipt['real_provider_api'] = 'unknown'
            except Exception as exc:
                receipt.update(status='failed', cleanup_error=safe_error_message(
                    exc, credentials.values if credentials is not None else set()))
        receipt['elapsed_s'] = round(time.monotonic()-started, 2)
        receipt = finish_receipt(receipt, out, before, credentials, filename='acceptance.json')
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return 0 if receipt['status'] == 'technical_followup_passed_quality_pending' else 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True, help='New frozen native financial-analysis run')
    parser.add_argument('--out', type=Path, help='Fresh acceptance artifact directory; defaults to a subdirectory of the source run')
    parser.add_argument('--question', default='请核查并行科技2023年营业收入同比，列出本期、基期、计算式及原始证据。')
    parser.add_argument('--metric', default='REVENUE_YOY', help='Exact registered metric required for this acceptance question')
    parser.add_argument('--period', default='2023Y', help='Exact current period required for this acceptance question')
    parser.add_argument('--expect', choices=('numeric', 'source_quote', 'knowledge_quote'), default='numeric')
    parser.add_argument('--literal', help='Source-section words; knowledge_quote requires an explicit definition subject')
    parser.add_argument('--source-contains', action='append', default=[],
                        help='Repeat for substantive pre-reviewed wording required in verified source quotations')
    args = parser.parse_args(argv)
    try:
        validate_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    return execute(args)


if __name__ == '__main__':
    raise SystemExit(main())
