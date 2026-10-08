"""CAGR source preconditions must be visible before dependent fact lookup."""
from dataclasses import replace
from decimal import Decimal

import pytest

from mingjing.facts import FactStore
from mingjing.judge import Judge, _source_semantic_issue
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.source_semantics import freeze_source_dimensions


@pytest.mark.parametrize('text', [
    '甲公司2024年营业收入CAGR为20%。',
    '甲公司2024-2024年营业收入CAGR为20%。',
    '甲公司2024-2023年营业收入CAGR为20%。',
    '甲公司2018-2023年、2019-2024年营业收入CAGR为20%。',
])
def test_missing_or_ambiguous_cagr_endpoints_list_all_independent_blockers(text, monkeypatch):
    claim = freeze_source_dimensions(extract_mentions(text, '甲公司')[-1])
    assert claim.relation == 'cagr'
    store = FactStore([])
    monkeypatch.setattr(store, 'query_outcome',
                        lambda *a, **k: pytest.fail('Invalid source must not query financial amounts'))
    result = Judge(store, '2025-03-31').verify(claim)
    assert result.status == 'abstained' and result.abstention_code == 'X2'
    assert any(item['code'] == 'X2' and item['kind'] == 'source_semantics' for item in result.blockers)
    assert any(item['code'] == 'X3' and item['kind'] == 'source_scope' for item in result.blockers)
    assert any(item.get('lookup_status') == 'not_executed' for item in result.blockers)
    assert result.expected is None and result.operands == []


def test_explicit_cagr_periods_remain_authorized_and_use_frozen_external_operands():
    claim = freeze_source_dimensions(replace(
        extract_mentions('甲公司2021-2023年合并营业收入CAGR为10%。', '甲公司')[-1],
        binding_status='agreed'))
    assert _source_semantic_issue(claim) is None
    facts = [Fact('revenue-' + period, '甲公司', 'REVENUE', period,
                  value=Decimal(value), scope='合并', publish_date='2024-04-15',
                  source_doc='annual', evidence=('annual:p1:revenue-' + period,), decimals=2)
             for period, value in [('2021Y', '100'), ('2023Y', '121')]]
    result = Judge(FactStore(facts), '2024-04-22').verify(claim)
    assert result.status == 'verified' and result.rule_ids == ['FIN-cagr']
    assert {item['fact_id'] for item in result.operands} == {'revenue-2021Y', 'revenue-2023Y'}
    assert Decimal(result.expected) == Decimal('0.1')
