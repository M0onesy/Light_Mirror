from decimal import Decimal
from dataclasses import replace

import pytest

from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.source_semantics import freeze_source_dimensions, semantic_review_required


def test_management_ratio_year_comparison_is_not_expense_growth(monkeypatch):
    source = ('整体费用率的下降主要由管理费用率的下降驱动，管理费用率由2016\n'
              '年的116.79%下降至2023 年的9.66%。')
    claims = extract_mentions(source, '甲公司')
    assert len(claims) == 2
    assert [c.period for c in claims] == ['2016Y', '2023Y']
    assert all(c.account == 'ADMIN_EXPENSE_RATIO' and c.relation == 'value' for c in claims)
    assert all(c.parse_status == 'parsed' and c.base_period is None and not semantic_review_required(c) for c in claims)
    assert all(c.table_context['definition_id'] == 'metric:admin-expense-ratio' for c in claims)
    assert [Decimal(c.candidate_value) for c in claims] == [Decimal('1.1679'), Decimal('.0966')]
    store = FactStore([Fact('a', '甲公司', 'EXP_ADMIN', '2023Y', value=Decimal('9.66'))])
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Missing source scope cannot be guessed from expense amounts')
    for method in ('query', 'query_outcome', 'versions'):
        monkeypatch.setattr(store, method, forbidden)
    assert all(Judge(store).verify(freeze_source_dimensions(c)).abstention_code == 'X3' for c in claims)
    for claim in claims:
        original = freeze_source_dimensions(claim)
        rebound = replace(original, account='EXP_ADMIN', relation='yoy', base_period='2022Y', binding_status='agreed')
        result = Judge(store, '2024-04-22', require_dual_binding=True).verify(rebound)
        assert result.abstention_code == 'X3' and result.status == 'abstained'
        assert result.expected is None and not result.operands and not result.evidence


@pytest.mark.parametrize('caption,account,expense_account', [
    ('期间费用率', 'EXP_PERIOD_RATIO_UNSPECIFIED', 'EXP_PERIOD'),
    ('销售费用率', 'SELLING_EXPENSE_RATIO', 'EXP_SELLING'),
    ('研发费用率', 'RD_EXPENSE_RATIO', 'EXP_RD'),
    ('财务费用率', 'FINANCE_EXPENSE_RATIO', 'EXP_FINANCE')])
def test_ratio_captions_use_source_registry_and_cannot_be_rebound_to_expense_growth(caption, account, expense_account, monkeypatch):
    claim = extract_mentions(f'甲公司2023年{caption}为9.66%。', '甲公司')[0]
    assert claim.account == account and claim.relation == 'value'
    assert claim.parse_status == 'parsed'
    unresolved = account == 'EXP_PERIOD_RATIO_UNSPECIFIED'
    assert semantic_review_required(claim) is unresolved
    if unresolved:
        assert claim.table_context['source_account_unresolved'] == account
    else:
        assert claim.table_context['definition_id'].startswith('metric:')
    assert claim.unit == '%' and Decimal(claim.candidate_value) == Decimal('.0966')
    # If the ratio were wrongly turned into expense growth, these amounts
    # would produce the exact same 9.66% rate. Source guards must run first.
    store = FactStore([
        Fact('current', '甲公司', expense_account, '2023Y', value=Decimal('10.966'), publish_date='2024-04-01'),
        Fact('base', '甲公司', expense_account, '2022Y', value=Decimal('10'), publish_date='2023-04-01')])
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Numerically matching expense growth is not the source-defined expense ratio')
    for method in ('query', 'query_outcome', 'versions'):
        monkeypatch.setattr(store, method, forbidden)
    frozen = freeze_source_dimensions(claim)
    for relation in ('value', 'yoy'):
        rebound = replace(frozen, account=expense_account, relation=relation, base_period='2022Y', binding_status='agreed')
        result = Judge(store, '2024-04-22', require_dual_binding=True).verify(rebound)
        assert result.status == 'abstained' and result.abstention_code == ('X2' if unresolved else 'X3')
        assert result.expected is None and not result.evidence and not result.operands


@pytest.mark.parametrize('caption,account,rule_id', [
    ('销售费用率', 'EXP_SELLING', 'FIN-selling-expense-ratio'),
    ('管理费用率', 'EXP_ADMIN', 'FIN-admin-expense-ratio'),
    ('研发费用率', 'EXP_RD', 'FIN-rd-expense-ratio'),
    ('财务费用率', 'EXP_FINANCE', 'FIN-finance-expense-ratio')])
def test_registered_expense_ratio_uses_revenue_not_matching_expense_growth(caption, account, rule_id):
    claim = freeze_source_dimensions(extract_mentions(f'甲公司2023年合并口径{caption}为9.66%。', '甲公司')[0])
    store = FactStore([
        Fact('expense', '甲公司', account, '2023Y', value=Decimal('10.966'), publish_date='2024-04-01', source_doc='annual', evidence=('annual:expense',), decimals=3),
        Fact('matching-growth-base', '甲公司', account, '2022Y', value=Decimal('10'), publish_date='2024-04-01', source_doc='annual', evidence=('annual:prior-expense',)),
        Fact('revenue', '甲公司', 'REVENUE', '2023Y', value=Decimal('100'), publish_date='2024-04-01', source_doc='annual', evidence=('annual:revenue',)),
    ])
    result = Judge(store, '2024-04-22').verify(claim)
    assert result.status == 'violated' and result.error_type == 'E5'
    assert Decimal(result.expected) == Decimal('.10966')
    assert result.rule_ids == [rule_id] and result.formula == account + '/REVENUE'
    assert {item['fact_id'] for item in result.operands} == {'expense', 'revenue'}
    assert set(result.evidence) == {'annual:expense', 'annual:revenue'}


def test_expense_amount_growth_still_uses_expense_account():
    source = '甲公司2023年管理费用为100万元，同比下降9.66%。'
    claims = extract_mentions(source, '甲公司')
    assert claims[0].account == 'EXP_ADMIN' and claims[0].relation == 'value'
    assert claims[1].account == 'EXP_ADMIN' and claims[1].relation == 'yoy'
    assert Decimal(claims[1].candidate_value) == Decimal('-0.0966')


def test_margin_lowered_to_another_year_value_is_not_year_on_year_growth():
    claims = extract_mentions('甲公司毛利率由2016年的30%下降至2023年的26%。', '甲公司')
    assert len(claims) == 2 and all(c.account == 'GROSS_MARGIN' and c.relation == 'value' for c in claims)
    assert [c.period for c in claims] == ['2016Y', '2023Y']
    assert [c.candidate_value for c in claims] == ['0.30', '0.26']
