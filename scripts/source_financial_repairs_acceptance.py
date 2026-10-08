"""Live native development probe against an already-exposed original annual PDF.

The short draft is authored here to exercise repaired features. It is not the
original research report, independent human truth, or a financial-quality score.
Saved provider/model/credentials are consumed without changing their files.
"""
from __future__ import annotations

import argparse
import sys
import copy
import json
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.import_integrity import assert_application_origin
assert_application_origin(ROOT)

from mingjing.agent import FinancialAgent, write_json
from mingjing.agent_replay import replay_agent
from mingjing.compute import display_amount
from mingjing.config import SecretStore, app_root, load_config
from mingjing.evidence import sha256_file
from mingjing.facts import FactStore, fact_from_dict
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.providers import resolve_route
from mingjing.replay import replay_run
from mingjing.source_semantics import freeze_source_dimensions
from scripts.development_receipt import source_lock


def execute(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    profile = app_root()
    saved = {str(path): sha256_file(path) for path in (profile/'config.json', profile/'secrets.json') if path.is_file()}
    source = json.loads(args.exposed_results.read_text(encoding='utf-8'))
    company = source['company']
    selected = [row for row in source['facts'] if row['company'] == company and row['scope'] == '合并']
    adjusted = next(row for row in selected if row['account'] == 'NP_PARENT_ADJ' and row['period'] == '2023Y')
    report = Path(adjusted['source_path']).resolve()
    report_sha = sha256_file(report)
    cfg = copy.deepcopy(load_config(profile/'config.json').to_dict())
    route = resolve_route(cfg)
    secrets = SecretStore(profile)
    if not secrets.get(route['credential_ref']):
        raise ValueError('Current saved route has no readable credential')
    cfg['agent'].update(enabled=True, cross_provider_fallback=False, fallback_routes=[])
    cfg['agent']['budget'].update(max_model_calls=64, max_tool_calls=400, max_total_tokens=1000000, max_runtime_s=600)
    cfg['harness'].update(runtime_mode='embedded', enabled=False, executable='', home='', patch_file='', working_dir='')
    draft = out/'feature-probe.md'
    statements = [
        f'{company}2023年合并口径毛利率较2022年下降2.63个百分点。',
        f'{company}2023年合并口径毛利率较2022年上升10.000000个百分点。',
        f"{company}2023年合并口径扣非归母净利润为{display_amount(Decimal(adjusted['value']), adjusted['display_unit'], adjusted['decimals'])}。",
        f'{company}2023年合并口径营业收入为990000000.00000000元。',
        f'{company}2023年合并口径营业收入同比下降99.00000000%。',
    ]
    draft.write_text('\n'.join(statements), encoding='utf-8')
    request = dict(draft=str(draft), corpus=str(report), company=company, period='2023Y',
                   report_date=source['report_date'], task_type='check', mode='harness')
    locked_source = source_lock()
    receipt = {'status':'running', 'independent_gold':False, 'D3_accessed':False, 'EXE_built':False,
               'draft_kind':'developer_authored_feature_probe_not_original_research_report',
               'provider':route['provider'], 'model':route['model'], 'source_sha256':report_sha,
               'budget_snapshot':cfg['agent']['budget'], 'financial_quality':'not_measured', 'model_cost':'unknown'}
    write_json(out/'acceptance.json', receipt)
    agent = FinancialAgent(request, cfg, secrets.get, out/'run', run_id='financial-repairs-' + out.name)
    started = time.monotonic()
    try:
        data = agent.run()
        receipts = []
        for result in data['results']:
            correction = result.get('correction') or {}
            if result['status'] == 'violated' and correction.get('replacement_text'):
                claims = [freeze_source_dimensions(claim) for claim in extract_mentions(correction['replacement_text'], company)]
                judge = Judge(FactStore([fact_from_dict(row) for row in data['facts']]), data['report_date'])
                results = [judge.verify(claim).to_dict() for claim in claims]
                receipts.append({'claim_id':result['claim_id'], 'replacement_text':correction['replacement_text'],
                                 'claims':len(claims), 'results':results,
                                 'passed':bool(results) and all(row['status']=='verified' for row in results)})
        write_json(out/'correction-rechecks.json', receipts)
        success = [row for row in data['tool_trace'] if row['status']=='success']
        rules = {row['input'].get('rule_id') for row in success if row['tool']=='mj_get_rule'}
        receipt.update(summary=data['summary'], partial=data['partial'], model_run=data['model'],
                       binding_submissions=data['model']['binding_submissions'],
                       kernel_replay=replay_run(agent.out), agent_replay=replay_agent(agent.out),
                       correction_rechecks=receipts, read_rules=sorted(str(rule) for rule in rules),
                       elapsed_s=round(time.monotonic()-started, 2))
        numeric = [row for row in data['results'] if row['status'] in {'verified','violated'}]
        receipt['checks'] = {
            'native_model_success':data['model']['status']=='success',
            'all_five_occurrences_processed':len(data['claims'])==5 and not data['partial'],
            'expected_probe_verdicts':data['summary']=={'verified':2, 'violated':3, 'abstained':0},
            'both_roles_submitted_five':data['model']['binding_submissions']=={'planner':5, 'verifier':5},
            'native_difference_rule_read':'FIN-pp-difference' in rules,
            'all_numeric_evidence':all(row.get('formula') and row.get('operands') and row.get('evidence') for row in numeric),
            'all_original_pdf_page_evidence':all(identifier in data['evidence_resources'] and
                (agent.out/data['evidence_resources'][identifier]['image']).is_file() for row in numeric for identifier in row['evidence']),
            'three_corrections_rechecked':len(receipts)==3 and all(row['passed'] for row in receipts),
            'kernel_replay':receipt['kernel_replay']['status']=='passed',
            'native_replay':receipt['agent_replay']['status']=='passed' and receipt['agent_replay']['network_calls']==0,
        }
    except BaseException as exc:
        receipt.update(status='failed', error=agent.redact(type(exc).__name__ + ': ' + str(exc)))
    finally:
        receipt['source_code_unchanged'] = locked_source == source_lock()
        receipt['original_pdf_unchanged'] = sha256_file(report) == report_sha
        receipt['user_profile_unchanged'] = all(Path(path).is_file() and sha256_file(path)==sha for path,sha in saved.items())
        secret_bytes = [value.encode('utf-8') for provider in cfg.get('providers', {}).values()
                        if (value := secrets.get(provider.get('credential_ref', '')))]
        artifacts = [path for path in out.rglob('*') if path.is_file()
                     and path.suffix in {'.json', '.jsonl', '.md', '.html', '.log', '.yml'}]
        receipt['secret_scan'] = {'files_scanned':len(artifacts),
                                 'plaintext_found':any(secret in path.read_bytes()
                                                      for path in artifacts for secret in secret_bytes)}
        if receipt.get('status') != 'failed':
            receipt['status'] = ('passed_live_feature_probe_quality_pending' if all(receipt['checks'].values())
                                 and receipt['source_code_unchanged'] and receipt['original_pdf_unchanged']
                                 and receipt['user_profile_unchanged'] and not receipt['secret_scan']['plaintext_found']
                                 else 'needs_improvement')
        write_json(out/'acceptance.json', receipt)
    print(json.dumps({key:receipt.get(key) for key in ('status','summary','provider','model','checks','error')}, ensure_ascii=False))
    return 0 if receipt['status']=='passed_live_feature_probe_quality_pending' else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exposed-results', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    return execute(parser.parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
