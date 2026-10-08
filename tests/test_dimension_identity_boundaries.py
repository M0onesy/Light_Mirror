"""Dimension identity must be source-bound, not selected by matching amounts."""
from dataclasses import replace
from decimal import Decimal

import pytest

from mingjing.dimensions import dimension_ids_compatible
from mingjing.facts import FactStore, fact_from_dict
from mingjing.models import Fact


def source_fact(identifier, *, kind='customer', document='report-a', scope='合并', **extra):
    defaults = dict(fact_id=f'{document}-{identifier}', company='发行人甲', account='REVENUE',
                    period='2023Y', dimension_kind=kind, dimension_id=identifier, scope=scope,
                    value=Decimal('100.00'), source_doc=document, publish_date='2024-04-15',
                    evidence=(f'{document}:p2:row',))
    defaults.update(extra)
    return Fact(**defaults)


@pytest.mark.parametrize('kind,left,right', [
    ('customer', '三台县梓丰现代农业', '三台县梓丰现代农业发展有限公司'),
    ('customer', '北京甲科技有限公司', '北京甲科技有限责任公司'),
    ('customer', '北京甲科技', '北京甲科技有限公司'),
    ('customer', '中科院力学研究所', '中国科学院力学研究所'),
    ('business', '超算云服务', '超算云业务'),
    ('business', '超算云', '超算云业务'),
    ('purchase_mode', '外采', '外采算力资源'),
])
def test_unproved_global_aliases_and_prefixes_never_merge_same_amount(kind, left, right):
    assert not dimension_ids_compatible(kind, left, right)
    fact = source_fact(right, kind=kind)
    result = FactStore([fact]).query_outcome('发行人甲', 'REVENUE', '2023Y',
        dimension_kind=kind, dimension_id=left, strategy='pinned', source_doc='report-a')
    assert result.status == 'missing' and result.fact is None


def test_exact_named_identity_retains_layout_whitespace_normalization():
    fact = source_fact('青岛国实科技集团有限公司')
    assert dimension_ids_compatible('customer', '青岛国实科技集团有\n限公司', fact.dimension_id)
    assert FactStore([fact]).query('发行人甲', 'REVENUE', '2023Y',
        dimension_kind='customer', dimension_id='青岛国实科技集团有\n限公司') == fact
    assert FactStore([fact]).query('发行人乙', 'REVENUE', '2023Y',
        dimension_kind='customer', dimension_id=fact.dimension_id) is None


def test_anonymous_single_source_still_requires_a_source_bound_strategy():
    fact = source_fact('客户H')
    store = FactStore([fact])
    result = store.query_outcome('发行人甲', 'REVENUE', '2023Y',
        dimension_kind='customer', dimension_id='客户 H')
    assert result.status == 'source_required' and result.fact is None
    # Merely adding a source argument does not turn latest_as_of into a
    # declared-source strategy, or make unrelated document labels identical.
    assert store.query_outcome('发行人甲', 'REVENUE', '2023Y',
        dimension_kind='customer', dimension_id='客户H', source_doc='report-b').status == 'source_required'
    for strategy in ('pinned', 'same_report'):
        assert store.query('发行人甲', 'REVENUE', '2023Y', dimension_kind='customer',
            dimension_id='客户H', strategy=strategy, source_doc='report-a') == fact
        assert store.query('发行人甲', 'REVENUE', '2023Y', dimension_kind='customer',
            dimension_id='客户H', strategy=strategy, source_doc='report-b') is None


def test_equal_anonymous_values_in_different_documents_cannot_be_combined():
    first = source_fact('客户E')
    second = replace(first, fact_id='second', source_doc='report-b', evidence=('report-b:p3:row',))
    store = FactStore([first, second])
    result = store.query_outcome('发行人甲', 'REVENUE', '2023Y',
        dimension_kind='customer', dimension_id='客户E')
    assert result.status == 'ambiguous_source' and result.fact is None
    assert set(result.candidate_fact_ids) == {first.fact_id, second.fact_id}


def test_unknown_scope_is_an_exact_dimension_and_never_becomes_consolidated():
    unknown = source_fact('清华大学', scope='未知')
    store = FactStore([unknown])
    dims = dict(dimension_kind='customer', dimension_id='清华大学',
                strategy='pinned', source_doc='report-a')
    assert store.query('发行人甲', 'REVENUE', '2023Y', scope='未知', **dims) == unknown
    assert store.query('发行人甲', 'REVENUE', '2023Y', scope='合并', **dims) is None
    assert store.query('发行人甲', 'REVENUE', '2023Y', scope='母公司', **dims) is None


def test_source_table_metadata_roundtrips_and_distinct_tables_are_retained():
    fact = source_fact('清华大学', source_table_id='table-one',
        source_table_evidence=('report-a:p2:header',), source_scope_evidence=('report-a:p1:scope',))
    assert fact_from_dict(fact.to_dict()) == fact
    legacy = fact.to_dict()
    for key in ('source_table_id', 'source_table_evidence', 'source_scope_evidence'):
        legacy.pop(key)
    restored = fact_from_dict(legacy)
    assert restored.source_table_id == ''
    assert restored.source_table_evidence == restored.source_scope_evidence == ()
    store = FactStore([fact, replace(fact, source_table_id='table-two')])
    assert len(store.facts) == 2
