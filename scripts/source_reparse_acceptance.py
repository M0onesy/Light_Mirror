"""Real Harness/API check of a deliberately incomplete local fact index.

Uses one already-exposed annual report. This is a supplement workflow check,
not human annotation, a new D3 set, or an extraction-recall benchmark.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path

from mingjing.agent import FinancialAgent, write_json
from mingjing.agent_replay import replay_agent
from mingjing.cli import build_parser, command_check
from mingjing.config import SecretStore, app_root, load_config
from mingjing.evidence import sha256_file
from mingjing.providers import resolve_route
from mingjing.reparse import create_reparse_packet
from mingjing.reparse_inputs import load_parent_context
from mingjing.replay import replay_run


def execute(args):
    seed = json.loads((args.seed / 'results.json').read_text(encoding='utf-8'))
    source = Path(seed['corpus'])
    if source.suffix.lower() != '.pdf' or not source.is_file():
        raise ValueError('Expected the already-exposed single issuer annual PDF')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    saved = app_root()
    profile_before = {str(path): sha256_file(path) for path in (saved/'config.json', saved/'secrets.json') if path.is_file()}
    cfg = load_config(saved/'config.json').to_dict()
    secrets = SecretStore(saved)
    route = resolve_route(cfg)
    if not secrets.get(route['credential_ref']):
        raise ValueError('Saved model route has no readable credential')
    cfg['agent']['enabled'] = True
    cfg['agent']['cross_provider_fallback'] = False
    cfg['agent']['budget'].update(max_model_calls=64, max_tool_calls=256, max_total_tokens=500000, max_runtime_s=1200)
    cfg['harness'].update(runtime_mode='embedded', enabled=False, executable='', home='', patch_file='', working_dir='')
    corpus = out / 'material' / source.parent.parent.name
    corpus.mkdir(parents=True)
    copied = corpus / source.name
    shutil.copy2(source, copied)
    if sha256_file(copied) != sha256_file(source):
        raise ValueError('Annual report copy hash mismatch')
    baseline = [{**row, 'source_path': copied.name} for row in seed['facts'] if row['account'] != 'REVENUE']
    write_json(corpus/'facts.json', baseline)
    intent = out / 'analysis-intent.md'
    intent.write_text('从锁定财报执行登记指标分析，所有金额来自原始财报及确定性内核。', encoding='utf-8')
    parent = out / 'parent-kernel'
    kernel_args = build_parser().parse_args(['check', '--draft', str(intent), '--corpus', str(corpus),
        '--company', seed['company'], '--period', seed['period'], '--report-date', seed['report_date'],
        '--task-type', 'analysis', '--mode', 'kernel', '--run-id', 'local-reparse-parent', '--out', str(parent)])
    if command_check(kernel_args):
        raise ValueError('Parent kernel task failed')
    context = load_parent_context(parent)
    parent_bytes = {path: sha256_file(path) for path in parent.rglob('*') if path.is_file()}
    resource = next(row for row in context['source_resources'] if row['path'] == str(copied))
    packet_root = out / 'reparse' / 'local-reparse-parent'
    proposal = create_reparse_packet(output_root=packet_root,
        block={'id': resource['id']+':page-'+str(args.page), 'report_id': resource['id'], 'page': args.page},
        claim=None, source_resources=context['source_resources'], baseline_facts=context['baseline_facts'],
        company=context['company'], as_of_date=context['as_of_date'], source_generation=context['source_generation'])
    receipt = {'status': 'running', 'scope': 'development_simulated_missing_index_real_pdf_and_real_model',
        'fixture_change': 'REVENUE rows deliberately removed from a private copy of the frozen index',
        'provider': route['provider'], 'model': route['model'], 'pdf_sha256': sha256_file(copied),
        'original_pdf_unchanged': None, 'packet': proposal,
        'human_gold': 'not_executed', 'D3': 'not_executed', 'clean_windows_vm': 'not_executed'}
    write_json(out/'acceptance.json', receipt)
    if proposal['status'] != 'ready':
        receipt.update(status='needs_improvement', reason='window_did_not_produce_nonconflicting_supplement')
        write_json(out/'acceptance.json', receipt)
        return 1
    packet = packet_root / (proposal['packet_id'] + '.json')
    request = {'draft': str(intent), 'corpus': str(corpus), 'company': seed['company'], 'period': seed['period'],
        'report_date': seed['report_date'], 'task_type': 'analysis', 'scope_declaration': '',
        'reparse_parent_run': str(parent), '_controlled_reparse_root': str(packet_root),
        'reparse_packets': [{'packet_id': proposal['packet_id'], 'path': str(packet), 'sha256': sha256_file(packet),
                             'source_generation': context['source_generation']}]}
    task_out = out / 'supplemented-native'
    owner = FinancialAgent(request, cfg, secrets.get, task_out, run_id='source-reparse-'+out.name)
    started = time.monotonic()
    last_update = [0.0]
    def emit(event, **fields):
        if event == 'tool' and time.monotonic()-last_update[0] > 15:
            last_update[0] = time.monotonic()
            print(json.dumps({'stage': 'native_tool', 'tool': fields.get('tool'), 'role': fields.get('role'),
                              'requests': owner.native_request_count}, ensure_ascii=False), flush=True)
    owner.emit = emit
    try:
        data = owner.run()
        before_metrics = context['data']['analysis']['metrics']
        after_metrics = data['analysis']['metrics']
        target = next(row for row in after_metrics if row['metric'] == 'REVENUE_YOY' and row['period'] == '2023Y')
        original_target = next(row for row in before_metrics if row['metric'] == 'REVENUE_YOY' and row['period'] == '2023Y')
        with localcontext() as decimal_context:
            # Match the registered arithmetic precision. Truncating the
            # independent reference to 28 digits falsely rejects a correct
            # 64-digit result; the printed operands still remain unchanged.
            decimal_context.prec = 64
            expected = (Decimal('495671818.88')-Decimal('312776998.45'))/Decimal('312776998.45')
        evidence = target.get('evidence') or []
        resources = data.get('evidence_resources') or {}
        receipt.update(model_run=data['model'], partial=data['partial'], elapsed_s=round(time.monotonic()-started, 2),
            before_metric_statuses=dict(Counter(row['status'] for row in before_metrics)),
            after_metric_statuses=dict(Counter(row['status'] for row in after_metrics)),
            restored_revenue_yoy={'before_status': original_target['status'], 'after_status': target['status'],
                                  'value': target['value'], 'expected_decimal': str(expected), 'evidence': evidence},
            kernel_replay=replay_run(task_out),
            agent_replay=replay_agent(task_out) if data['model']['status'] == 'success' else {'status': 'not_executed'},
            native_tools=dict(Counter(row['tool'] for row in data['tool_trace'] if row['status'] == 'success')),
            original_parent_unchanged=all(path.is_file() and sha256_file(path)==sha for path,sha in parent_bytes.items()),
            original_pdf_unchanged=sha256_file(source)==sha256_file(copied),
            user_profile_unchanged=all(Path(path).is_file() and sha256_file(path)==sha for path,sha in profile_before.items()),
            page_evidence_available=bool(evidence) and all(identifier in resources and (task_out/resources[identifier]['image']).is_file()
                                                        for identifier in evidence))
        passed = (data['model']['status']=='success' and not data['partial'] and
            original_target['status']=='abstained' and target['status']=='verified' and Decimal(target['value'])==expected and
            receipt['kernel_replay']['status']=='passed' and receipt['agent_replay']['status']=='passed' and
            all(receipt[key] for key in ('original_parent_unchanged','original_pdf_unchanged','user_profile_unchanged','page_evidence_available')))
        receipt['status'] = 'technical_workflow_passed_quality_pending' if passed else 'needs_improvement'
    except Exception as exc:
        receipt.update(status='failed', error=owner.redact(type(exc).__name__+': '+str(exc)))
    finally:
        keys = [secret.encode() for provider in cfg.get('providers', {}).values()
                if (secret := secrets.get(provider.get('credential_ref', '')))]
        receipt['plaintext_secret_found'] = any(any(key in path.read_bytes() for key in keys)
            for path in out.rglob('*') if path.is_file() and path.suffix in {'.json', '.jsonl', '.md', '.html', '.log'})
        if receipt['plaintext_secret_found']:
            receipt['status'] = 'failed_secret_scan'
        write_json(out/'acceptance.json', receipt)
    print(json.dumps({key: receipt.get(key) for key in ('status','model_run','before_metric_statuses','after_metric_statuses',
          'restored_revenue_yoy','original_parent_unchanged','user_profile_unchanged')}, ensure_ascii=False), flush=True)
    return 0 if receipt['status']=='technical_workflow_passed_quality_pending' else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=Path, default=Path('runs/source-real-analysis-next-05'))
    parser.add_argument('--out', type=Path, default=Path('runs/source-real-reparse-next-01'))
    parser.add_argument('--page', type=int, default=84)
    parser.add_argument('--review-existing', type=Path,
                        help='Validate an existing frozen supplement run offline without altering its original receipt')
    parser.add_argument('--review-out', type=Path,
                        help='Fresh JSON receipt for offline validation; original files remain read-only')
    args = parser.parse_args()
    if args.review_existing:
        if not args.review_out:
            parser.error('--review-existing requires --review-out')
        return review_existing(args.review_existing, args.review_out)
    return execute(args)


def review_existing(root: Path, destination: Path):
    """Recheck the corrected reference precision using actual frozen artifacts."""
    if destination.exists():
        raise ValueError('Use a fresh review receipt; earlier failures are retained')
    original_path = root/'acceptance.json'
    original = json.loads(original_path.read_text(encoding='utf-8'))
    frozen = root/'supplemented-native'
    protected = [original_path, frozen/'results.json', frozen/'run_manifest.json',
                 frozen/'agent_lock.json', frozen/'agent_model_cache.json']
    protected_hashes = {str(path): sha256_file(path) for path in protected if path.is_file()}
    data = json.loads((frozen/'results.json').read_text(encoding='utf-8'))
    before = json.loads((root/'parent-kernel'/'results.json').read_text(encoding='utf-8'))
    metric = next(row for row in data['analysis']['metrics']
                  if row['metric'] == 'REVENUE_YOY' and row['period'] == '2023Y')
    old = next(row for row in before['analysis']['metrics']
               if row['metric'] == 'REVENUE_YOY' and row['period'] == '2023Y')
    with localcontext() as context:
        context.prec = 64
        expected = (Decimal('495671818.88')-Decimal('312776998.45'))/Decimal('312776998.45')
    # Those printed reference values are independently reviewed development
    # material, not generated facts fed to an Agent or a D3 gold set.
    kernel, native = replay_run(frozen), replay_agent(frozen)
    evidence = metric.get('evidence') or []
    resources = data.get('evidence_resources') or {}
    pages = bool(evidence) and all(identity in resources and
        (frozen/resources[identity]['image']).is_file() for identity in evidence)
    preservation = all(original.get(key) is True for key in
                       ('original_parent_unchanged', 'original_pdf_unchanged', 'user_profile_unchanged'))
    passed = (old['status'] == 'abstained' and metric['status'] == 'verified' and
              Decimal(metric['value']) == expected and pages and preservation and
              data['model']['status'] == 'success' and not data['partial'] and
              kernel['status'] == native['status'] == 'passed')
    receipt = {'status': 'technical_restoration_revalidated_quality_pending' if passed else 'needs_improvement',
               'original_receipt': str(original_path.resolve()), 'original_receipt_sha256': sha256_file(original_path),
               'original_status': original['status'],
               'authoritative_frozen_artifacts_unchanged': all(
                   Path(path).is_file() and sha256_file(path) == expected
                   for path, expected in protected_hashes.items()),
               'replay_receipts_and_history_updated': True,
               'correction': 'Independent reference arithmetic uses 64 digits instead of truncating at 28 digits',
               'before_status': old['status'], 'after_status': metric['status'], 'value': metric['value'],
               'reference_decimal': str(expected), 'reference_precision': 64,
               'page_evidence_available': pages, 'preservation_from_original_receipt': preservation,
               'kernel_replay': kernel, 'agent_replay': native, 'network_calls': 0,
               'scope': 'development_validator_correction_not_independent_financial_gold'}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key:receipt[key] for key in ('status','original_status','reference_precision','network_calls')}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
