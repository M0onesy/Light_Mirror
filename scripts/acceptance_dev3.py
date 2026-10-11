"""Real, exposed development report through the upgraded native execution path.

The saved user profile is read-only. All materials, task state and receipts live
under the explicitly selected acceptance directory; no credential is exported.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.agent import FinancialAgent, write_json
from mingjing.config import SecretStore, app_root, load_config
from mingjing.evidence import sha256_file
from mingjing.http_transport import safe_error_message
from mingjing.import_integrity import assert_application_origin
from mingjing.providers import resolve_route
from mingjing.replay import replay_run
from mingjing.smart.kb import KnowledgeBase
from mingjing.smart.native import SmartRunAdapter
from mingjing.smart.pipeline import native_payload
from mingjing.smart.render import write_outputs
from scripts.source_acceptance_inputs import open_credentials, profile_hashes, profile_unchanged


def acceptance_statistics(payload):
    """Count every source occurrence; never narrow the historical denominator."""
    from mingjing.smart.common import period_end

    items = payload['items']
    cutoff = payload.get('report', {}).get('report_date') or payload.get('report_date') or ''

    def inferred(item):
        end = period_end(item.get('period', ''))
        return (item.get('forecast') is True or item.get('modality') == 'inference'
                or bool(cutoff and end and end > cutoff)
                or (item['category'] == 'forecast' and not any(
                    key in item for key in ('forecast', 'modality', 'period'))))

    def historical(item):
        return not inferred(item)

    def entity(item):
        return item.get('entity') or {'external': 'external', 'market': 'market'}.get(item['category'], 'company')

    def company_history(item):
        return (historical(item) and entity(item) in {'company', 'segment'}
                and item['category'] not in {'market', 'template'})

    def unprocessed(item):
        return (item.get('execution_status') != 'judged'
                or item.get('status') not in {'verified', 'violated', 'unverifiable', 'skipped'})

    pending = payload.get('execution_statistics', {}).get('pending_ledger_mentions')
    pending_known = isinstance(pending, int) and not isinstance(pending, bool) and pending >= 0
    model_pending = sum(unprocessed(item) for item in items)
    complete = (pending_known and pending == 0 and model_pending == 0
                and not payload.get('partial', False) and not payload.get('coverage_ledger', {}).get('partial', False))

    def count(selected, *, target=False):
        unresolved = [item for item in selected if item['status'] == 'unverifiable' or unprocessed(item)]
        assertions = {}
        for item in selected:
            assertions.setdefault(item.get('assertion_id') or item['cid'], []).append(item)
        states = dict(Counter(item['status'] for item in selected))
        eligible = sum(item['status'] != 'skipped' or unprocessed(item) for item in selected)
        result = {
            'occurrences': len(selected),
            'assertions': len(assertions), 'states': states, 'statuses': states,
            'assertion_statuses': dict(Counter(
                next(iter(statuses)) if len(statuses) == 1 else 'mixed'
                for rows in assertions.values()
                for statuses in [{('unprocessed' if unprocessed(item) else item['status']) for item in rows}])),
            'judged': sum(item['status'] in {'verified', 'violated'} and not unprocessed(item) for item in selected),
            'eligible': eligible, 'unresolved': len(unresolved),
            'unresolved_ids': [item['cid'] for item in unresolved],
            'unresolved_assertions': sum(any(item['status'] == 'unverifiable' or unprocessed(item) for item in rows)
                                         for rows in assertions.values()),
            'pending': sum(unprocessed(item) for item in selected),
            'abstention_rate': sum(item['status'] == 'unverifiable' and not unprocessed(item) for item in selected) / eligible if eligible else None,
            'unresolved_rate': len(unresolved) / eligible if eligible else None,
        }
        if target:
            assessed = complete and bool(eligible)
            result.update(target_at_most_9=len(unresolved) <= 9 if assessed else None,
                          target_assessment=('assessed_development_scope' if assessed else
                                             'not_assessed_empty_scope' if complete else 'not_assessed_incomplete_ledger'))
        return result

    groups = {category: count([item for item in items if item['category'] == category])
              for category in sorted({item['category'] for item in items})}
    views = {
        'core': count([item for item in items if historical(item) and item['category'] == 'core'], target=True),
        'historical_financial': count([item for item in items if historical(item)
                                       and item['category'] in {'core', 'open_metric'}], target=True),
        'company_historical_acceptance': count([item for item in items if company_history(item)], target=True),
        'company_business_operating_and_unbound': count([item for item in items if company_history(item)
                                                        and item['category'] not in {'core', 'open_metric'}]),
        'industry_external': count([item for item in items if entity(item) == 'external']),
        'forecast': count([item for item in items if inferred(item)]),
        'market_inputs': count([item for item in items if item['category'] == 'market']),
        'template': count([item for item in items if item['category'] == 'template']),
    }
    views['historical_financial']['categories'] = ['core', 'open_metric']
    return {
        'schema': 'mingjing.development-acceptance-statistics.v2',
        'unit': 'source_occurrence', 'groups': groups, 'scope_views': views, **views,
        'scope_rules': {'report_date': cutoff,
                        'historical': 'not forecast, not inference modality, period end no later than report date',
                        'company_historical_acceptance': 'company/segment historical items except market/template; includes core/open/other/operational/unbound',
                        'forecast': 'forecast flag or inference modality or future period end; can overlap external scope',
                        'target_gate': 'requires complete ledger and every modeled item judged; empty scope is not assessed'},
        'unmodeled_ledger_mentions': pending, 'modeled_pending_occurrences': model_pending,
        'ledger_complete': complete,
        'recall': 'unknown_requires_independent_gold',
        'formal_quality_status': 'not_established_by_exposed_development_material',
    }


def execute(args):
    assert_application_origin(ROOT)
    out = args.out.resolve()
    if out.exists():
        raise ValueError('Acceptance output must be a new directory')
    out.mkdir(parents=True)
    before = profile_hashes(app_root())
    credentials = open_credentials(SecretStore, app_root())
    cfg = load_config(app_root() / 'config.json').to_dict()
    route = resolve_route(cfg)
    cfg['agent']['enabled'] = True
    cfg['agent']['cross_provider_fallback'] = False
    cfg['agent']['budget'].update(max_model_calls=512, max_tool_calls=4000, max_total_tokens=2000000,
                                 max_runtime_s=3600, binding_group_size=16)
    cfg['harness'].update(runtime_mode='embedded', enabled=False, executable='', home='', patch_file='', working_dir='')
    cfg['workspace']['knowledge_root'] = ''
    receipt = {'schema': 'mingjing-dev3-real-acceptance.v1', 'status': 'running',
               'scope': 'exposed_development_material_not_independent_gold',
               'route': {k: route[k] for k in ('provider', 'model', 'protocol')},
               'budget': cfg['agent']['budget'], 'independent_gates': {'human_gold': 'not_executed', 'D3': 'not_executed', 'Windows_clean_machine': 'not_executed', 'Kimi': 'not_executed', 'GPT': 'not_executed'}}
    finished = threading.Event()
    started = time.monotonic()
    agent = None
    try:
        kb = KnowledgeBase(out / 'knowledge-base')
        report = args.report.resolve()
        receipt['report_origin'] = {'path': str(report), 'sha256': sha256_file(report)}
        if args.pages:
            import fitz
            selected = [int(p) for p in args.pages.split(',')]
            with fitz.open(report) as original, fitz.open() as sample:
                if any(p < 1 or p > len(original) for p in selected) or len(set(selected)) != len(selected):
                    raise ValueError('Invalid physical page selection')
                for p in selected:
                    sample.insert_pdf(original, from_page=p - 1, to_page=p - 1)
                report = out / '真实研报小样本.pdf'
                sample.save(report)
            receipt['report_origin']['physical_pages'] = selected
            receipt['report_origin']['sample_sha256'] = sha256_file(report)
        anchor = kb.add_file(report, origin='acceptance_exposed_report', hints={
            'company': '并行科技', 'stock_code': '839493', 'doc_type': 'research_report', 'report_date': '2024-04-22'})
        material_records = []
        if args.material_index:
            with args.material_index.open(encoding='utf-8-sig', newline='') as stream:
                records = list(csv.DictReader(stream))
            records = [r for r in records if r['date'] <= '2024-04-22' and ('年报' in r['category'] or '招股书' in r['category'])]
            # Use one latest official version per annual period / prospectus.
            latest = {}
            for r in sorted(records, key=lambda r: r['date']):
                year = re.search(r'(20\d{2})年年度报告', r['title'])
                key = year[1] if year else 'prospectus'
                latest[key] = r
            for r in latest.values():
                source = args.material_index.parent / r['file']
                if sha256_file(source) != r['sha256']:
                    raise ValueError('Official material SHA-256 differs from its acquisition record')
                year = re.search(r'(20\d{2})年年度报告', r['title'])
                meta = {'company': '并行科技', 'stock_code': r['code'], 'report_date': r['date'],
                        'period': year[1] + 'Y' if year else '', 'doc_type': 'annual_report' if year else 'prospectus'}
                material_records.append(kb.add_file(source, origin=r['url'], hints=meta))
        else:
            material_records.append(kb.add_file(args.annual, origin='official_annual_exposed', hints={
                'company': '并行科技', 'stock_code': '839493', 'period': '2023Y', 'report_date': '2024-04-15', 'doc_type': 'annual_report'}))
        adapter = SmartRunAdapter(None, kb, out)
        adapter.freeze(material_records, out / 'materials')
        adapter.freeze([anchor], out / 'draft')
        request = {'draft': str(out / 'draft' / (anchor['doc_id'] + anchor['ext'])), 'corpus': str(out / 'materials'),
                   'company': '并行科技', 'period': '2023Y', 'report_date': '2024-04-22', 'task_type': 'check', 'mode': 'harness',
                   'material_profile': 'smart', 'validation_policy': 'standard', 'smart_options': {'use_llm': not args.kernel},
                   'smart_draft_metadata': {k: anchor[k] for k in __import__('mingjing.smart.detect', fromlist=['DocMeta']).DocMeta().to_dict()}}
        receipt['inputs'] = [{'name': r['name'], 'sha256': r['doc_id'], 'date': r['report_date']} for r in material_records]
        receipt['phase'] = 'native_financial_task'
        write_json(out / 'acceptance.json', receipt)
        agent = FinancialAgent(request, cfg, credentials.get, out / 'native-run', run_id='dev3-' + out.name)

        def heartbeat():
            while not finished.wait(25):
                print(json.dumps({'phase': 'native_financial_task', 'seconds': round(time.monotonic() - started),
                    'model_requests': agent.native_request_count, 'tokens': agent.total_tokens if agent.usage_known else 'unknown',
                    'A': len(agent.dispatcher.bindings['planner']) if hasattr(agent, 'dispatcher') else 0,
                    'B': len(agent.dispatcher.bindings['verifier']) if hasattr(agent, 'dispatcher') else 0}, ensure_ascii=False), flush=True)
        threading.Thread(target=heartbeat, daemon=True).start()
        data = agent.run()
        payload = native_payload(data, {'run_id': agent.run_id, 'report': anchor, 'model': agent.smart_model,
                                       'facts': agent.facts, 'materials': material_records, 'options': {'strict': False},
                                       'native_run_id': agent.run_id, 'canonical_run_path': str(agent.out), 'execution': data['execution']})
        write_outputs(payload, out / 'report')
        receipt.update(model=data['model'], summary=data['summary'], smart_summary=payload['summary'], partial=data['partial'],
                       claims=len(data['claims']), facts=len(data['facts']),
                       statistics=acceptance_statistics(payload), execution_statistics=payload['execution_statistics'],
                       tools=dict(Counter(t['tool'] for t in data['tool_trace'] if t['status'] == 'success')),
                       kernel_replay=replay_run(agent.out), source_version='1.0.0.dev3')
        if data['model']['status'] == 'success':
            from mingjing.agent_replay import replay_agent
            receipt['agent_replay'] = replay_agent(agent.out)
        else:
            receipt['agent_replay'] = {'status': 'not_executed', 'reason': 'original_not_native_success'}
        replay_ok = (args.kernel or receipt['agent_replay']['status'] == 'passed')
        receipt['status'] = ('passed_development_chain' if data['model']['status'] in {'success', 'disabled'}
                             and receipt['kernel_replay']['status'] == 'passed' and replay_ok else 'requires_improvement')
    except Exception as exc:
        receipt.update(status='failed', error=safe_error_message(exc, credentials.values))
        print(json.dumps({'status': 'failed', 'error': receipt['error']}, ensure_ascii=False), flush=True)
    finally:
        finished.set()
        if agent and agent.runtime:
            agent.runtime.close()
        receipt.update(user_profile_unchanged=profile_unchanged(before), elapsed_s=round(time.monotonic() - started, 2))
        if not receipt['user_profile_unchanged']:
            receipt['status'] = 'failed_user_profile_modified'
        write_json(out / 'acceptance.json', receipt)
    print(json.dumps({k: receipt.get(k) for k in ('status', 'route', 'summary', 'partial', 'elapsed_s', 'user_profile_unchanged')}, ensure_ascii=False), flush=True)
    return 0 if receipt['status'] == 'passed_development_chain' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--annual', type=Path)
    parser.add_argument('--material-index', type=Path)
    parser.add_argument('--pages', default='')
    parser.add_argument('--kernel', action='store_true')
    raise SystemExit(execute(parser.parse_args()))
