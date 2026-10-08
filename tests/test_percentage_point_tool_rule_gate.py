"""Actual Agent tool reads, not a prompt or unrelated rule, authorize deltas."""
from decimal import Decimal

import pytest

from mingjing.financial_tools import FinancialDispatcher, ToolDenied
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact


def fixture():
    text = ('甲公司2023年合并口径毛利率较2022年上升5.0个百分点。'
            '甲公司2023年合并营业收入100万元。')
    claims = extract_mentions(text, '甲公司')
    facts = [Fact('margin-now', '甲公司', 'GROSS_MARGIN', '2023Y', value=Decimal('.25'),
                  display_unit='%', decimals=2, source_doc='annual', publish_date='2024-04-01'),
             Fact('margin-base', '甲公司', 'GROSS_MARGIN', '2022Y', value=Decimal('.20'),
                  display_unit='%', decimals=2, source_doc='annual', publish_date='2024-04-01'),
             Fact('rev', '甲公司', 'REVENUE', '2023Y', value=Decimal('1000000'),
                  source_doc='annual', publish_date='2024-04-01')]
    d = FinancialDispatcher('run', 'lock', claims, facts, [], [], '2024-04-22')
    for role in ('planner', 'verifier', 'coordinator'):
        d.register_session(role, role)
    for role in ('planner', 'verifier'):
        for c in claims:
            call(d, role, 'mj_submit_binding', {'mention_id': c.claim_id, 'binding': {
                'company': c.company, 'account': c.account, 'period': c.period, 'scope': '合并',
                'relation': c.relation, 'base_period': c.base_period or ''}}, f'{role}-{c.claim_id}')
    assert all(c.binding_status == 'agreed' for c in d.freeze_bindings())
    return d, claims


def call(d, role, name, params, id, *, session=None):
    return d.dispatch({'session_id': session or role, 'role': role, 'run_id': 'run', 'resource_lock': 'lock',
                       'call_id': id, 'tool_name': name, 'params': params})


@pytest.mark.parametrize('read_facts,read_delta', [(False, False), (True, False), (False, True)])
def test_singular_delta_requires_actual_fact_and_correct_rule_reads_in_same_session(read_facts, read_delta):
    d, claims = fixture()
    mid = claims[0].claim_id
    if read_facts:
        result = call(d, 'coordinator', 'mj_get_fact', {'binding_id': mid}, 'facts')
        assert result['required_rule_ids'] == ['FIN-pp-difference']
        assert result['query_dimensions'] == {'company': '甲公司', 'account': 'GROSS_MARGIN', 'period': '2023Y',
            'scope': '合并', 'relation': 'pp_difference', 'base_period': '2022Y',
            'dimension_kind':'total','dimension_id':'','currency':'CNY','definition_id':''}
        assert not {'candidate_value', 'expected', 'observed', 'formula', 'verdict'} & set(result['query_dimensions'])
    rule = 'FIN-pp-difference' if read_delta else 'E1-value'
    call(d, 'coordinator', 'mj_get_rule', {'rule_id': rule}, 'rule')
    with pytest.raises(ToolDenied, match='FIN-pp-difference'):
        call(d, 'coordinator', 'mj_verify', {'binding_id': mid}, 'rejected')
    assert d.trace[-1]['status'] == 'denied'
    if not read_facts:
        call(d, 'coordinator', 'mj_get_fact', {'binding_id': mid}, 'repair-facts')
    if not read_delta:
        call(d, 'coordinator', 'mj_get_rule', {'rule_id': 'FIN-pp-difference'}, 'repair-rule')
    result = call(d, 'coordinator', 'mj_verify', {'binding_id': mid}, 'repaired')
    assert result['status'] == 'verified' and result['rule_ids'] == ['FIN-pp-difference']
    assert d.trace[-1]['status'] == 'success'


def test_batch_prechecks_all_required_rules_before_any_kernel_judgment(monkeypatch):
    d, claims = fixture()
    # Put the unrelated already-authorized amount first. The later delta
    # must fail preflight before even that first judgment is executed.
    ids = [claims[1].claim_id, claims[0].claim_id]
    reads = call(d, 'coordinator', 'mj_get_facts', {'binding_ids': ids}, 'facts')
    assert reads['items'][0]['required_rule_ids'] == ['E1-value']
    assert reads['items'][1]['required_rule_ids'] == ['FIN-pp-difference']
    call(d, 'coordinator', 'mj_get_rule', {'rule_id': 'E1-value'}, 'wrong-rule')
    actual = Judge.verify
    verified = []
    def spy(self, claim):
        verified.append(claim.claim_id)
        return actual(self, claim)
    monkeypatch.setattr(Judge, 'verify', spy)
    with pytest.raises(ToolDenied, match='FIN-pp-difference'):
        call(d, 'coordinator', 'mj_verify_bindings', {'binding_ids': ids}, 'batch-rejected')
    assert verified == []
    call(d, 'coordinator', 'mj_get_rule', {'rule_id': 'FIN-pp-difference'}, 'repair-rule')
    result = call(d, 'coordinator', 'mj_verify_bindings', {'binding_ids': ids}, 'batch-repaired')
    assert verified == ids and all(item['status'] == 'verified' for item in result['items'])


def test_second_session_cannot_inherit_fact_and_delta_rule_reads_from_first():
    d, claims = fixture()
    mid = claims[0].claim_id
    call(d, 'coordinator', 'mj_get_fact', {'binding_id': mid}, 'facts')
    call(d, 'coordinator', 'mj_get_rule', {'rule_id': 'FIN-pp-difference'}, 'rule')
    d.register_session('other', 'coordinator')
    with pytest.raises(ToolDenied, match='FIN-pp-difference'):
        call(d, 'coordinator', 'mj_verify', {'binding_id': mid}, 'other-rejected', session='other')
    call(d, 'coordinator', 'mj_get_fact', {'binding_id': mid}, 'other-facts', session='other')
    call(d, 'coordinator', 'mj_get_rule', {'rule_id': 'FIN-pp-difference'}, 'other-rule', session='other')
    result = call(d, 'coordinator', 'mj_verify', {'binding_id': mid}, 'other-repaired', session='other')
    assert result['status'] == 'verified'
