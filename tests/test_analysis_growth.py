"""Source-bound growth metrics and dated material requirements."""
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from mingjing.analysis import analyze_financials
from mingjing.facts import FactStore
from mingjing.models import Fact


def fact(identifier, account, period, value, source='current-report', scope='合并', date='2025-03-01'):
    return Fact(identifier, '甲公司', account, period, value=Decimal(value), source_doc=source,
                scope=scope, publish_date=date, evidence=(identifier + ':page-evidence',))


def growth(facts, account='REVENUE', period='2024Y', scope='合并', date='2025-03-31'):
    analysis = analyze_financials(FactStore(facts), '甲公司', [period], date, scope)
    return next(row for row in analysis['metrics'] if row['metric'] == account + '_YOY')


@pytest.mark.parametrize('account', ['REVENUE', 'NP_PARENT', 'CFO'])
def test_growth_uses_current_reports_adjusted_comparative_not_older_original(account):
    current = fact('current', account, '2024Y', '120')
    adjusted = fact('adjusted', account, '2023Y', '100')
    old = fact('old', account, '2023Y', '80', source='older-report', date='2024-03-01')
    row = growth([current, old, adjusted], account)
    assert row['status'] == 'verified' and row['value'] == '0.2'
    assert row['base_period'] == '2023Y' and row['unit'] == '比例' and row['rule_id'] == 'E5-yoy'
    assert [operand['fact_id'] for operand in row['operands']] == ['current', 'adjusted']
    assert row['evidence'] == ['current:page-evidence', 'adjusted:page-evidence']


@pytest.mark.parametrize('period,base_period', [('2024H1', '2023H1'), ('2024Q2', '2023Q2'), ('2024Y', '2023Y')])
def test_growth_does_not_mix_annual_cumulative_and_single_quarter(period, base_period):
    row = growth([fact('current', 'REVENUE', period, '120'), fact('base', 'REVENUE', base_period, '100')], period=period)
    assert row['value'] == '0.2' and row['base_period'] == base_period
    missing = growth([fact('current', 'REVENUE', period, '120'), fact('other-kind', 'REVENUE', '2023Q1', '100')], period=period)
    assert missing['value'] is None and missing['abstention_code'] == 'X1'


@pytest.mark.parametrize('base_value', ['0', '-100'])
def test_zero_and_negative_bases_abstain_with_both_operands_visible(base_value):
    row = growth([fact('current', 'REVENUE', '2024Y', '120'), fact('base', 'REVENUE', '2023Y', base_value)])
    assert row['value'] is None and row['abstention_code'] == 'X2'
    assert len(row['operands']) == len(row['evidence']) == 2


def test_missing_comparative_never_borrows_another_document_or_scope():
    row = growth([fact('current', 'REVENUE', '2024Y', '120'),
                  fact('old', 'REVENUE', '2023Y', '100', source='older-report'),
                  fact('parent', 'REVENUE', '2023Y', '100', scope='母公司')])
    assert row['value'] is None and row['abstention_code'] == 'X1'
    assert [operand['fact_id'] for operand in row['operands']] == ['current']


def test_analysis_without_as_of_date_never_consumes_facts_or_future_events():
    store = FactStore([fact('future', 'REVENUE', '2024Y', '120')])
    store.query = lambda *a, **k: (_ for _ in ()).throw(AssertionError('undated analysis must not query facts'))
    analysis = analyze_financials(store, '甲公司', ['2024Y'], None)
    assert analysis['metrics'] and all(row['abstention_code'] == 'X5' and row['value'] is None for row in analysis['metrics'])
    assert not analysis['events'] and not analysis['signals']


@pytest.mark.parametrize('date', ['', '2025-02-31', 'unknown'])
def test_unknown_source_publication_date_is_X4_not_missing_material(date):
    current = fact('current', 'REVENUE', '2024Y', '120')
    base = fact('undated-base', 'REVENUE', '2023Y', '100', date=date)
    row = growth([current, base])
    assert row['status'] == 'abstained' and row['abstention_code'] == 'X4'
    assert [f['fact_id'] for f in row['operands']] == ['current']
    assert base.evidence[0] not in row['evidence']
    analysis = analyze_financials(FactStore([replace(current, publish_date=date)]),
                                 '甲公司', ['2024Y'], '2025-03-31')
    direct = next(m for m in analysis['metrics'] if m['metric'] == 'REVENUE')
    assert direct['abstention_code'] == 'X4' and not direct['evidence']


@pytest.mark.parametrize('kind', ['conflict', 'low_confidence'])
def test_same_report_comparative_conflict_or_low_confidence_is_X4(kind):
    current = fact('current', 'REVENUE', '2024Y', '120')
    base = fact('base', 'REVENUE', '2023Y', '100')
    rejected = replace(base, fact_id='rejected', evidence=('rejected:private-page',),
                       value=Decimal('80')) if kind == 'conflict' else replace(base, parse_conf='0.4')
    row = growth([current, base, rejected] if kind == 'conflict' else [current, rejected])
    assert row['value'] is None and row['abstention_code'] == 'X4'
    assert [f['fact_id'] for f in row['operands']] == ['current']
    assert row['evidence'] == ['current:page-evidence']


@pytest.mark.parametrize('rejected', [
    dict(source='other-report', date=''),
    dict(scope='母公司', date=''),
    dict(date='2025-04-01'),
])
def test_other_document_scope_and_future_do_not_leak_or_trigger_X4(rejected):
    current = fact('current', 'REVENUE', '2024Y', '120')
    base = replace(fact('sensitive-rejected', 'REVENUE', '2023Y', '999999', **rejected),
                   parse_conf='0.1')
    row = growth([current, base])
    assert row['abstention_code'] == 'X1'
    serialized = json.dumps(row, ensure_ascii=False)
    assert 'sensitive-rejected' not in serialized and '999999' not in serialized
    assert row['evidence'] == ['current:page-evidence']


def test_superseded_low_confidence_does_not_override_usable_current_version():
    current = fact('current', 'REVENUE', '2024Y', '120')
    base = fact('adjusted-base', 'REVENUE', '2023Y', '100')
    old = replace(fact('old-low-confidence', 'REVENUE', '2023Y', '60', date='2025-02-01'), parse_conf='0.2')
    row = growth([current, old, base])
    assert row['status'] == 'verified' and row['value'] == '0.2'
    assert [f['fact_id'] for f in row['operands']] == ['current', 'adjusted-base']


@pytest.mark.parametrize('metric,first,second', [
    ('GROSS_MARGIN', 'REVENUE', 'COST'), ('NET_MARGIN', 'NP', 'REVENUE'),
    ('DEBT_RATIO', 'LIABILITIES', 'ASSETS'), ('CASH_CONTENT', 'CFO', 'NP'),
])
def test_ratio_missing_source_identity_never_borrows_a_global_fact(metric, first, second):
    facts = [fact('no-document-anchor', first, '2024Y', '100', source=''),
             fact('unrelated-document', second, '2024Y', '75', source='other-report')]
    analysis = analyze_financials(FactStore(facts), '甲公司', ['2024Y'], '2025-03-31')
    row = next(m for m in analysis['metrics'] if m['metric'] == metric)
    assert row['status'] == 'abstained' and row['abstention_code'] == 'X4'
    assert [f['fact_id'] for f in row['operands']] == ['no-document-anchor']
    assert facts[1].evidence[0] not in row['evidence']


@pytest.mark.parametrize('metric,first,second', [
    ('GROSS_MARGIN', 'REVENUE', 'COST'), ('NET_MARGIN', 'NP', 'REVENUE'),
    ('DEBT_RATIO', 'LIABILITIES', 'ASSETS'), ('CASH_CONTENT', 'CFO', 'NP'),
])
def test_ratio_confidence_failure_is_X4_and_absent_operand_is_X1(metric, first, second):
    anchor = fact('anchor', first, '2024Y', '100')
    low = replace(fact('low', second, '2024Y', '75'), parse_conf='0.5')
    for facts, code in [([anchor, low], 'X4'), ([anchor], 'X1')]:
        analysis = analyze_financials(FactStore(facts), '甲公司', ['2024Y'], '2025-03-31')
        row = next(m for m in analysis['metrics'] if m['metric'] == metric)
        assert row['value'] is None and row['abstention_code'] == code
        assert row['evidence'] == ['anchor:page-evidence']


def test_non_recurring_component_failure_is_X4_without_rejected_evidence():
    facts = [fact(name, name, '2024Y', value) for name, value in
             [('NP_PARENT', '130'), ('NRI_TOTAL', '20'), ('NRI_TAX_EFFECT', '3'), ('NRI_MINORITY', '2')]]
    facts[-1] = replace(facts[-1], parse_conf='0.5')
    analysis = analyze_financials(FactStore(facts), '甲公司', ['2024Y'], '2025-03-31')
    row = next(m for m in analysis['metrics'] if m['metric'] == 'NP_PARENT_RECOMPUTED')
    assert row['value'] is None and row['abstention_code'] == 'X4'
    assert facts[-1].evidence[0] not in row['evidence']
