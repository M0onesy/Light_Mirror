"""Known source conflicts remain repairable before an atomic A/B commit."""
from __future__ import annotations

import copy
from dataclasses import replace
import json

import pytest

from mingjing.financial_tools import FinancialArgumentError
from mingjing.mentions import extract_mentions
from test_binding_argument_contract import binding, bridge, invoke


@pytest.mark.parametrize('changed', [
    {'account': 'COST'}, {'period': '2022Y'}, {'scope': '母公司'},
    {'relation': 'yoy', 'base_period': '2022Y'}, {'base_period': '2022Y'},
])
def test_known_source_conflict_is_visible_to_same_native_role_and_can_be_repaired(tmp_path, changed):
    owner, claim = bridge(tmp_path)
    source_before = copy.deepcopy(owner.dispatcher.claims[claim.claim_id].to_dict())
    original_value = claim.candidate_value
    source_reply = invoke(owner, 'planner', 'mj_submit_binding', {
        'mention_id': claim.claim_id, 'binding': {**binding(claim), **changed}}, 'wrong-source')
    assert source_reply['status'] == 'rejected' and source_reply['accepted'] is False
    error = source_reply['error']
    assert error['code'] == 'binding_source_conflict'
    assert set(error['conflicting_fields']) >= set(changed)
    assert error['source_dimensions']['account'] == 'REVENUE'
    assert error['source_dimensions']['period'] == '2024Y'
    assert error['source_dimensions']['scope'] == '合并'
    assert not any(key in json.dumps(error) for key in ('candidate_value', 'expected', 'fact_value', 'binding_A', 'binding_B'))
    assert original_value not in json.dumps(error)
    assert not owner.dispatcher.bindings['planner'] and not owner.dispatcher.bindings['verifier']
    assert owner.dispatcher.trace[-1]['status'] == 'denied'
    assert set(owner.dispatcher.trace[-1]['input']) == {'input_sha256'}
    # The role can submit a correction in the very same session. No new run,
    # source mutation or host-completed A/B binding is necessary.
    assert invoke(owner, 'planner', 'mj_submit_binding', {
        'mention_id': claim.claim_id, 'binding': binding(claim)}, 'corrected-source')['accepted']
    assert not owner.dispatcher.bindings['verifier']
    assert owner.dispatcher.claims[claim.claim_id].to_dict() == source_before


def test_known_cagr_base_period_conflict_returns_original_periods_without_amounts(tmp_path):
    claim = extract_mentions('甲公司2018-2023年合并营业收入CAGR为41%。', '甲公司')[-1]
    owner, claim = bridge(tmp_path, claim=claim)
    valid = {**binding(claim), 'relation': 'cagr', 'base_period': '2018Y'}
    answer = invoke(owner, 'planner', 'mj_submit_binding', {
        'mention_id': claim.claim_id, 'binding': {**valid, 'base_period': '2019Y'}}, 'wrong-base')
    assert answer['error']['conflicting_fields'] == ['base_period']
    assert answer['error']['source_dimensions']['base_period'] == '2018Y'
    assert '41' not in json.dumps(answer)
    assert invoke(owner, 'planner', 'mj_submit_binding', {
        'mention_id': claim.claim_id, 'binding': valid}, 'repaired-base')['accepted']


def test_batch_validates_every_member_before_any_binding_is_committed(tmp_path):
    owner, claim = bridge(tmp_path)
    second = replace(claim, claim_id='second-original')
    owner.dispatcher.claims[second.claim_id] = second
    owner.dispatcher.sessions['planner']['mentions'].add(second.claim_id)
    batch = {'bindings': [
        {'mention_id': claim.claim_id, 'binding': binding(claim)},
        {'mention_id': second.claim_id, 'binding': {**binding(second), 'account': 'COST'}},
    ]}
    reply = invoke(owner, 'planner', 'mj_submit_bindings', batch, 'wrong-batch')
    assert reply['error']['code'] == 'binding_source_conflict'
    assert not owner.dispatcher.bindings['planner']
    batch['bindings'][1]['binding'] = binding(second)
    assert invoke(owner, 'planner', 'mj_submit_bindings', batch, 'repair-batch')['accepted']
    assert set(owner.dispatcher.bindings['planner']) == {claim.claim_id, second.claim_id}


def test_unknown_source_methods_accept_unknown_submission_and_keep_unknown_at_freeze(tmp_path):
    claim = extract_mentions('甲公司2023年合并口径ROE为12%。', '甲公司')[-1]
    assert claim.account.endswith('_UNSPECIFIED')
    owner, claim = bridge(tmp_path, claim=claim)
    for role in ('planner', 'verifier'):
        args = {**binding(claim), 'account': '', 'relation': '', 'scope': ''}
        assert invoke(owner, role, 'mj_submit_binding', {
            'mention_id': claim.claim_id, 'binding': args}, 'leave-unknown')['accepted']
    frozen = owner.dispatcher.freeze_bindings()[0]
    assert frozen.binding_status == 'disagreed'


def test_rejection_has_no_opposite_role_history_or_injected_model_text(tmp_path):
    owner, claim = bridge(tmp_path)
    assert invoke(owner, 'planner', 'mj_submit_binding', {
        'mention_id': claim.claim_id, 'binding': binding(claim)}, 'planner-ok')['accepted']
    before = copy.deepcopy(owner.dispatcher.bindings['planner'])
    marker = 'untrusted-input-do-not-echo'
    answer = invoke(owner, 'verifier', 'mj_submit_binding', {
        'mention_id': claim.claim_id, 'binding': {**binding(claim), 'period': marker}}, 'verifier-wrong')
    raw = json.dumps(answer)
    assert marker not in raw and 'planner' not in raw and 'verifier' not in raw
    assert owner.dispatcher.bindings['planner'] == before and not owner.dispatcher.bindings['verifier']


def test_source_conflict_exception_attributes_do_not_enter_public_repair_message(tmp_path):
    owner, claim = bridge(tmp_path)
    with pytest.raises(FinancialArgumentError) as caught:
        owner.dispatcher.validated_binding(owner.dispatcher.sessions['planner'], 'planner', {
            'mention_id': claim.claim_id, 'binding': {**binding(claim), 'account': 'COST'}})
    expected = caught.value.rejection()
    caught.value.args = ('secret-in-exception',)
    caught.value.code = 'secret-in-code'
    caught.value.field = 'secret-in-field'
    assert caught.value.rejection() == expected
    assert 'secret' not in json.dumps(caught.value.rejection())
