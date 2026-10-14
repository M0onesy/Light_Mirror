"""A 部分验收：旁证探查不得改变 verdict（judge.py `_all_blockers` + `_probe_fact_lookup`）。

判据（对齐 ac-refactor-plan §1.4.1）：
1. 同一 claim，开启/关闭探查后 result 的全部 verdict 字段完全一致；
2. 原短路 blocker（lookup_status=='not_executed'）仍在；
3. 开启探查时新增 code=='PROBE' 且 kind=='diagnostic_probe' 的旁证 blocker；
4. 探查仅暴露布尔旁证，不落金额。

注意：被短路条目原本零事实查询，且既有测试禁止对无效来源查询金额
（tests/test_cagr_source_preconditions.py、tests/test_metric_coverage_registry.py）。
因此本测试显式以 enable_probe=True 打开探查，绝不在默认配置下触发查询。
"""
from dataclasses import replace

from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.source_semantics import freeze_source_dimensions

VERDICT_FIELDS = ('status', 'error_type', 'abstention_code', 'expected', 'observed',
                  'display_value', 'correction', 'operands', 'rule_ids', 'reason')


def _short_circuited_claim():
    """一个会被前置守卫短路（binding 未定）且公司/科目/期间齐备的 claim。"""
    return freeze_source_dimensions(extract_mentions('测试公司2023年合并营业收入100元。', '测试公司')[-1])


def test_probe_does_not_change_verdict_and_keeps_short_circuit_blocker():
    # binding 未定触发前置守卫短路；公司/科目/期间齐备，探查可执行。
    claim = replace(_short_circuited_claim(), binding_status='unknown')
    store = FactStore([])  # 空库：探查必然 missing，但结构不受影响

    off = Judge(store, '2024-04-22').verify(claim).to_dict()
    on = Judge(store, '2024-04-22', enable_probe=True).verify(claim).to_dict()

    # 1) verdict 字段逐字段一致（旁证零回流）
    for field in VERDICT_FIELDS:
        assert off.get(field) == on.get(field), field

    # 2) 原短路 blocker 仍在（默认与开启均存在）
    assert any(b.get('lookup_status') == 'not_executed' for b in off['blockers'])
    assert any(b.get('lookup_status') == 'not_executed' for b in on['blockers'])

    # 3) 默认配置绝不产生 PROBE 旁证
    assert not any(b.get('code') == 'PROBE' for b in off['blockers'])

    # 4) 开启配置新增 PROBE 旁证，且 kind 正确、携带 probe_status
    probe = [b for b in on['blockers'] if b.get('code') == 'PROBE']
    assert len(probe) == 1
    assert probe[0]['kind'] == 'diagnostic_probe'
    assert probe[0]['probe_status'] in {'found', 'missing', 'not_applicable'}

    # 5) 关闭时 blockers 是开启时 blockers 的子集（开启只增不减）
    assert all(item in on['blockers'] for item in off['blockers'])


def test_probe_found_never_leaks_amount():
    """探查命中时只暴露 fact_id / publish_date，绝不落金额。"""
    from decimal import Decimal
    from mingjing.models import Fact

    claim = replace(_short_circuited_claim(), account='REVENUE', binding_status='unknown')
    fact = Fact('revenue-2023Y', '测试公司', 'REVENUE', '2023Y', value=Decimal('100'),
                scope='合并', publish_date='2024-04-15', source_doc='report',
                evidence=('report:p1:cell',), decimals=2)
    store = FactStore([fact])

    result = Judge(store, '2024-04-22', enable_probe=True).verify(claim).to_dict()
    probe = [b for b in result['blockers'] if b.get('code') == 'PROBE']
    assert probe, '开启探查后应存在 PROBE 旁证'
    item = probe[0]
    assert 'probe_fact_id' in item and item['probe_fact_id'] == 'revenue-2023Y'
    # 金额字段绝不出现在旁证中
    assert 'value' not in item and 'probe_value' not in item
