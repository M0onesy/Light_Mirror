"""Percentage change columns are identified by their header, never magnitude."""
from decimal import Decimal

import pytest

from mingjing import parser
from test_fact_quality import _extract, _pages


@pytest.mark.parametrize('change', ['-', '—', '-10.00%', '0.00%', '999.00%'])
def test_relative_ratio_header_discards_only_declared_change_cell(tmp_path, monkeypatch, change):
    pages = _pages([['主要会计数据和财务指标', '单位：元\n本期\n上年同期\n增减比例%',
                     '毛利率%\n40.00%\n30.00%\n'+change]])
    _, facts = _extract(tmp_path, monkeypatch, pages)
    assert [(fact.period, fact.value) for fact in facts] == [
        ('2023Y', Decimal('.4000')), ('2022Y', Decimal('.3000'))]


def test_ratio_change_column_between_three_years_retains_original_periods(tmp_path, monkeypatch):
    pages = _pages([['主要会计数据和财务指标', '单位：元',
                     '2023年\n2022年\n同比增减%\n2021年',
                     '毛利率%\n40.00%\n30.00%\n-10.00%\n25.00%']])
    _, facts = _extract(tmp_path, monkeypatch, pages)
    assert {fact.period: fact.value for fact in facts} == {
        '2023Y': Decimal('.4000'), '2022Y': Decimal('.3000'), '2021Y': Decimal('.2500')}


@pytest.mark.parametrize('cells', ['-\n30.00%\n-10.00%', '40.00%\n-\n-10.00%'])
def test_change_column_does_not_shift_independently_missing_historical_cell(tmp_path, monkeypatch, cells):
    pages = _pages([['主要会计数据和财务指标', '单位：元\n本期\n上年同期\n增减比例%',
                     '毛利率%\n'+cells]])
    _, facts = _extract(tmp_path, monkeypatch, pages)
    assert len(facts) == 1
    assert (facts[0].period, facts[0].value) == (
        ('2022Y', Decimal('.3000')) if cells.startswith('-') else ('2023Y', Decimal('.4000')))


@pytest.mark.parametrize('header', ['本期\n上年同期', '本期\n上年同期\n增减比例%\n同比增长%',
                                  '本期\n上年同期\n2021年'])
def test_unexplained_or_duplicate_surplus_ratio_columns_remain_unparsed(tmp_path, monkeypatch, header):
    pages = _pages([['主要会计数据和财务指标', '单位：元\n'+header,
                     '毛利率%\n40.00%\n30.00%\n-10.00%']])
    _, facts = _extract(tmp_path, monkeypatch, pages)
    assert not any(fact.account == 'GROSS_MARGIN' for fact in facts)


def test_geometric_change_caption_must_be_between_the_actual_financial_columns():
    block = {'text': '2023年\n2022年\n同比增减%\n2021年', 'geometry_lines': [
        {'text': '2023年', 'bbox': [200, 10, 240, 20]},
        {'text': '2022年', 'bbox': [300, 10, 340, 20]},
        {'text': '同比增减%', 'bbox': [400, 10, 440, 20]},
        {'text': '2021年', 'bbox': [500, 10, 540, 20]}]}
    assert parser._growth_column_position(block, ['2023Y', '2022Y', '2021Y']) == 2
    block['geometry_lines'][2]['bbox'] = [200, 10, 240, 20]
    assert parser._growth_column_position(block, ['2023Y', '2022Y', '2021Y']) is None


def test_detached_change_header_cannot_establish_unpositioned_column_order():
    assert parser._growth_column_position({'text': '同比增减%'}, ['2023Y', '2022Y']) is None
