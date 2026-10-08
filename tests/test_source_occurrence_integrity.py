"""Explicit source syntax exclusions retain occurrence and reproducible basis."""
from dataclasses import replace

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions, listed_periods
from mingjing.source_semantics import freeze_source_dimensions, source_binding_issue


def build(text, lines=None):
    return CoverageLedger.build(text, [{'start':0, 'end':len(text), 'page':1, 'lines':lines or []}], [], '甲公司', '2099Y')


@pytest.mark.parametrize('kind,suffix,values', [
    ('', 'Y', '4437.80、10158.77和13166.95万元'),
    ('第四季度', 'Q4', '37.56%、35.96%及31.89%'),
    ('上半年', 'H1', '1万元/2万元/3万元'),
    ('下半年', 'H2', '1、2和3亿元'),
])
def test_ordered_period_range_binds_each_original_occurrence(kind, suffix, values):
    text = f'甲公司2020–2022年{kind}合并口径营业收入分别为{values}。'
    ledger, claims = build(text)
    assert len(claims) == 3
    assert [c.period for c in claims] == [f'2020{suffix}', f'2021{suffix}', f'2022{suffix}']
    assert all(text[c.start:c.end] == c.numeric_text for c in claims)
    for c in claims:
        period, proof = listed_periods(c.text, c.source_numeric_start)
        assert period == c.period and not proof['ambiguous']
        assert all(text[row['start']:row['end']] == row['text'] for row in ledger.entries)
        if c.account == 'REVENUE':
            frozen = freeze_source_dimensions(c)
            assert source_binding_issue(replace(frozen, binding_status='agreed'), strict=True) is None
            assert source_binding_issue(replace(frozen, period='2099Y', binding_status='agreed'), strict=True)[0] == 'X3'


@pytest.mark.parametrize('text', [
    '甲公司2020-2022年合并口径营业收入分别为1万元、2万元。',
    '甲公司2020-2022年合并口径营业收入分别为1万元、2万元、3万元、4万元。',
    '甲公司2022-2020年合并口径营业收入分别为1万元、2万元、3万元。',
])
def test_non_matching_ordered_list_never_guesses_last_period(text):
    _, claims = build(text)
    assert claims and all(c.period == '' for c in claims)
    assert all(c.table_context['source_period_list']['ambiguous'] for c in claims)


def test_secondary_list_needs_its_own_period_mapping():
    text = '甲公司2020-2022年合并口径营业收入分别为1万元、2万元、3万元，营业成本分别为1万元、2万元、3万元。'
    _, claims = build(text)
    assert [c.period for c in claims[:3]] == ['2020Y','2021Y','2022Y']
    assert [c.period for c in claims[3:]] == ['', '', '']


def test_multiline_period_and_range_minus_are_excluded_without_financial_loss():
    text = '2020\n年-2023年营业收入增长43%。\n2021年-2025年营业收入增长116.79%。\n甲公司2023年合并营业收入2023亿元。'
    ledger, claims = build(text)
    years = [row for row in ledger.entries if row['text'].strip() in {'2020','-2023','2021','-2025','2023'}]
    assert years and all(row['status'] == 'rule_excluded' for row in years)
    assert any(c.numeric_text == '43%' for c in claims)
    assert any(c.numeric_text == '116.79%' for c in claims)
    assert any(c.numeric_text == '2023亿元' for c in claims)
    assert any(row['exclusion_basis']['source_span']['text'].startswith('2020\n年') for row in years if 'exclusion_basis' in row)


@pytest.mark.parametrize('amount', ['2023亿元', '2023元', '2020-2022元', '2020—2022元', '2023万元', '2023%', '2023万股'])
def test_year_shaped_amount_and_percentage_never_receive_period_exclusion(amount):
    text = f'甲公司2023年合并营业收入为{amount}。'
    ledger, _ = build(text)
    target_start = text.index(amount)
    target = [r for r in ledger.entries if r['start'] >= target_start]
    assert target and all(r['reason'] != 'declared_period' for r in target)


@pytest.mark.parametrize('amount', ['1000-3000元','1000—3000元','1万元至3万元','-3000--1000元','20%-30%'])
def test_amount_range_is_not_relabelled_as_signed_scalar(amount, monkeypatch):
    text = f'甲公司2023年合并营业收入为{amount}。'
    ledger, claims = build(text)
    assert claims and all(r['reason']!='declared_period' for r in ledger.entries if r['start']>=text.index(amount))
    store = FactStore([])
    # A registered full-range assertion can now look up a same-unit fact;
    # an endpoint never becomes a scalar verification and missing is X1.
    results=[Judge(store,'2024-04-22').verify(replace(c,account='REVENUE',binding_status='agreed')) for c in claims]
    assert all(r.status=='abstained' for r in results)
    assert all(r.abstention_code==('X3' if amount=='20%-30%' else 'X1') for r in results)


def test_explicit_duration_and_technical_units_remain_in_ledger_as_outside_financial_scope():
    text = ('公司营业收入增长20%，服务器8万台、65万个计算核心，算力300 EFLOPS，研发持续5年。\n'
            '1E FLOPS = 10^18 FLOPS = 1000P FLOPS。\n'
            '甲公司2023年合并营业收入为8万元，毛利率65%。')
    ledger, claims = build(text)
    excluded = [r for r in ledger.entries if r['reason'] in {'technical_quantity','duration_quantity','technical_unit_identity'}]
    assert len(excluded) >= 6
    assert all(r['status'] == 'out_of_scope' and r['claim_id'] is None and r.get('exclusion_basis') for r in excluded)
    assert all(text[slice(*r['exclusion_basis']['occurrence_span'])] == r['text'] for r in excluded)
    assert any(c.numeric_text == '8万元' for c in claims)
    assert any(c.numeric_text == '65%' for c in claims)
    assert any(c.numeric_text == '20%' for c in claims)


def test_ordinal_column_requires_header_and_geometry_and_does_not_hide_neighbor_amount():
    rows = [('序号', [20,20,40,30]), ('营业收入（万元）', [100,20,200,30]),
            ('占比%', [300,20,350,30]), ('1', [22,40,28,50]), ('2,967', [100,40,140,50]),
            ('14.65%', [300,40,340,50]), ('2', [22,60,28,70]), ('1', [100,60,110,70]),
            ('数据来源：公司公告', [20,80,350,90])]
    text, lines = '', []
    for value, box in rows:
        start = len(text)
        text += value+'\n'
        lines.append({'text':value, 'start':start, 'end':start+len(value), 'bbox':box})
    ledger, claims = build(text, lines)
    ordinals = [r for r in ledger.entries if r['reason']=='table_ordinal_column']
    assert len(ordinals)==2 and all(r['status']=='rule_excluded' and r.get('exclusion_basis') for r in ordinals)
    financial = [r for r in ledger.entries if r['text'].strip() in {'2,967','14.65%'} or r['start']==lines[-2]['start']]
    assert len(financial)==3 and all(r['reason']!='table_ordinal_column' for r in financial)
    ledger_without_geometry, _ = build(text)
    assert not any(r['reason']=='table_ordinal_column' for r in ledger_without_geometry.entries)
    assert {(r['start'],r['end']) for r in ledger.entries} == {(r['start'],r['end']) for r in ledger_without_geometry.entries}
