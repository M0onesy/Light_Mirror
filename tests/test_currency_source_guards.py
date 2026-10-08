"""Currency source guards are mechanism tests, not independently reviewed gold."""
from dataclasses import replace
from decimal import Decimal

import fitz
import pytest

from mingjing import parser
from mingjing.evidence import sha256_file
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions, source_currency
from mingjing.models import AtomicClaim, Fact
from mingjing.source_semantics import freeze_source_dimensions
from mingjing.table_mentions import extract_table_mentions


def store():
    return FactStore([
        Fact('r23', '甲公司', 'REVENUE', '2023Y', value=Decimal('100000000'),
             publish_date='2024-04-01', evidence=('source:p1:r1c1',)),
        Fact('r22', '甲公司', 'REVENUE', '2022Y', value=Decimal('80000000'),
             publish_date='2024-04-01', evidence=('source:p1:r1c2',)),
    ])


@pytest.mark.parametrize('amount,code', [
    ('1亿美元', 'USD'), ('10000万美元', 'USD'), ('1美元', 'USD'),
    ('1亿港元', 'HKD'), ('1亿港币', 'HKD'), ('1亿欧元', 'EUR'),
    ('US$1亿', 'USD'), ('USD 1亿', 'USD'), ('HK$1亿', 'HKD'),
    ('EUR 1亿', 'EUR'), ('€1亿', 'EUR'), ('1亿英镑', 'GBP'),
    ('1亿日元', 'JPY'), ('1亿加元', 'CAD'), ('1亿澳元', 'AUD'),
])
def test_foreign_amount_cannot_consume_equal_rmb_fact(amount, code, monkeypatch):
    claim, = extract_mentions(f'甲公司2023年合并口径营业收入为{amount}。', '甲公司', '2023Y')
    assert source_currency(claim.text, claim.source_numeric_start)['currency'] == code
    fact_store = store()
    original_query = fact_store.query
    def same_currency_query(*args, **kwargs):
        assert kwargs['currency'] == code
        return original_query(*args, **kwargs)
    monkeypatch.setattr(fact_store, 'query', same_currency_query)
    result = Judge(fact_store, '2024-04-22', require_dual_binding=True).verify(
        replace(freeze_source_dimensions(claim), binding_status='agreed'))
    assert (result.status, result.abstention_code) == ('abstained', 'X1')
    assert result.expected is None and not result.operands and not result.evidence and result.correction is None


@pytest.mark.parametrize('text,code', [
    ('甲公司2023年合并口径营业收入为$1亿。', 'X3'),
    ('甲公司2023年合并口径营业收入为1亿外币。', 'X3'),
    ('甲公司2023年合并口径币种：美元/港元 营业收入为1亿元。', 'X3'),
    ('甲公司2023年合并口径美元口径营业收入同比增长25%。', 'X1'),
    ('甲公司2023年合并口径以美元计量的营业收入同比增长25%。', 'X1'),
    ('甲公司2023年合并口径以美元计价，营业收入同比增长25%。', 'X1'),
    ('甲公司2023年合并口径币种：美元 营业收入同比增长25%。', 'X1'),
])
def test_unknown_or_foreign_rate_abstains_without_money_operand(text, code):
    claim, = extract_mentions(text, '甲公司', '2023Y')
    result = Judge(store(), '2024-04-22').verify(replace(claim, binding_status='agreed'))
    assert (result.status, result.abstention_code) == ('abstained', code)
    assert result.expected is None and not result.operands


def test_currency_is_local_and_later_foreign_amount_does_not_invalidate_rmb():
    text = '甲公司2023年合并口径营业收入为1亿元，美元口径营业收入为1亿美元。'
    claims = extract_mentions(text, '甲公司', '2023Y', preserve_occurrences=True)
    assert len(claims) == 2
    assert source_currency(text, claims[0].start) is None
    assert Judge(store(), '2024-04-22').verify(claims[0]).status == 'verified'
    assert Judge(store(), '2024-04-22').verify(claims[1]).abstention_code == 'X1'
    growth, = extract_mentions('甲公司2023年合并口径人民币口径营业收入同比增长25%。', '甲公司')
    assert Judge(store(), '2024-04-22').verify(growth).status == 'verified'


@pytest.mark.parametrize('changes', [
    {'relation': 'value', 'account': 'REVENUE', 'candidate_value': '100000000'},
    {'relation': 'yoy', 'candidate_value': '.25', 'unit': '%', 'base_period': '2022Y'},
    {'table_context': {}},
    {'table_context': {'source_currency': {'currency': 'CNY'}}},
])
def test_model_dimensions_and_metadata_cannot_erase_original_foreign_currency(changes):
    claim, = extract_mentions('甲公司2023年合并口径营业收入为1亿美元。', '甲公司')
    claim = replace(freeze_source_dimensions(claim), binding_status='agreed', **changes)
    result = Judge(store(), '2024-04-22').verify(claim)
    assert result.status == 'abstained' and result.abstention_code in {'X1', 'X3'}
    assert not result.operands and result.expected is None


def test_legacy_claim_without_numeric_offset_cannot_verify_foreign_money():
    claim = AtomicClaim('legacy', '甲公司2023年合并口径营业收入为1亿美元。', 0, 0,
                        company='甲公司', account='REVENUE', period='2023Y', unit='亿',
                        candidate_value='100000000', binding_status='agreed', caliber_hint=('合并','未扣非'))
    result = Judge(store(), '2024-04-22').verify(claim)
    assert (result.status, result.abstention_code)==('abstained','X1')
    assert result.expected is None and not result.operands


@pytest.mark.parametrize('unit', ['股', '万股', '亿股', '份', '万份'])
def test_share_quantity_does_not_trigger_foreign_money_guard(unit):
    claim = AtomicClaim('share', f'币种：美元\n普通股份100{unit}', 0, 0,
                        company='甲公司', account='UNKNOWN', period='2023Y', unit=unit,
                        candidate_value='100', parse_status='unresolved')
    result = Judge(store(), '2024-04-22').verify(claim)
    assert result.abstention_code == 'X6'


def pdf(tmp_path, rows):
    path = tmp_path / '2024-04-01_2023年年度报告.pdf'
    with fitz.open() as document:
        page = document.new_page(width=600, height=900)
        y = 40
        for row in rows:
            page.insert_text((40, y), row, fontname='china-s', fontsize=11)
            y += len(row.splitlines())*16 + 18
        document.save(path)
    return path


@pytest.mark.parametrize('declaration,currency', [('币种：美元', 'USD'), ('单位：美元', 'USD'),
    ('币种：港元', 'HKD'), ('Currency: USD', 'USD'), ('Functional currency: USD', 'USD')])
def test_known_foreign_table_preserves_currency_but_cannot_fill_cny_query(tmp_path, declaration, currency):
    path = pdf(tmp_path, ['合并利润表', declaration, '单位：万元', '2023年\n2022年',
                          '营业收入\n100.00\n80.00', '毛利率%\n25.00\n20.00'])
    facts = parser.extract_facts(path, '甲公司', '2023Y')
    assert facts and {fact.currency for fact in facts} == {currency}
    assert FactStore(facts).query_outcome('甲公司', 'REVENUE', '2023Y', cutoff='2024-04-22').status == 'missing'
    assert FactStore(facts).query('甲公司', 'REVENUE', '2023Y', currency=currency,
                                 cutoff='2024-04-22').value == Decimal('1000000')
    output = parser.extract_fact_window(path, pages=[1], company='甲公司', publish_date='2024-04-01',
                                        expected_sha256=sha256_file(path))
    assert output['facts'] and {fact.currency for fact in output['facts']} == {currency}
    assert 'currency_unproven' not in {issue['code'] for issue in output['issues']}
    page, = parser.extract_text(path)
    assert declaration.replace('/', ' / ')[:2] in page['text']
    assert page['blocks'] and '100.00' in page['text']


@pytest.mark.parametrize('declaration', ['币种：美元/欧元', '币种：未知币种'])
def test_unknown_or_conflicting_table_currency_stays_out_of_fact_store(tmp_path, declaration):
    path = pdf(tmp_path, ['合并利润表', declaration, '单位：万元', '2023年\n2022年',
                         '营业收入\n100.00\n80.00'])
    with pytest.warns(RuntimeWarning, match='币种'):
        assert parser.extract_facts(path, '甲公司', '2023Y') == []
    output = parser.extract_fact_window(path, pages=[1], company='甲公司', publish_date='2024-04-01',
                                        expected_sha256=sha256_file(path))
    assert output['facts'] == []
    assert 'currency_unproven' in {issue['code'] for issue in output['issues']}


def test_new_rmb_table_resets_preceding_foreign_currency(tmp_path):
    path = pdf(tmp_path, ['合并利润表', '币种：美元', '单位：元', '2023年\n2022年',
                         '营业收入\n100.00\n80.00',
                         '母公司利润表', '币种：人民币', '单位：元', '2023年\n2022年',
                         '营业收入\n3.00\n2.00'])
    facts = parser.extract_facts(path, '甲公司', '2023Y')
    assert [(f.scope, f.currency, f.value) for f in facts] == [('合并', 'USD', Decimal('100.00')),
        ('合并', 'USD', Decimal('80.00')), ('母公司', 'CNY', Decimal('3.00')), ('母公司', 'CNY', Decimal('2.00'))]


def test_foreign_draft_table_header_reaches_each_numeric_occurrence():
    text = '合并利润表(万元)\n币种：美元\n2022A\n2023A\n营业收入\n80\n100\nYOY(%)\n25\n25\n'
    claims = extract_table_mentions(text, [{'start':0, 'end':len(text), 'page':1}], [], '甲公司', '2023Y')
    assert len(claims) == 4
    for claim in claims:
        assert claim.table_context['source_currency']['currency'] == 'USD'
        result = Judge(store(), '2024-04-22', require_dual_binding=True).verify(
            replace(freeze_source_dimensions(claim), binding_status='agreed'))
        assert result.status == 'abstained' and not result.operands
        assert result.abstention_code == 'X1'


def test_same_usd_claim_can_verify_or_recompute_without_fx_but_cny_cannot_consume_usd():
    usd_store = FactStore([replace(fact, currency='USD') for fact in store().facts])
    value, = extract_mentions('甲公司2023年合并口径营业收入为1亿美元。', '甲公司')
    growth, = extract_mentions('甲公司2023年合并口径美元口径营业收入同比增长25%。', '甲公司')
    assert Judge(usd_store, '2024-04-22').verify(value).status == 'verified'
    assert Judge(usd_store, '2024-04-22').verify(growth).status == 'verified'
    cny, = extract_mentions('甲公司2023年合并口径营业收入为1亿元。', '甲公司')
    result = Judge(usd_store, '2024-04-22').verify(cny)
    assert (result.status, result.abstention_code) == ('abstained', 'X1')
    assert result.expected is None and not result.operands
