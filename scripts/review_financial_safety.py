"""Independent, offline financial semantic review; never a release acceptance.

Synthetic adversarial probes use the real dispatcher and kernel, not a mock
Agent or invented benchmark labels. Frozen financial files remain read-only.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mingjing.analysis import analyze_financials
from mingjing.facts import FactStore, fact_from_dict
from mingjing.financial_tools import FinancialDispatcher, ToolDenied
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.scope import source_scope_dimensions


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def dispatch(dispatcher, role, name, params, call_id):
    return dispatcher.dispatch({'session_id': role, 'role': role, 'run_id': 'review',
                                'resource_lock': 'review-lock', 'call_id': call_id,
                                'tool_name': name, 'params': params})


def fixture_fact(account, period, value, evidence):
    return Fact(f'{account}-{period}', '甲公司', account, period, value=Decimal(value),
                publish_date='2024-04-01', evidence=(evidence,), source_doc='synthetic-review',
                nr='扣非' if account == 'NP_PARENT_ADJ' else '未扣非')


def binding_probe(source, facts, account, period):
    claims = [replace(c, table_context={**c.table_context, 'source_scope_required': True})
              for c in extract_mentions(source, '甲公司')]
    claim = claims[0]
    dispatcher = FinancialDispatcher('review', 'review-lock', claims, facts, [], [], '2024-04-22')
    source_result = Judge(dispatcher.store, '2024-04-22').verify(claim).to_dict()
    binding = {'company': '甲公司', 'account': account, 'period': period, 'scope': '合并',
               'relation': 'value', 'base_period': ''}
    try:
        for role in ('planner', 'verifier'):
            dispatcher.register_session(role, role)
            dispatch(dispatcher, role, 'mj_submit_binding', {'mention_id': claim.claim_id,
                                                           'binding': binding}, role + '-bind')
        bound = dispatcher.freeze_bindings()[0]
        result = Judge(dispatcher.store, '2024-04-22', require_dual_binding=True).verify(bound).to_dict()
        return {'source': source, 'source_claim': claim.to_dict(), 'source_result': source_result,
                'submitted_binding': binding, 'agreement': dispatcher.agreements[claim.claim_id],
                'bound_result': result, 'false_verified': result['status'] == 'verified' and
                                                      source_result['status'] != 'verified'}
    except ToolDenied as exc:
        return {'source': source, 'submitted_binding': binding, 'rejected': str(exc), 'false_verified': False}


def readonly_reporter_probe():
    from unittest.mock import patch
    facts = [fixture_fact('REVENUE', '2023Y', '200', 'synthetic:revenue'),
             fixture_fact('COST', '2023Y', '150', 'synthetic:cost')]
    dispatcher = FinancialDispatcher('review', 'review-lock', [], facts, [], [], '2024-04-22')
    dispatcher.register_session('reporter', 'reporter')
    analysis = analyze_financials(dispatcher.store, '甲公司', ['2023Y'], '2024-04-22')
    dispatcher.freeze_analysis(analysis)
    with patch('mingjing.analysis.analyze_financials', side_effect=AssertionError('reporter recomputed analysis')):
        page = dispatch(dispatcher, 'reporter', 'mj_analyze',
                        {'analysis_id': 'financial_summary', 'period': '2023Y', 'metric': 'GROSS_MARGIN'}, 'read-frozen')
    accepted = dispatch(dispatcher, 'reporter', 'mj_submit_report',
                        {'template': page['slot_template'], 'slots': page['slot_ids']}, 'submit-frozen')
    try:
        dispatch(dispatcher, 'reporter', 'mj_submit_report',
                 {'template': '公司净利润999999元', 'slots': []}, 'forged-report')
        free_number_rejected = False
    except ToolDenied:
        free_number_rejected = True
    return {'frozen_analysis_read_without_recomputation': True,
            'frozen_slot_report_accepted': accepted['accepted'],
            'free_number_report_rejected': free_number_rejected,
            'record': page['analysis'][0]['record']}


def frozen_review(run):
    payload = json.loads((run / 'results.json').read_text(encoding='utf-8'))
    facts = [fact_from_dict(item) for item in payload['facts']]
    store = FactStore(facts)
    analysis = analyze_financials(store, payload['company'], sorted({f.period for f in facts}), payload.get('report_date'))
    verified = [m for m in analysis['metrics'] if m['status'] == 'verified']
    source_evidence = {}
    from mingjing.parser import build_evidence
    for path in sorted({f.source_path for f in facts if f.source_path}):
        source = Path(path)
        if source.is_file():
            source_evidence.update({e.evidence_id: e.to_dict() for e in build_evidence(source)})
    wanted = {e for m in verified for e in m['evidence']}
    missing = sorted(wanted - source_evidence.keys())
    reconciliations = []
    for account in ('REVENUE', 'NP_PARENT', 'NP_PARENT_ADJ'):
        for year in sorted({f.period[:4] for f in facts}):
            annual = store.query(payload['company'], account, year + 'Y',
                                 nr='扣非' if account == 'NP_PARENT_ADJ' else '未扣非',
                                 cutoff=payload.get('report_date'))
            quarters = [store.query(payload['company'], account, year + f'Q{q}',
                                    nr='扣非' if account == 'NP_PARENT_ADJ' else '未扣非',
                                    cutoff=payload.get('report_date')) for q in range(1, 5)]
            if annual and all(quarters):
                total = sum((q.value for q in quarters), Decimal(0))
                reconciliations.append({'metric': account, 'year': year, 'annual': str(annual.value),
                                        'quarter_sum': str(total), 'exactly_equal': annual.value == total})
    return {'source_run': run.name, 'source_results_sha256': hashlib.sha256((run / 'results.json').read_bytes()).hexdigest(),
            'frozen_summary': payload.get('summary'),
            'development_recomputed_analysis_metrics': len(analysis['metrics']),
            'development_recomputed_verified_metrics': len(verified),
            'verified_without_operands': [f"{m['period']}:{m['metric']}" for m in verified if not m.get('operands')],
            'verified_without_evidence': [f"{m['period']}:{m['metric']}" for m in verified if not m.get('evidence')],
            'referenced_unique_evidence': len(wanted), 'unresolved_source_evidence_ids': missing,
            'quarter_reconciliations': reconciliations,
            'analysis': analysis,
            'note': 'Current-source recomputation on development facts; not a new Agent run, gold annotation or acceptance.'}


def review(run, out):
    run, out = run.resolve(strict=True), out.resolve()
    if out == run or run in out.parents:
        raise ValueError('Review output cannot modify the frozen source run')
    source = '甲公司2023年合并营业收入100元。'
    facts = [fixture_fact('REVENUE', '2023Y', '200', 'synthetic:revenue'),
             fixture_fact('NP', '2023Y', '100', 'synthetic:profit'),
             fixture_fact('REVENUE', '2022Y', '100', 'synthetic:previous')]
    probes = {'prose_account_relabel': binding_probe(source, facts, 'NP', '2023Y'),
              'prose_period_relabel': binding_probe(source, facts, 'REVENUE', '2022Y')}
    eps_source = '甲公司2023年合并口径稀释每股收益为1.10元。'
    eps_facts = [fixture_fact('EPS_DILUTED', '2023Y', '1.20', 'synthetic:diluted-eps'),
                 fixture_fact('EPS', '2023Y', '1.10', 'synthetic:basic-eps')]
    probes['statutory_diluted_eps_relabel_to_basic_eps'] = binding_probe(eps_source, eps_facts, 'EPS', '2023Y')
    conflicting = '甲公司2023年合并口径营业收入100元，但本段不按合并口径。'
    dimensions = source_scope_dimensions(extract_mentions(conflicting, '甲公司')[0])
    probes['scope_positive_negative_conflict'] = {'source': conflicting, 'source_dimensions': dimensions,
                                                'unique_scope_despite_conflict': bool(dimensions['scope_header'])}
    ratios = extract_mentions('甲公司2023年管理费用率为9.66%。', '甲公司')
    probes['expense_ratio_identity'] = {'accounts': [c.account for c in ratios],
                                       'parse_status': [c.parse_status for c in ratios],
                                       'abstention_codes': [Judge(FactStore([])).verify(c).abstention_code for c in ratios]}
    company_facts = [fixture_fact('REVENUE', '2023Y', '100', 'synthetic:selected-issuer')]
    probes['explicit_foreign_subject_to_selected_issuer'] = binding_probe(
        '乙公司2023年合并营业收入100元。', company_facts, 'REVENUE', '2023Y')
    foreign = probes['explicit_foreign_subject_to_selected_issuer']
    foreign['unsafe_company_substitution'] = foreign.get('bound_result', {}).get('status') == 'verified'
    probes['reporter_readonly_slots'] = readonly_reporter_probe()
    frozen = frozen_review(run)
    findings = [name for name, result in probes.items() if result.get('false_verified') or
                result.get('unique_scope_despite_conflict') or result.get('unsafe_company_substitution')]
    out.mkdir(parents=True, exist_ok=True)
    dump(out / 'semantic_probes.json', probes)
    dump(out / 'frozen_analysis_source_review.json', frozen)
    summary = {'kind': 'independent_development_financial_semantics_review', 'release_acceptance': False,
               'synthetic_probe_findings': findings, 'frozen_source_run': run.name,
               'actual_evidence_misses': len(frozen['unresolved_source_evidence_ids']),
               'actual_verified_metrics_missing_evidence': len(frozen['verified_without_evidence']),
               'reporter_readonly_slots': probes['reporter_readonly_slots']}
    dump(out / 'summary.json', summary)
    (out / 'README.md').write_text(
        '# 独立金融语义复核\n\n本复核不调用 API、不修改冻结运行、不查看 D3、不打包。\n\n'
        'semantic_probes.json 使用明确标注的虚构反例，执行真实 dispatcher 与 Decimal 内核；'
        '不冒充真实模型接入或基准成绩。frozen_analysis_source_review.json 读取开发材料的冻结事实，'
        '检查当前分析内核的操作数、源证据与季度/年度勾稽；不改旧分析结论。\n', encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    summary = review(args.run, args.out)
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps({key: summary[key] for key in ['synthetic_probe_findings', 'actual_evidence_misses',
                     'actual_verified_metrics_missing_evidence']}, ensure_ascii=False))
