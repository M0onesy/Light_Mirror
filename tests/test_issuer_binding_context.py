"""Regression boundaries for the recorded 07 issuer-only A/B disagreement.

The anonymized observations are development evidence, not independent human
gold. All new executions are controlled host tests, with no model/API call.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from mingjing.facts import FactStore
from mingjing.financial_tools import FinancialDispatcher, ToolDenied
from mingjing.judge import Judge, _source_scope_issue
from mingjing.mentions import extract_mentions
from mingjing.models import AtomicClaim
from mingjing.scope import create_scope_clarification, scope_clarification_metadata
from mingjing.source_semantics import freeze_source_dimensions
from test_agent_lifecycle import lifecycle, new_agent


RECORDED = Path(__file__).parent / 'fixtures' / 'issuer_context_web07_anonymized.json'
RECORDED_CASES = json.loads(RECORDED.read_text(encoding='utf-8'))['cases']


def dispatcher_for(claim, *, task_company='甲公司'):
    dispatcher = FinancialDispatcher('issuer-context', 'frozen-lock', [claim], [], [], [],
                                     '2024-04-22', task_company=task_company)
    for role in ('planner', 'verifier', 'coordinator'):
        dispatcher.register_session(role, role)
    return dispatcher


def call(dispatcher, role, name, params, call_id):
    return dispatcher.dispatch({'run_id': dispatcher.run_id, 'resource_lock': dispatcher.resource_lock,
        'session_id': role, 'role': role, 'call_id': call_id, 'tool_name': name, 'params': params})


def no_fact_reads(monkeypatch, store):
    def denied(*args, **kwargs):
        raise AssertionError('Unbound or unsupported source must not consume a fact value')
    for method in ('query', 'query_outcome', 'versions'):
        monkeypatch.setattr(store, method, denied)


@pytest.mark.parametrize('record', RECORDED_CASES, ids=lambda row: row['claim']['claim_id'])
def test_recorded_07_issuer_only_disagreement_keeps_A_empty_and_reports_true_reason(record, monkeypatch):
    provenance = json.loads(RECORDED.read_text(encoding='utf-8'))['provenance']
    assert provenance['label_status'] == 'development_regression_not_independent_human_gold'
    original = AtomicClaim(**deepcopy(record['claim']))
    proof = original.table_context['source_dimensions']
    assert hashlib.sha256(original.text.encode('utf-8')).hexdigest() == proof['source_text_sha256']
    assert original.text[original.source_numeric_start:original.source_numeric_start+len(original.numeric_text)] == original.numeric_text
    assert original.caliber_hint[0] == '未知'
    assert record['binding_A']['company'] == '' and record['binding_B']['company'] == '甲公司'
    assert record['original_agreement']['differences'] == ['company']
    assert record['original_agreement']['scope_source_proven'] is True
    assert '报表口径冲突' in record['original_result']['reason']
    dispatcher = dispatcher_for(original)
    for role, key in (('planner', 'binding_A'), ('verifier', 'binding_B')):
        call(dispatcher, role, 'mj_submit_binding', {'mention_id': original.claim_id,
             'binding': deepcopy(record[key])}, 'recorded-submit')
    bound = dispatcher.freeze_bindings()[0]
    assert dispatcher.bindings['planner'][bound.claim_id]['company'] == ''
    assert dispatcher.bindings['verifier'][bound.claim_id]['company'] == '甲公司'
    assert dispatcher.agreements[bound.claim_id] == record['original_agreement']
    assert list(bound.caliber_hint) == ['未知', '未扣非']
    assert bound.binding_status == 'disagreed' and bound.candidate_value == original.candidate_value
    no_fact_reads(monkeypatch, dispatcher.store)
    result = Judge(dispatcher.store, '2024-04-22', require_dual_binding=True).verify(bound)
    assert result.status == 'abstained' and result.abstention_code == 'X3'
    assert result.reason == '语义绑定未知或双盲绑定不一致'
    assert '报表口径冲突' not in result.reason
    with pytest.raises(ToolDenied, match='no consistent binding'):
        call(dispatcher, 'coordinator', 'mj_get_fact', {'binding_id': bound.claim_id}, 'forbidden-fact')


@pytest.mark.parametrize('subject', ['', '公司', '本公司', '该公司', '集团'])
def test_omitted_or_company_pronoun_can_use_frozen_issuer_context(subject):
    claim = extract_mentions(subject+'2023年合并营业收入98765元。', '甲公司', '2023Y')[0]
    dispatcher = dispatcher_for(claim)
    context = dispatcher.mention_context(dispatcher.claims[claim.claim_id])
    assert context['issuer_context'] == {'company': '甲公司', 'provenance': 'host_locked_issuer_context',
        'authority': 'frozen_task_company', 'source_resource_id': 'source-dimensions', 'applicable': True}
    assert context['source_dimensions']['company_source_explicit'] is False
    assert context['source_dimensions']['explicit']['company'] is False
    assert not dispatcher.bindings['planner'] and not dispatcher.bindings['verifier']


@pytest.mark.parametrize('subject', ['甲公司', '乙公司', '甲公司与乙公司', '本公司的子公司',
                                    '甲公司的关联公司', '某未知主体'])
def test_explicit_other_parallel_related_or_unknown_source_does_not_authorize_host_replacement(subject):
    claim = extract_mentions(subject+'2023年合并营业收入98765元。', '甲公司', '2023Y')[0]
    dispatcher = dispatcher_for(claim)
    context = dispatcher.mention_context(dispatcher.claims[claim.claim_id])
    assert context['issuer_context']['applicable'] is False
    assert context['issuer_context']['company'] == ''
    if subject == '甲公司':
        assert context['source_dimensions']['company_source_explicit'] is True
        assert context['source_dimensions']['company_provenance'] == 'explicit_source_subject'
    else:
        assert context['source_dimensions'].get('source_company_conflict') or context['source_dimensions'].get('source_company_ambiguous')


@pytest.mark.parametrize('change', [{'company': ''}, {'company': '乙公司'},
    {'company_provenance': 'explicit_source_subject'}, {'source_company_conflict': True},
    {'source_company_ambiguous': True}])
def test_locked_identity_or_provenance_mismatch_and_source_flags_disable_context(change):
    frozen = freeze_source_dimensions(extract_mentions('2023年合并营业收入98765元。', '甲公司')[0])
    metadata = deepcopy(frozen.table_context)
    metadata['source_dimensions'].update(change)
    dispatcher = dispatcher_for(replace(frozen, table_context=metadata))
    assert dispatcher.mention_context(next(iter(dispatcher.claims.values())))['issuer_context']['applicable'] is False


def test_task_company_mismatch_and_unselected_task_never_authorize_context():
    frozen = freeze_source_dimensions(extract_mentions('2023年合并营业收入98765元。', '甲公司')[0])
    for claim, task_company in ((frozen, '乙公司'), (replace(frozen, company=''), '')):
        dispatcher = dispatcher_for(claim, task_company=task_company)
        assert dispatcher.mention_context(next(iter(dispatcher.claims.values())))['issuer_context']['applicable'] is False


@pytest.mark.parametrize('record', RECORDED_CASES, ids=lambda row: row['claim']['claim_id'])
def test_binders_receive_identical_amount_free_context_before_and_after_A_submission(record):
    claim = AtomicClaim(**deepcopy(record['claim']))
    dispatcher = dispatcher_for(claim)
    stored = dispatcher.claims[claim.claim_id]
    original = deepcopy(stored.to_dict())
    before_A = call(dispatcher, 'planner', 'mj_read_context', {'mention_id': claim.claim_id}, 'context-a')
    before_B = call(dispatcher, 'verifier', 'mj_read_context', {'mention_id': claim.claim_id}, 'context-b')
    assert before_A == before_B and before_A['issuer_context']['applicable'] is True
    call(dispatcher, 'planner', 'mj_submit_binding', {'mention_id': claim.claim_id,
         'binding': deepcopy(record['binding_A'])}, 'submit-a-empty')
    after_B = call(dispatcher, 'verifier', 'mj_read_context', {'mention_id': claim.claim_id}, 'context-b-after-a')
    assert after_B == before_B
    assert dispatcher.bindings['planner'][claim.claim_id]['company'] == ''
    assert not dispatcher.bindings['verifier']
    for context in (before_A, before_B, after_B):
        payload = json.dumps(context, ensure_ascii=False)
        assert not any(word in payload for word in ('candidate_value', 'binding_A', 'bindings', 'history'))
        for amount in ('-114', '-80', '-37'):
            assert amount not in context['text']
        assert context['table_context']['headers'][0]['text'] == '2022A'
        assert context['table_context']['headers'][1]['text'] == '2023A'
    assert stored.to_dict() == original


def test_eligible_context_masks_nested_candidate_and_neighbor_amounts_without_mutating_source():
    frozen = freeze_source_dimensions(extract_mentions('2023年合并营业收入4321元。', '甲公司')[0])
    metadata = deepcopy(frozen.table_context)
    metadata['nested_source'] = {'labels': ['营业收入4321元', '归母净利润98765元']}
    metadata['source_dimensions']['source_company_subjects'] = ['营业收入4321元与净利润98765元']
    frozen = replace(frozen, table_context=metadata)
    dispatcher = dispatcher_for(frozen)
    before = deepcopy(dispatcher.claims[frozen.claim_id].to_dict())
    for role in ('planner', 'verifier'):
        for name, params in [('mj_list_mentions', {}), ('mj_read_context', {'mention_id': frozen.claim_id}),
                             ('mj_fact_dimensions', {'mention_id': frozen.claim_id})]:
            payload = json.dumps(call(dispatcher, role, name, params, name), ensure_ascii=False)
            assert '4321' not in payload and '98765' not in payload and 'candidate_value' not in payload
    assert dispatcher.mention_context(dispatcher.claims[frozen.claim_id])['issuer_context']['applicable'] is True
    assert dispatcher.claims[frozen.claim_id].to_dict() == before


@pytest.mark.parametrize('status', ['unknown', 'unresolved', 'disagreed', 'pending'])
def test_nonagreed_source_proven_parent_profit_ignores_only_stale_caliber_hint(status, monkeypatch):
    claim = AtomicClaim(**deepcopy(RECORDED_CASES[0]['claim']))
    claim = replace(claim, binding_status=status)
    store = FactStore([])
    no_fact_reads(monkeypatch, store)
    assert _source_scope_issue(claim, strict=True) is None
    result = Judge(store, '2024-04-22', require_dual_binding=True).verify(claim)
    assert result.abstention_code == 'X3' and result.reason == '语义绑定未知或双盲绑定不一致'


@pytest.mark.parametrize('status', ['unknown', 'unresolved', 'disagreed', 'pending'])
def test_missing_source_scope_remains_specific_X3_despite_eligible_company_context(status, monkeypatch):
    claim = extract_mentions('2023年营业收入4321元。', '甲公司')[0]
    claim = replace(claim, binding_status=status, caliber_hint=('未知', '未扣非'))
    dispatcher = dispatcher_for(claim)
    stored = dispatcher.claims[claim.claim_id]
    assert dispatcher.mention_context(stored)['issuer_context']['applicable'] is True
    no_fact_reads(monkeypatch, dispatcher.store)
    issue = _source_scope_issue(stored, strict=True)
    assert issue and issue[0] == 'X3' and '原文未唯一声明' in issue[1]
    result = Judge(dispatcher.store, '2024-04-22', require_dual_binding=True).verify(stored)
    assert result.abstention_code == 'X3' and result.reason == issue[1]


@pytest.mark.parametrize('status', ['agreed', 'single'])
def test_committed_wrong_scope_remains_blocked(status, monkeypatch):
    claim = AtomicClaim(**deepcopy(RECORDED_CASES[0]['claim']))
    claim = replace(claim, binding_status=status, caliber_hint=('母公司', '未扣非'))
    store = FactStore([])
    no_fact_reads(monkeypatch, store)
    issue = _source_scope_issue(claim, strict=True)
    assert issue and issue[0] == 'X3' and '报表口径冲突' in issue[1]
    result = Judge(store, '2024-04-22', require_dual_binding=(status == 'agreed')).verify(claim)
    assert result.abstention_code == 'X3' and '报表口径冲突' in result.reason


@pytest.mark.parametrize('status', ['unknown', 'disagreed', 'agreed', 'single'])
def test_user_source_scope_declaration_conflict_is_checked_before_binding_status(status, monkeypatch):
    claim = extract_mentions('2023年母公司营业收入4321元。', '甲公司')[0]
    resource = create_scope_clarification('合并', draft_sha256='a' * 64)
    metadata = dict(claim.table_context, scope_declaration=scope_clarification_metadata(resource, 'b' * 64))
    claim = replace(claim, table_context=metadata, binding_status=status)
    store = FactStore([])
    no_fact_reads(monkeypatch, store)
    issue = _source_scope_issue(claim, strict=True)
    assert issue and issue[0] == 'X3' and '用户口径澄清与原文明示口径冲突' in issue[1]
    result = Judge(store, '2024-04-22', require_dual_binding=True).verify(claim)
    assert result.abstention_code == 'X3' and result.reason == issue[1]


def test_controlled_binding_stage_gives_both_roles_same_company_authority_without_scope_default(lifecycle, monkeypatch):
    # Capture the real orchestration stage; intentionally do not open a
    # native runtime or pretend these instructions prove model behavior.
    owner = new_agent(lifecycle)
    owner.prepare()
    received = []

    def capture(role, group, instructions, prompt, stop_event=None):
        received.append({'role': role, 'group': list(group), 'instructions': instructions, 'prompt': prompt})

    monkeypatch.setattr(owner, '_bind_role_group', capture)
    owner.bind()
    assert len(received) == 2 and {row['role'] for row in received} == {'planner', 'verifier'}
    assert received[0]['group'] == received[1]['group']
    assert received[0]['instructions'] == received[1]['instructions']
    assert received[0]['prompt'] == received[1]['prompt']
    prompt = received[0]['prompt']
    assert 'company_source_explicit及explicit.company仅表示当前句是否直接写出公司名称' in prompt
    assert 'issuer_context.applicable=true时' in prompt and '仍须独立审阅并自行提交' in prompt
    assert 'applicable=false时依据原文主体' in prompt
    assert '公司上下文可用不代表scope可以默认' in prompt
    assert not owner.dispatcher.bindings['planner'] and not owner.dispatcher.bindings['verifier']
    assert lifecycle.boundary.provider_work == 0 and not lifecycle.boundary.instances


@pytest.mark.parametrize('task_company', ['360公司', '177公司'])
@pytest.mark.parametrize('subject', ['', '公司'])
def test_numeric_issuer_name_remains_exact_in_shared_context_while_all_financial_amounts_are_blinded(task_company, subject):
    text = subject+'2023年合并营业收入98765.43元，归母净利润12345.67元。'
    claim = extract_mentions(text, task_company, '2023Y')[0]
    dispatcher = dispatcher_for(claim, task_company=task_company)
    frozen_before = deepcopy(dispatcher.claims[claim.claim_id].to_dict())
    before_A = call(dispatcher, 'planner', 'mj_read_context', {'mention_id': claim.claim_id}, 'numeric-context-a')
    before_B = call(dispatcher, 'verifier', 'mj_read_context', {'mention_id': claim.claim_id}, 'numeric-context-b')
    assert before_A == before_B
    assert before_A['issuer_context']['company'] == task_company
    assert before_A['issuer_context']['applicable'] is True
    binding = {'company': task_company, 'account': 'REVENUE', 'period': '2023Y',
               'scope': '合并', 'relation': 'value', 'base_period': ''}
    call(dispatcher, 'planner', 'mj_submit_binding', {'mention_id': claim.claim_id,
         'binding': binding}, 'numeric-submit-a')
    after_B = call(dispatcher, 'verifier', 'mj_read_context', {'mention_id': claim.claim_id}, 'numeric-context-b-after-a')
    assert after_B == before_B
    for context in (before_A, before_B, after_B):
        assert context['issuer_context']['company'] == context['company_hint'] == task_company
        payload = json.dumps(context, ensure_ascii=False)
        assert '98765.43' not in payload and '12345.67' not in payload
        assert 'candidate_value' not in payload and 'history' not in payload and 'bindings' not in payload
    assert dispatcher.bindings['planner'][claim.claim_id] == binding
    assert not dispatcher.bindings['verifier']
    assert dispatcher.claims[claim.claim_id].to_dict() == frozen_before
