"""Source-domain regressions, not independent human financial gold.

The archived ten claims exposed unsafe group-versus-segment comparisons.
Assertions verify a domain guard and forbidden fact consumption; they do not
assert that those business figures themselves are financially correct.
"""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.facts import FactStore, fact_from_dict
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import AtomicClaim, Fact
from mingjing.replay import _claim_from_dict
from mingjing.source_semantics import (freeze_source_dimensions, source_binding_issue,
                                      source_dimensions_from_text)
from mingjing.table_mentions import extract_table_mentions


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/segment_source_regression.json').read_text(encoding='utf-8'))


def _store():
    return FactStore([
        Fact('rev23', '甲', 'REVENUE', '2023Y', value=Decimal('120000000'),
             publish_date='2024-04-15', source_doc='fixture-report', evidence=('fixture:r1c1',)),
        Fact('rev22', '甲', 'REVENUE', '2022Y', value=Decimal('100000000'),
             publish_date='2024-04-15', source_doc='fixture-report', evidence=('fixture:r1c2',)),
        Fact('gm23', '甲', 'GROSS_MARGIN', '2023Y', value=Decimal('0.2563'),
             publish_date='2024-04-15', source_doc='fixture-report', evidence=('fixture:r2c1',)),
    ])


@pytest.mark.parametrize('data', FIXTURE['claims'], ids=lambda c: c['claim_id'])
def test_ten_development_observations_cannot_consume_whole_company_facts(data, monkeypatch):
    assert FIXTURE['origin'] == 'development_observation_not_human_gold'
    store = FactStore([fact_from_dict(f) for f in FIXTURE['frozen_fact_candidates']])
    assert store.facts  # The regression is not passing merely for lack of data.
    def forbidden(*args, **kwargs):
        raise AssertionError('Source qualifier must stop whole-company fact consumption')
    monkeypatch.setattr(store, 'query', forbidden)
    monkeypatch.setattr(store, 'versions', forbidden)
    claim = _claim_from_dict(data)  # Archived payload has no new offset field.
    result = Judge(store, '2024-04-22', require_dual_binding=True).verify(claim)
    assert result.status == 'abstained'
    assert result.abstention_code == ('X2' if data['claim_id'] == 'claim-00011' else 'X3')
    assert result.expected is None and not result.operands and not result.evidence
    assert result.correction is None


def test_total_margin_and_later_segment_margin_are_separate_source_occurrences():
    source = '2023年公司合并口径毛利率略微下降至26%，其中核心业务超算云服务毛利率为27%。'
    claims = extract_mentions(source, '甲', '2023Y', preserve_occurrences=True)
    assert len(claims) == 2
    assert all(c.text[c.source_numeric_start:c.source_numeric_start+len(c.numeric_text)] == c.numeric_text for c in claims)
    total, segment = [replace(c, account='GROSS_MARGIN', relation='value', binding_status='agreed') for c in claims]
    judge = Judge(_store(), '2024-04-22', require_dual_binding=True)
    assert judge.verify(total).status == 'verified'
    segment_source = source_dimensions_from_text(segment)
    assert segment_source['company'] == '甲' and segment_source['dimension_id'] == '超算云服务'
    assert not segment_source.get('source_company_conflict')
    missing_segment = judge.verify(segment)
    assert missing_segment.abstention_code == 'X1'  # Genuine source identity lacks its own matching fact.
    assert missing_segment.expected is None and not missing_segment.operands


def test_earlier_segment_does_not_pollute_explicit_later_total():
    source = '2023年核心业务超算云服务毛利率为27%，公司整体毛利率为26%。'
    claims = extract_mentions(source, '甲', '2023Y', preserve_occurrences=True)
    claims = [replace(c, account='GROSS_MARGIN', relation='value', binding_status='agreed') for c in claims]
    judge = Judge(_store(), '2024-04-22')
    assert claims[0].table_context['dimension_id']=='超算云服务'
    assert judge.verify(claims[0]).abstention_code == 'X1'  # Source-bound business lacks its own fact.
    assert judge.verify(claims[1]).status == 'verified'


def test_same_sentence_amount_and_signed_yoy_pass_but_cagr_cannot_be_relabelled():
    source = '2023年公司实现营收1.20亿元（+20%），2018-2023年5年营收CAGR达41%。'
    amount, growth, cagr = extract_mentions(source, '甲', '2023Y')
    assert cagr.relation == 'cagr'
    judge = Judge(_store(), '2024-04-22')
    assert judge.verify(amount).status == 'verified'
    assert growth.relation == 'yoy' and judge.verify(growth).status == 'verified'
    for account, relation in [('REVENUE', 'value'), ('REVENUE', 'yoy'), ('GROSS_MARGIN', 'value')]:
        result = judge.verify(replace(cagr, account=account, relation=relation,
                                      base_period='2022Y', binding_status='agreed'))
        assert result.abstention_code == 'X2' and result.correction is None


@pytest.mark.parametrize('source', [
    '2023年超算云整体收入为1.20亿元。',
    '2023年从收入构成来看，共建模式营业收入为1.20亿元。',
    '2023年主营业务收入为1.20亿元。',
    '2023年分产品营业收入为1.20亿元。',
    '2023年公司软件业务收入为1.20亿元。',
    '2023年前五大客户营收为1.20亿元。',
])
def test_business_qualifier_cannot_be_removed_by_model_binding(source):
    claims = extract_mentions(source, '甲', '2023Y')
    assert len(claims) == 1
    result = Judge(_store(), '2024-04-22').verify(replace(
        claims[0], account='REVENUE', relation='value', caliber_hint=('合并', '未扣非'), binding_status='agreed'))
    # The explicitly defined top-five customer/cloud aggregate has a valid
    # source identity but no matching dimension fact. Other unregistered qualifiers
    # remain binding ambiguity; neither may consume the issuer total.
    assert result.abstention_code == ('X1' if claims[0].table_context.get('dimension_kind') in {'customer','business'} else 'X3')
    assert not result.operands and result.expected is None and result.correction is None


@pytest.mark.parametrize('subject,explicit,conflict', [
    ('甲公司', True, False), ('公司', False, False), ('乙公司', False, True),
])
def test_undefined_main_business_caption_is_not_invented_as_a_company(subject, explicit, conflict):
    source = subject + '2024年主营业务收入80元。'
    claim, = extract_mentions(source, '甲公司', '2024Y', preserve_occurrences=True)
    proof = source_dimensions_from_text(claim)
    assert claim.account == 'UNKNOWN' and claim.numeric_text == '80元'
    assert proof['company_source_explicit'] is explicit
    assert bool(proof.get('source_company_conflict')) is conflict
    assert proof['source_company_subjects'] == ([subject] if explicit or conflict else [])
    assert proof['company'] == ('' if conflict else '甲公司')
    # Correcting the corporate subject does not grant a business identity or
    # permission to consume issuer-total revenue, even when the amount agrees.
    store = FactStore([Fact('total', '甲公司', 'REVENUE', '2024Y', value=Decimal('80'),
                            publish_date='2024-04-01')])
    rebound = replace(freeze_source_dimensions(claim), account='REVENUE', binding_status='agreed',
                      caliber_hint=('合并', '未扣非'))
    result = Judge(store, '2025-03-31', require_dual_binding=True).verify(rebound)
    assert result.status == 'abstained' and result.abstention_code == 'X3'
    assert not result.operands and result.expected is None and result.correction is None


def test_repeated_equal_numbers_need_exact_local_identity_and_replacement():
    source = '2023年超算云服务毛利率为27%，公司整体毛利率为27%。'
    segment, total = extract_mentions(source, '甲', '2023Y', preserve_occurrences=True)
    judge = Judge(_store(), '2024-04-22')
    bound = [replace(c, account='GROSS_MARGIN', relation='value', binding_status='agreed') for c in [segment, total]]
    assert bound[0].table_context['dimension_id']=='超算云服务'
    assert judge.verify(bound[0]).abstention_code == 'X1'
    result = judge.verify(bound[1])
    assert result.status == 'violated'  # Genuine total mismatch in synthetic mechanism fixture.
    assert result.correction['replacement_text'] == source[:-1].replace('公司整体毛利率为27%', '公司整体毛利率为26%')
    old = replace(bound[1], source_numeric_start=None)
    assert judge.verify(old).abstention_code == 'X3'
    for offset in [-1, len(source)+1, 0, True]:
        assert judge.verify(replace(bound[1], source_numeric_start=offset)).abstention_code == 'X3'
    restored = _claim_from_dict(bound[1].to_dict())
    assert restored.source_numeric_start == bound[1].source_numeric_start
    assert judge.verify(restored).to_dict() == result.to_dict()


@pytest.mark.parametrize('account,relation,unit,value', [
    ('REVENUE', 'value', '%', '0.2'),
    ('REVENUE', 'yoy', '元', '20'),
    ('GROSS_MARGIN', 'value', '元', '26'),
])
def test_final_dimensions_stop_percentage_amount_cross_comparisons(account, relation, unit, value):
    item = AtomicClaim('unit', '2023年公司数字提及。', 0, 0, company='甲',
                       account=account, period='2023Y', candidate_value=value,
                       unit=unit, relation=relation, binding_status='agreed')
    result = Judge(_store(), '2024-04-22').verify(item)
    assert result.abstention_code == 'X3'
    assert result.expected is None and result.correction is None


def test_table_occurrences_retain_local_position_through_dual_binding_and_replay():
    source = '合并利润表(百万元)\n2022A\n2023A\n营业收入\n100\n120\nYOY(%)\n20\n20\n'
    claims = extract_table_mentions(source, [{'start': 0, 'end': len(source), 'page': 1}], [], '甲', '2023Y')
    assert len(claims) == 4
    for c in claims:
        assert c.text[c.source_numeric_start:c.source_numeric_start+len(c.numeric_text)] == c.numeric_text
    growth = replace(claims[-1], binding_status='agreed')
    assert Judge(_store(), '2024-04-22', require_dual_binding=True).verify(growth).status == 'verified'
    assert _claim_from_dict(growth.to_dict()).source_numeric_start == growth.source_numeric_start


@pytest.mark.parametrize('label', ['YOY(%)', '同比(%)', '增长率(%)', 'YOY', '同比', '增长率'])
def test_standalone_table_growth_uses_source_account_not_preceding_numeric_cell_as_company(label):
    source = f'合并利润表(百万元)\n2022A\n2023A\n营业收入\n100\n120\n{label}\n20\n20\n'
    claims = extract_table_mentions(source, [{'start': 0, 'end': len(source), 'page': 1}], [], '甲', '2099Y')
    growth = freeze_source_dimensions(claims[-1])
    proof = source_dimensions_from_text(growth)
    assert (proof['company'], proof['account'], proof['period'], proof['relation'], proof['base_period']) == (
        '甲', 'REVENUE', '2023Y', 'yoy', '2022Y')
    assert not proof['explicit']['company']
    assert proof['company_provenance'] == 'host_locked_issuer_context'
    assert source_binding_issue(growth, strict=True) is None  # Guard before A/B is also safe.
    judge = Judge(_store(), '2024-04-22', require_dual_binding=True)
    assert judge.verify(growth).abstention_code == 'X3'  # Independent A/B remains required.
    agreed = replace(growth, binding_status='agreed')
    result = judge.verify(agreed)
    assert result.status == 'verified'
    restored = _claim_from_dict(agreed.to_dict())
    assert source_dimensions_from_text(restored) == proof
    assert judge.verify(restored).to_dict() == result.to_dict()
    for dimensions in ({'account': 'NP'}, {'period': '2022Y'}, {'base_period': '2021Y'}, {'relation': 'value'}):
        rejected = judge.verify(replace(agreed, **dimensions))
        assert rejected.abstention_code == 'X3' and not rejected.operands


def test_table_growth_without_preceding_account_never_gains_a_financial_identity():
    source = '合并利润表(百万元)\n2022A\n2023A\nYOY(%)\n20\n20\n'
    claims = extract_table_mentions(source, [{'start': 0, 'end': len(source), 'page': 1}], [], '甲', '2023Y')
    assert len(claims) == 2 and all(c.account == 'UNKNOWN' for c in claims)
    result = Judge(_store(), '2024-04-22', require_dual_binding=True).verify(
        replace(claims[-1], account='REVENUE', binding_status='agreed'))
    assert result.status == 'abstained' and not result.operands


def test_business_title_remains_authoritative_for_table_cell():
    item = AtomicClaim('table-cell', '营业收入\n120\n', 5, 8, company='甲',
                       account='REVENUE', period='2023Y', candidate_value='120000000',
                       unit='百万元', numeric_text='120', source_numeric_start=5,
                       caliber_hint=('合并', '未扣非'), binding_status='agreed',
                       table_context={'title': '超算云业务收入构成'})
    result = Judge(_store(), '2024-04-22').verify(item)
    assert result.abstention_code == 'X3' and not result.operands


@pytest.mark.parametrize('source,account,relation', [
    ('2023年A产品营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('2023年产品A营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('2023年产品甲营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('产品A2023年营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('2023年云服务营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('2023年华东地区营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('华东地区2023年营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('2023年甲业务线营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('2023年业务线A营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('2023年云服务营业收入同比增长20%。', 'REVENUE', 'yoy'),
    ('2023年华东地区营业收入同比增长20%。', 'REVENUE', 'yoy'),
    ('2023年境外市场营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('2023年国内市场营业收入为1.20亿元。', 'REVENUE', 'value'),
    ('2023年软件产品毛利率为26%。', 'GROSS_MARGIN', 'value'),
    ('2023年华东地区毛利率为26%。', 'GROSS_MARGIN', 'value'),
])
def test_generic_product_service_or_geographic_value_equal_to_total_still_abstains(source, account, relation, monkeypatch):
    claims = extract_mentions(source, '甲', '2023Y')
    assert len(claims) == 1
    item = replace(claims[0], account=account, relation=relation,
                   base_period='2022Y' if relation == 'yoy' else None,
                   binding_status='agreed', caliber_hint=('合并', '未扣非'))
    store = _store()
    def forbidden(*args, **kwargs):
        raise AssertionError('A domain qualifier cannot be bypassed by equal numbers or model labels')
    monkeypatch.setattr(store, 'query', forbidden)
    monkeypatch.setattr(store, 'versions', forbidden)
    result = Judge(store, '2024-04-22').verify(item)
    assert result.abstention_code == 'X3'
    assert result.expected is None and result.correction is None and not result.operands


@pytest.mark.parametrize('source', [
    '2023年公司整体营业收入为1.20亿元，同比增长20%。',
    '公司提供云服务，2023年公司整体营业收入为1.20亿元，同比增长20%。',
    '华东地区服务布局完善，2023年公司整体营业收入为1.20亿元，同比增长20%。',
    '公司产品A服务华东地区，2023年公司整体营业收入为1.20亿元，同比增长20%。',
])
def test_explicit_total_and_plain_yoy_are_not_polluted_by_product_or_regional_information(source):
    claims = extract_mentions(source, '甲', '2023Y', preserve_occurrences=True)
    assert len(claims) == 2
    judge = Judge(_store(), '2024-04-22')
    assert [judge.verify(replace(c, account='REVENUE', binding_status='agreed')).status for c in claims] == ['verified', 'verified']


@pytest.mark.parametrize('qualifier', ['产品A', '云服务', '华东地区', '甲业务线'])
def test_same_sentence_segment_then_explicit_total_and_growth_reset_domain(qualifier):
    source = f'2023年{qualifier}营业收入为1.20亿元，公司整体营业收入为1.20亿元，同比增长20%。'
    claims = extract_mentions(source, '甲', '2023Y', preserve_occurrences=True)
    assert len(claims) == 3
    bound = [replace(c, account='REVENUE', binding_status='agreed') for c in claims]
    judge = Judge(_store(), '2024-04-22')
    assert judge.verify(bound[0]).abstention_code == 'X3'
    assert [judge.verify(c).status for c in bound[1:]] == ['verified', 'verified']


def test_repeated_total_and_generic_product_margin_do_not_share_domain():
    source = '2023年公司整体毛利率为26%，其中产品A毛利率为26%。'
    total, product = extract_mentions(source, '甲', '2023Y', preserve_occurrences=True)
    judge = Judge(_store(), '2024-04-22')
    assert judge.verify(replace(total, account='GROSS_MARGIN', binding_status='agreed')).status == 'verified'
    assert judge.verify(replace(product, account='GROSS_MARGIN', binding_status='agreed')).abstention_code == 'X3'
