"""Exposed official-page regressions, not independently adjudicated gold."""
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing import parser
from mingjing.evidence import sha256_file
from mingjing.models import Fact
from tests.test_fact_window import pdf


SOURCE = json.loads((Path(__file__).parent/'fixtures/prospectus_period_boundary_14.json').read_text('utf-8'))


@pytest.mark.parametrize('caption,suffix', [('2023年1—6月', 'H1'), ('2023年1月—6月', 'H1'),
    ('2023年1月-3月', 'Q1'), ('2023年1月—9月', '9M'), ('2023年1月—12月', 'Y')])
def test_explicit_month_interval_never_inherits_requested_annual_period(caption, suffix):
    periods, _ = parser._window_header_periods('项目'+caption+'2022年度2021年度2020年度', '2023Y')
    assert periods == ['2023'+suffix, '2022Y', '2021Y', '2020Y']


def extract(source):
    return parser._extract_table_facts(source['pages'], path=Path(source['filename']),
        company=source['company'], effective_period='2023Y', doc_sha=source['doc_sha256'],
        disclosure_date='2023-10-16', disclosure_version='原披露')


def test_original_prospectus_four_column_header_distinguishes_half_year_and_annual():
    facts = extract(SOURCE[0])
    revenue = {(fact.period, fact.value) for fact in facts if fact.account == 'REVENUE' and fact.scope == '合并'}
    assert ('2023H1', Decimal('202512534.26')) in revenue
    assert ('2022Y', Decimal('312776998.45')) in revenue
    assert not any(fact.period == '2023Y' for fact in facts)
    assert all(len(fact.evidence) >= 3 for fact in facts)


def test_original_policy_change_and_fundraising_tables_do_not_inherit_financial_statement():
    facts = extract(SOURCE[1])
    # The fundraising row on p447 is 2,520万元 with a 7.39% composition cell.
    # It is neither reported mother-company R&D expense nor a fiscal-year value.
    assert not any(':p447:' in evidence for fact in facts for evidence in fact.evidence)
    assert not any(fact.account == 'EXP_RD' and fact.value == Decimal('25200000') for fact in facts)
    assert any(fact.account == 'REVENUE' for fact in facts)


def proposals(path, rows):
    return [Fact(str(index), '公司甲', 'REVENUE', '2023Y', source_doc=sha256_file(path)[:12],
        source_path=str(path), publish_date='2024-04-15', value=Decimal(value),
        dimension_kind='business', dimension_id=business, currency=currency,
        evidence=('source:p1:r1c1',)) for index, (business, currency, value) in enumerate(rows)]


def test_window_conflict_guard_keeps_separate_businesses_and_currencies(tmp_path, monkeypatch):
    path = pdf(tmp_path, [['原始页面已锁定']])
    facts = proposals(path, [('业务甲', 'CNY', '100'), ('业务乙', 'CNY', '100'), ('业务甲', 'USD', '10')])
    monkeypatch.setattr(parser, '_extract_table_facts', lambda *a, **kw: facts)
    output = parser.extract_fact_window(path, pages=[1], company='公司甲',
        publish_date='2024-04-15', expected_sha256=sha256_file(path))
    assert output['facts'] == facts and not output['issues']


def test_window_conflict_guard_rejects_only_the_conflicting_business(tmp_path, monkeypatch):
    path = pdf(tmp_path, [['原始页面已锁定']])
    facts = proposals(path, [('业务甲', 'CNY', '100'), ('业务甲', 'CNY', '200'), ('业务乙', 'CNY', '100')])
    monkeypatch.setattr(parser, '_extract_table_facts', lambda *a, **kw: facts)
    output = parser.extract_fact_window(path, pages=[1], company='公司甲',
        publish_date='2024-04-15', expected_sha256=sha256_file(path))
    assert output['facts'] == [facts[2]]
    assert len(output['issues']) == 1
    assert output['issues'][0]['dimension_id'] == '业务甲'
