"""Offline developer review of source-bound growth and frozen analysis.

Synthetic probes are explicitly synthetic; frozen source runs remain read-only.
This is neither independent human gold nor final release acceptance.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from collections import Counter
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mingjing.analysis import analyze_financials
from mingjing.facts import FactStore
from mingjing.models import Fact
from mingjing.mentions import previous_period


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fixture(identifier, account, period, value, *, scope='合并', source='current-report',
            path='', published='2025-03-01', confidence='1') -> Fact:
    return Fact(identifier, '甲公司', account, period, scope=scope,
                value=Decimal(value), source_doc=source, source_path=path,
                publish_date=published, parse_conf=confidence,
                evidence=(f'synthetic:{identifier}',))


def growth(facts, *, scope='合并', date='2025-03-31', period='2024Y') -> dict:
    result = analyze_financials(FactStore(facts), '甲公司', [period], date, scope)
    return next(item for item in result['metrics'] if item['metric'] == 'REVENUE_YOY')


def synthetic_review() -> dict:
    current = fixture('current', 'REVENUE', '2024Y', '120')
    base = fixture('base', 'REVENUE', '2023Y', '100')
    probes = []

    def record(name, row, expected_status, expected_value=None, expected_code=None):
        failures = []
        if row['status'] != expected_status:
            failures.append('unexpected_status')
        if row['value'] != expected_value:
            failures.append('unexpected_value')
        if expected_code is not None and row['abstention_code'] != expected_code:
            failures.append('unexpected_abstention_code')
        probes.append({'name': name, 'expected_status': expected_status,
                       'expected_value': expected_value, 'expected_abstention_code': expected_code,
                       'failures': failures, 'actual': row})

    scoped = [current, base,
              fixture('parent-current', 'REVENUE', '2024Y', '500', scope='母公司'),
              fixture('parent-base', 'REVENUE', '2023Y', '200', scope='母公司')]
    record('consolidated_scope_isolated', growth(scoped), 'verified', '0.2')
    record('parent_scope_isolated', growth(scoped, scope='母公司'), 'verified', '1.5')
    record('older_original_does_not_override_same_report_comparative',
           growth([current, base, fixture('old', 'REVENUE', '2023Y', '60',
                                         source='older-report', published='2024-03-01')]),
           'verified', '0.2')
    record('future_current_not_visible',
           growth([replace(current, publish_date='2025-04-01'), base]),
           'abstained', expected_code='X1')
    record('future_comparative_not_visible',
           growth([current, replace(base, publish_date='2025-04-01')]),
           'abstained', expected_code='X1')
    record('unknown_disclosure_date_not_visible',
           growth([current, replace(base, publish_date='')]),
           'abstained', expected_code='X4')
    record('source_path_fallback_remains_same_document',
           growth([replace(current, source_doc='', source_path='synthetic/report.pdf'),
                   replace(base, source_doc='', source_path='synthetic/report.pdf')]),
           'verified', '0.2')
    record('source_identity_missing_does_not_borrow_base',
           growth([replace(current, source_doc='', source_path=''), base]),
           'abstained', expected_code='X4')
    record('cross_document_base_not_borrowed',
           growth([current, replace(base, source_doc='different-report')]),
           'abstained', expected_code='X1')
    for period in ('2024Y', '2024H1', '2024H2', '2024Q1', '2024Q2', '2024Q3', '2024Q4', '20249M'):
        record('period_type_' + period, growth([
            replace(current, period=period), replace(base, period=previous_period(period, 'yoy'))],
            period=period), 'verified', '0.2')
    for value in ('0', '-100'):
        record('nonpositive_base_' + value, growth([current, replace(base, value=Decimal(value))]),
               'abstained', expected_code='X2')
    record('missing_report_date', growth([current, base], date=None),
           'abstained', expected_code='X5')
    record('same_version_conflict_is_X4',
           growth([current, base, replace(base, fact_id='conflicting', value=Decimal('80'),
                                         evidence=('synthetic:conflicting',))]),
           'abstained', expected_code='X4')
    record('low_confidence_comparative_is_X4',
           growth([current, replace(base, parse_conf='0.80')]),
           'abstained', expected_code='X4')
    return {'kind': 'synthetic_real_kernel_development_probes', 'count': len(probes),
            'findings': [p['name'] for p in probes if p['failures']], 'probes': probes}


DIRECT = {'REVENUE', 'COST', 'NP', 'NP_PARENT', 'NP_PARENT_ADJ', 'CFO', 'ASSETS',
          'LIABILITIES', 'EQUITY_PARENT', 'EPS', 'EPS_DILUTED', 'ROE'}
FORMULAE = {'GROSS_MARGIN': (('REVENUE', 'COST'), '(REVENUE-COST)/REVENUE'),
            'NET_MARGIN': (('NP', 'REVENUE'), 'NP/REVENUE'),
            'DEBT_RATIO': (('LIABILITIES', 'ASSETS'), 'LIABILITIES/ASSETS'),
            'CASH_CONTENT': (('CFO', 'NP'), 'CFO/NP'),
            'NP_PARENT_RECOMPUTED': (('NP_PARENT', 'NRI_TOTAL', 'NRI_TAX_EFFECT', 'NRI_MINORITY'),
                                     'NP_PARENT-(NRI_TOTAL-NRI_TAX_EFFECT-NRI_MINORITY)'),
            'NON_RECURRING_SHARE': (('NP_PARENT', 'NRI_TOTAL', 'NRI_TAX_EFFECT', 'NRI_MINORITY'),
                                     '(NP_PARENT-NP_PARENT_RECOMPUTED)/NP_PARENT')}
SOURCE_MULTIPLIERS = {'元': Decimal('1'), '万元': Decimal('10000'), '百万元': Decimal('1000000'),
                      '亿元': Decimal('100000000'), '%': Decimal('0.01'), '％': Decimal('0.01'),
                      '百分点': Decimal('0.01'), '': Decimal('1')}
SOURCE_NUMBER = re.compile(r'[+\-]?\d+(?:\.\d+)?')


def shared_document(operands) -> bool:
    identities = [{f[key] for key in ('source_doc', 'source_path') if f.get(key)} for f in operands]
    return bool(identities and set.intersection(*identities))


def operand_source_review(fact: dict, available_evidence: dict) -> dict:
    spans = [available_evidence[e] for e in fact.get('evidence', []) if e in available_evidence]
    unit = fact.get('display_unit', '元')
    multiplier = SOURCE_MULTIPLIERS.get(unit)
    source_number = Decimal(fact['value']) / multiplier if multiplier is not None else None
    matched = []
    for span in spans:
        text = span['text'].replace(',', '').replace('，', '').replace('−', '-').replace('－', '-')
        for match in SOURCE_NUMBER.finditer(text):
            if source_number is not None and Decimal(match[0]) == source_number:
                matched.append({'evidence_id': span['evidence_id'], 'page': span['page'],
                                'source_token': match[0],
                                'context': text[max(0, match.start() - 100):match.end() + 100]})
    compact = ''.join(re.sub(r'\s+', '', span['text']) for span in spans)
    label = re.sub(r'\s+', '', fact.get('label', ''))
    return {'fact_id': fact['fact_id'], 'account': fact['account'], 'period': fact['period'],
            'canonical_value': fact['value'], 'display_unit': unit,
            'source_number_expected': str(source_number) if source_number is not None else None,
            'source_number_present_in_original_pdf_spans': bool(matched),
            'source_label': fact.get('label'), 'source_label_present': bool(label and label in compact),
            'table_label': fact.get('table_label'), 'column_label': fact.get('column_label'),
            'matched_spans': matched,
            'note': 'Numeric token is checked against original PDF spans; unit/column heading context remains visible for technical review.'}


def verify_metric(item: dict, analysis: dict, facts: dict, available_evidence: dict) -> dict:
    failures = []
    operands = item.get('operands', [])
    metric = item['metric']
    values = [Decimal(f['value']) for f in operands]
    if not operands:
        failures.append('verified_without_operands')
    if not item.get('evidence'):
        failures.append('verified_without_evidence')
    expected_evidence = list(dict.fromkeys(e for f in operands for e in f.get('evidence', [])))
    if item.get('evidence') != expected_evidence:
        failures.append('evidence_not_operand_union')
    if item.get('company') != analysis.get('company') or item.get('scope') != analysis.get('scope'):
        failures.append('metric_company_or_scope_mismatch')
    for f in operands:
        if f.get('company') != analysis.get('company') or f.get('scope') != analysis.get('scope'):
            failures.append('operand_company_or_scope_mismatch')
        try:
            visible = date.fromisoformat(f.get('publish_date', '')) <= date.fromisoformat(analysis.get('report_date') or '')
        except ValueError:
            visible = False
        if not visible:
            failures.append('undated_or_future_operand')
        if f.get('nr') != ('扣非' if f.get('account') == 'NP_PARENT_ADJ' else '未扣非'):
            failures.append('operand_nonrecurring_scope_mismatch')
        if f['fact_id'] not in facts or facts[f['fact_id']] != f:
            failures.append('operand_not_identical_to_frozen_fact')
    ratio = metric.endswith('_YOY') or metric in {'ROE', 'GROSS_MARGIN', 'NET_MARGIN', 'DEBT_RATIO',
                                                 'CASH_CONTENT', 'NON_RECURRING_SHARE'}
    expected_unit = '比例' if ratio else '元/股' if metric in {'EPS', 'EPS_DILUTED'} else '元'
    if item.get('unit') != expected_unit:
        failures.append('metric_unit_mismatch')
    expected = None
    expected_accounts = ()
    expected_formula = None
    if metric in DIRECT and values:
        expected, expected_accounts, expected_formula = values[0], (metric,), '原报告披露值'
    elif metric.endswith('_YOY') and len(values) == 2:
        account = metric.removesuffix('_YOY')
        expected_accounts, expected_formula = (account, account), '(current-base)/base'
        if values[1] <= 0:
            failures.append('verified_nonpositive_growth_base')
        else:
            expected = (values[0] - values[1]) / values[1]
        if operands[0]['period'] != item['period'] or operands[1]['period'] != previous_period(item['period'], 'yoy'):
            failures.append('growth_period_or_period_type_mismatch')
        if item.get('base_period') != previous_period(item['period'], 'yoy') or item.get('rule_id') != 'E5-yoy':
            failures.append('growth_rule_or_base_mismatch')
    elif metric in FORMULAE:
        expected_accounts, expected_formula = FORMULAE[metric]
        if len(values) >= len(expected_accounts):
            if metric == 'NP_PARENT_RECOMPUTED':
                expected = values[0] - (values[1] - values[2] - values[3])
            elif metric == 'NON_RECURRING_SHARE' and values[0] > 0:
                expected = (values[1] - values[2] - values[3]) / values[0]
            elif metric == 'GROSS_MARGIN' and values[0] > 0:
                expected = (values[0] - values[1]) / values[0]
            elif metric not in {'GROSS_MARGIN', 'NON_RECURRING_SHARE'} and values[1] > 0:
                expected = values[0] / values[1]
    else:
        failures.append('unregistered_verified_metric')
    core_operands = operands[:len(expected_accounts)]
    if tuple(f['account'] for f in core_operands) != expected_accounts:
        failures.append('operand_account_mismatch')
    if len(operands) > len(expected_accounts) and not (
            metric == 'NP_PARENT_RECOMPUTED' and len(operands) == 5 and operands[-1]['account'] == 'NP_PARENT_ADJ'):
        failures.append('unexpected_additional_operands')
    if item.get('formula') != expected_formula:
        failures.append('formula_text_mismatch')
    if not metric.endswith('_YOY') and any(f['period'] != item['period'] for f in operands):
        failures.append('operand_period_mismatch')
    if len(operands) > 1 and not shared_document(operands):
        failures.append('derived_operands_not_same_report')
    if expected is None or Decimal(item['value']) != expected:
        failures.append('decimal_recalculation_mismatch')
    if metric == 'NP_PARENT_RECOMPUTED' and len(operands) == 5 and expected is not None:
        disclosed = operands[-1]
        scale = SOURCE_MULTIPLIERS.get(disclosed.get('display_unit', '元'))
        if scale is None or item.get('disclosed_value') != disclosed['value']:
            failures.append('deduction_disclosure_metadata_mismatch')
        elif item.get('match_disclosure') != (abs(expected - values[-1]) <=
                scale * Decimal('0.5') * Decimal(10) ** -disclosed['decimals']):
            failures.append('deduction_disclosure_rounding_mismatch')
    unresolved = [e for e in item.get('evidence', []) if e not in available_evidence]
    if unresolved:
        failures.append('evidence_not_recovered_from_real_document')
    evidence_pages = [{'id': e, 'page': available_evidence[e]['page'],
                       'document_sha256': available_evidence[e]['document_sha256'],
                       'bbox': available_evidence[e]['bbox']}
                      for e in item.get('evidence', []) if e in available_evidence]
    source_checks = [operand_source_review(f, available_evidence) for f in operands]
    if any(not check['source_number_present_in_original_pdf_spans'] for check in source_checks):
        failures.append('operand_value_not_found_in_original_pdf_spans')
    return {'period': item['period'], 'metric': metric, 'value': item['value'],
            'unit': item.get('unit'), 'formula': item.get('formula'),
            'operand_ids': [f['fact_id'] for f in operands],
            'recomputed_value': str(expected) if expected is not None else None,
            'failures': sorted(set(failures)), 'unresolved_evidence': unresolved,
            'evidence_pages': evidence_pages, 'operand_source_checks': source_checks}


def frozen_review(run: Path) -> dict:
    results_file = run / 'results.json'
    before_hash = hashlib.sha256(results_file.read_bytes()).hexdigest()
    payload = json.loads(results_file.read_text(encoding='utf-8'))
    analysis = payload.get('analysis')
    if not isinstance(analysis, dict):
        raise ValueError('Frozen results do not contain financial analysis')
    facts = {f['fact_id']: f for f in payload['facts']}
    from mingjing.parser import build_evidence
    available_evidence, unavailable_documents = {}, []
    for source in sorted({f['source_path'] for f in payload['facts'] if f.get('source_path')}):
        path = Path(source)
        if not path.is_file():
            unavailable_documents.append(source)
            continue
        available_evidence.update({e.evidence_id: e.to_dict() for e in build_evidence(path)})
    checks = [verify_metric(item, analysis, facts, available_evidence)
              for item in analysis.get('metrics', []) if item['status'] == 'verified']
    after_hash = hashlib.sha256(results_file.read_bytes()).hexdigest()
    return {'kind': 'frozen_real_document_development_review', 'source_run': run.name,
            'source_results_sha256': before_hash, 'source_file_unchanged': before_hash == after_hash,
            'analysis_schema': analysis.get('schema_version'), 'company': analysis.get('company'),
            'scope': analysis.get('scope'), 'report_date': analysis.get('report_date'),
            'total_metrics': len(analysis.get('metrics', [])),
            'metric_status_counts': dict(Counter(item['status'] for item in analysis.get('metrics', []))),
            'abstention_code_counts': dict(Counter(item.get('abstention_code') for item in
                analysis.get('metrics', []) if item['status'] == 'abstained')),
            'verified_metrics_reviewed': len(checks),
            'verified_growth_reviewed': sum(c['metric'].endswith('_YOY') for c in checks),
            'unique_operand_facts_reviewed': len({fid for check in checks for fid in check['operand_ids']}),
            'unique_evidence_ids_reviewed': len({e['id'] for check in checks for e in check['evidence_pages']}),
            'pdf_pages_reviewed': sorted({e['page'] for check in checks for e in check['evidence_pages']}),
            'metric_findings': [f"{c['period']}:{c['metric']}" for c in checks if c['failures']],
            'unavailable_documents': unavailable_documents, 'checks': checks,
            'note': 'Developer technical source review; not independent human gold or final release acceptance.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    run = args.run.resolve(strict=True) if args.run else None
    if run and (out == run or run in out.parents):
        raise ValueError('Review output cannot modify the frozen source run')
    out.mkdir(parents=True, exist_ok=True)
    synthetic = synthetic_review()
    dump(out / 'synthetic_growth_probes.json', synthetic)
    actual = frozen_review(run) if run else None
    if actual:
        dump(out / 'frozen_analysis_review.json', actual)
    summary = {'release_acceptance': False, 'synthetic_probes': synthetic['count'],
               'synthetic_findings': synthetic['findings'],
               'frozen_run': run.name if run else None,
               'actual_verified_metrics_reviewed': actual['verified_metrics_reviewed'] if actual else None,
               'actual_verified_growth_reviewed': actual['verified_growth_reviewed'] if actual else None,
               'actual_metric_findings': actual['metric_findings'] if actual else None}
    dump(out / 'summary.json', summary)
    (out / 'README.md').write_text(
        '# 财报分析同比与冻结证据复核\n\n'
        '虚构探针执行真实 Decimal 分析内核，明确区分公司/口径、版本、期间、披露时序与公式前提。'
        '真实运行复核只读冻结结果，逐项重算并按稳定证据 ID 反查原 PDF；不修改历史记录。'
        '本产物是开发技术复核，不是独立真人金标、真实供应商兼容性验收或最终 EXE 验收。\n',
        encoding='utf-8')
    if actual:
        with (out / 'README.md').open('a', encoding='utf-8') as handle:
            handle.write(f"\n冻结运行：`{actual['source_run']}`；公司：{actual['company']}；口径：{actual['scope']}；"
                         f"研报截止日期：{actual['report_date']}。源结果文件前后 SHA-256 一致。\n\n"
                         f"共 {actual['total_metrics']} 个登记指标，状态计数 `{actual['metric_status_counts']}`；"
                         f"弃权代码计数 `{actual['abstention_code_counts']}`。逐项复核 {actual['verified_metrics_reviewed']} 个"
                         f"有值指标（含 {actual['verified_growth_reviewed']} 个同比），涉及 "
                         f"{actual['unique_operand_facts_reviewed']} 个唯一事实与 {actual['unique_evidence_ids_reviewed']} 个"
                         f"证据 ID，PDF 页码 `{actual['pdf_pages_reviewed']}`。"
                         f"异常指标：`{actual['metric_findings']}`。上述数量不是材料召回或金标覆盖率。\n\n"
                         '|期间|指标|Decimal 标准值|证据页|\n|---|---|---|---|\n')
            for check in actual['checks']:
                if check['metric'].endswith('_YOY'):
                    pages = sorted({e['page'] for e in check['evidence_pages']})
                    handle.write(f"|{check['period']}|{check['metric']}|{check['value']}|{pages}|\n")
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
