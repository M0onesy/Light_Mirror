"""Public 04 regression and safe model repair; no live provider acceptance."""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import jsonschema
import pytest

from mingjing.agent import FinancialAgent
from mingjing.audit import AuditLog, digest
from mingjing.config import AppConfig
from mingjing.financial_tools import (
    BIND_ACCOUNTS, BIND_FIELDS, BIND_RELATIONS, BIND_SCOPES, FinancialArgumentError,
    FinancialDispatcher, TOOL_SCHEMAS, ToolDenied, tool_definitions,
)
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import AtomicClaim, Fact


RECORDED = Path(__file__).parent/'fixtures/binding_account_rejection_04.json'


def bridge(tmp_path, *, claim=None, secret_values=None):
    claim = claim or extract_mentions('甲公司2024年合并营业收入100元。', '甲公司')[0]
    fact = Fact('registered-fact', claim.company, 'REVENUE', claim.period,
                value=Decimal(claim.candidate_value or '100'), publish_date='2025-03-01')
    owner = FinancialAgent({}, AppConfig().to_dict(), lambda _: '', tmp_path, run_id='argument-regression')
    owner.resource_lock = 'trusted-lock'
    owner.audit = AuditLog(tmp_path, owner.run_id)
    owner.dispatcher = FinancialDispatcher(owner.run_id, owner.resource_lock, [claim], [fact], [], [],
        '2025-03-31', audit=owner.audit, secret_values=secret_values)
    owner.save_state = lambda *_: None
    for role in ('planner', 'verifier'):
        owner.dispatcher.register_session(role, role, [claim.claim_id])
    return owner, claim


def invoke(owner, role, name, arguments, call_id):
    return owner._incoming('mj/tool.dispatch', {
        'session_id': role, 'role': role, 'run_id': owner.run_id, 'resource_lock': owner.resource_lock,
        'tool_name': name, 'params': arguments, 'call_id': call_id,
    })


def binding(claim):
    return dict(company=claim.company, account='REVENUE', period=claim.period,
                scope='合并', relation='value', base_period='')


def test_shared_schemas_publish_exact_binding_vocabulary():
    for role in ('planner', 'verifier'):
        definitions = {tool['name']: tool['inputSchema'] for tool in tool_definitions(role)}
        for tool in ('mj_submit_binding', 'mj_submit_bindings'):
            schema = definitions[tool]
            if tool == 'mj_submit_bindings':
                schema = schema['properties']['bindings']['items']
            fields = schema['properties']['binding']['properties']
            assert fields['account']['enum'] == list(BIND_ACCOUNTS)
            assert fields['scope']['enum'] == list(BIND_SCOPES)
            assert fields['relation']['enum'] == list(BIND_RELATIONS)
            assert {'', 'UNKNOWN', 'REVENUE', 'EPS_DILUTED'} <= set(fields['account']['enum'])
            assert '营业收入' not in fields['account']['enum']
            assert 'account_labels' in fields['account']['description']
            assert set(schema['properties']['binding']['required']) == set(BIND_FIELDS)
            assert schema['properties']['binding']['additionalProperties'] is False


def test_recorded_04_cache_calls_match_real_rejections_without_mutating_old_run(tmp_path):
    recorded = json.loads(RECORDED.read_text(encoding='utf-8'))
    assert recorded['provenance']['source_status'] == 'partial'
    assert len(recorded['cached_submission_calls']) == len(recorded['trace_submissions']) == 5
    claim = AtomicClaim(**recorded['claim'])
    owner, claim = bridge(tmp_path, claim=claim)
    original_value = claim.candidate_value
    for index, row in enumerate(recorded['trace_submissions']):
        assert any(call['input'] == row['input'] for call in recorded['cached_submission_calls'])
        answer = invoke(owner, row['role'], row['tool'], row['input'], f'captured-{index}')
        if row['status'] == 'denied':
            assert answer['status'] == 'rejected' and answer['accepted'] is False
            assert answer['error']['code'] == 'invalid_binding_account'
            assert answer['error']['field'] == 'binding.account'
            assert 'REVENUE' in answer['error']['allowed']
            assert owner.dispatcher.trace[-1]['status'] == 'denied'
            assert owner.dispatcher.trace[-1]['input'] == {'input_sha256': digest(row['input'])}
        else:
            assert answer['accepted'] is True
    assert not owner.dispatcher.bindings['planner']
    assert len(owner.dispatcher.bindings['verifier']) == 1
    assert not owner.dispatcher.freeze_bindings()[0].binding_status == 'agreed'
    # A controlled model-side repair uses the source dictionary. The host
    # never silently normalizes the rejected Chinese label or creates a bind.
    repair = copy.deepcopy(recorded['trace_submissions'][0]['input'])
    labels = owner.dispatcher.source_labels(claim)
    repair['bindings'][0]['binding']['account'] = labels[repair['bindings'][0]['binding']['account']]
    assert invoke(owner, 'planner', 'mj_submit_bindings', repair, 'controlled-repair')['accepted']
    rebound = owner.dispatcher.freeze_bindings()[0]
    assert rebound.binding_status == 'agreed'
    assert rebound.candidate_value == original_value
    assert Judge(owner.dispatcher.store, '2025-03-31', require_dual_binding=True).verify(rebound).status == 'verified'


@pytest.mark.parametrize('field,value,code', [
    ('account', '营业收入', 'invalid_binding_account'),
    ('scope', '集团默认口径', 'invalid_binding_scope'),
    ('relation', '同比', 'invalid_binding_relation'),
    ('account', 'CUSTOM_AMOUNT', 'invalid_binding_account'),
])
def test_enum_errors_are_safe_and_never_commit_partial_binding(tmp_path, field, value, code):
    owner, claim = bridge(tmp_path)
    owner.dispatcher.store.facts.append(Fact('unregistered', claim.company, 'CUSTOM_AMOUNT', claim.period))
    arguments = {'mention_id': claim.claim_id, 'binding': {**binding(claim), field: value}}
    rejected = invoke(owner, 'planner', 'mj_submit_binding', arguments, 'invalid-enum')
    assert rejected['error']['code'] == code
    assert not owner.dispatcher.bindings['planner']
    assert value not in json.dumps(rejected, ensure_ascii=False)


@pytest.mark.parametrize('account', ['', 'UNKNOWN'])
def test_unknown_fields_are_accepted_but_do_not_become_numeric_agreement(tmp_path, account):
    owner, claim = bridge(tmp_path)
    for role in ('planner', 'verifier'):
        args = {'mention_id': claim.claim_id, 'binding': {**binding(claim), 'account': account, 'scope': '', 'relation': ''}}
        assert invoke(owner, role, 'mj_submit_binding', args, role)['accepted']
    frozen = owner.dispatcher.freeze_bindings()[0]
    result = Judge(owner.dispatcher.store, '2025-03-31', require_dual_binding=True).verify(frozen)
    assert frozen.binding_status == 'disagreed'
    assert result.status == 'abstained' and result.abstention_code == 'X3'


def test_source_semantic_review_is_never_aliased_to_a_registered_amount(tmp_path):
    claim = extract_mentions('甲公司2024年合并口径管理费用率为9.66%。', '甲公司')[0]
    owner, claim = bridge(tmp_path, claim=claim)
    assert claim.account == 'ADMIN_EXPENSE_RATIO'
    assert owner.dispatcher.source_dimensions(claim)['account'] == 'ADMIN_EXPENSE_RATIO'
    assert claim.table_context['definition_id'] == 'metric:admin-expense-ratio'
    for role in ('planner', 'verifier'):
        rejected = invoke(owner, role, 'mj_submit_binding', {'mention_id': claim.claim_id,
            'binding': {**binding(claim), 'account': 'EXP_ADMIN'}}, role)
        assert rejected['accepted'] is False and rejected['error']['code'] == 'binding_source_conflict'
        assert rejected['error']['source_dimensions']['account'] == 'ADMIN_EXPENSE_RATIO'
        assert not owner.dispatcher.bindings[role]
    frozen = owner.dispatcher.freeze_bindings()[0]
    assert not owner.dispatcher.agreements[claim.claim_id]['agreed']
    result = Judge(owner.dispatcher.store, '2025-03-31', require_dual_binding=True).verify(frozen)
    assert result.status == 'abstained' and result.abstention_code in {'X2', 'X3'}


@pytest.mark.parametrize('tool,mutation', [
    ('mj_submit_binding', 'extra_secret_key'),
    ('mj_submit_binding', 'missing_field'),
    ('mj_submit_binding', 'invalid_account'),
    ('mj_read_context', 'extra_secret_key'),
    ('mj_read_context', 'foreign_mention'),
])
def test_all_denied_payloads_leave_only_hashes_and_safe_output(tmp_path, tool, mutation):
    owner, claim = bridge(tmp_path)
    secret = 'secret_in_input-sk-do-not-persist'
    args = {'mention_id': claim.claim_id, 'binding': binding(claim)} if tool == 'mj_submit_binding' else {'mention_id': claim.claim_id}
    if mutation == 'extra_secret_key':
        args[secret] = {'api_key': secret}
    elif mutation == 'missing_field':
        del args['binding']['company']
        args['binding']['base_period'] = secret
    elif mutation == 'invalid_account':
        args['binding']['account'] = secret
    else:
        args['mention_id'] = secret
    try:
        response = invoke(owner, 'planner', tool, args, 'denied')
    except (ToolDenied, jsonschema.ValidationError) as exc:
        response = {'exception_type': type(exc).__name__, 'message': str(exc)}
    assert secret not in json.dumps(response)
    assert owner.dispatcher.trace[-1]['status'] == 'denied'
    assert owner.dispatcher.trace[-1]['input'] == {'input_sha256': digest(args)}
    assert secret not in json.dumps(owner.dispatcher.trace)
    assert secret not in (tmp_path/'audit.jsonl').read_text(encoding='utf-8')


def test_keys_resolved_after_prepare_cannot_enter_otherwise_valid_bindings(tmp_path):
    secret_values = []
    owner, claim = bridge(tmp_path, secret_values=secret_values)
    secret = 'secret_in_input-sk-after-prepare'
    secret_values.append(secret)
    args = {'mention_id': claim.claim_id, 'binding': {**binding(claim), 'base_period': secret}}
    jsonschema.validate(args, TOOL_SCHEMAS['mj_submit_binding'])  # Structurally valid.
    with pytest.raises(ToolDenied, match='credential material'):
        invoke(owner, 'planner', 'mj_submit_binding', args, 'known-key')
    assert not owner.dispatcher.bindings['planner']
    assert secret not in json.dumps(owner.dispatcher.trace)
    assert secret not in (tmp_path/'audit.jsonl').read_text(encoding='utf-8')


def test_native_events_redact_known_keys_and_private_metadata_before_actual_audit(tmp_path):
    owner, _ = bridge(tmp_path)
    secret = 'fixture-only-secret-in-native-event'
    owner.resolved_secrets.append(secret)
    event = {'type': 'tool-call-arguments', secret: {'query': 'benign'},
        'arguments': [{secret: 'echo '+secret, ' API-Key ': 'unresolved-credential-payload',
                       'Provider-Token': 'another-unresolved-payload'}],
        ' Reasoning-Content ': 'private-never-a-product-conclusion', 'safe': 'source-block'}
    owner._event('session.event', event)
    raw = (tmp_path/'audit.jsonl').read_text(encoding='utf-8')
    row = json.loads(raw)
    assert all(value not in raw for value in (secret, 'unresolved-credential-payload',
        'another-unresolved-payload', 'private-never-a-product-conclusion'))
    assert row['original_event_sha256'] == digest(event)
    assert row['output_hash'] == digest(row['output'])
    assert row['original_event_sha256'] != row['output_hash']
    assert row['output']['safe'] == 'source-block' and row['output']['arguments'] == [{'[REDACTED]': 'echo [REDACTED]'}]


@pytest.mark.parametrize('field', [' Api-Key ', 'APIKey', 'apiToken', 'api-token', 'AUTHORIZATION',
                                 ' Password ', 'secret', 'Access-Key', 'providerApiKey', 'session_token'])
def test_native_credential_aliases_are_removed_without_resolving_any_key(tmp_path, field):
    owner, _ = bridge(tmp_path)
    owner.secret_resolver = lambda *_: (_ for _ in ()).throw(AssertionError('Redaction cannot resolve new credentials'))
    assert owner.redact({'safe': [{field: 'unresolved-private-field', 'preserved': 'source'}]}) == {
        'safe': [{'preserved': 'source'}]}


def test_native_cache_put_preserves_request_hash_and_labels_raw_and_sanitized_hashes(tmp_path):
    owner, _ = bridge(tmp_path)
    secret = 'fixture-only-secret-in-model-cache-key'
    owner.resolved_secrets.append(secret)
    chunks = [{'type': 'text-delta', 'text': 'Financial evidence '+secret,
               secret: {'safe': 'provider-metadata'}, ' Api-Token ': 'unresolved-private-payload'}]
    request_hash = digest({'model': 'fictitious-model', 'prompt': 'original fixture request'})
    assert owner._incoming('mj/model.cache.put', dict(session_id='planner', role='planner',
        run_id=owner.run_id, resource_lock=owner.resource_lock, request_hash=request_hash, chunks=chunks)) == {'stored': True}
    raw_cache = (tmp_path/'agent_model_cache.json').read_text(encoding='utf-8')
    raw_audit = (tmp_path/'audit.jsonl').read_text(encoding='utf-8')
    assert secret not in raw_cache+raw_audit and 'unresolved-private-payload' not in raw_cache+raw_audit
    assert set(json.loads(raw_cache)) == {request_hash}
    event = json.loads(raw_audit)
    assert event['type'] == 'model_response_cache' and event['request_hash'] == request_hash
    assert event['original_response_sha256'] == digest(chunks)
    assert event['sanitized_response_sha256'] == digest(owner.model_cache[request_hash])
    assert event['original_response_sha256'] != event['sanitized_response_sha256'] and event['content_redacted'] is True


def test_legitimate_native_metadata_and_request_hash_stay_unchanged(tmp_path):
    owner, _ = bridge(tmp_path)
    chunks = [{'type': 'tool-call-delta', 'toolName': 'mj_list_reports', 'arguments': '{}'},
              {'type': 'finish', 'finishReason': 'completed'}]
    request_hash = digest({'prompt': 'list registered reports'})
    owner._incoming('mj/model.cache.put', dict(session_id='planner', role='planner', run_id=owner.run_id,
        resource_lock=owner.resource_lock, request_hash=request_hash, chunks=chunks))
    row = json.loads((tmp_path/'audit.jsonl').read_text(encoding='utf-8'))
    assert owner.model_cache[request_hash] == chunks
    assert row['original_response_sha256'] == row['sanitized_response_sha256'] == digest(chunks)
    assert row['content_redacted'] is False


def test_typed_rejection_ignores_exception_strings_and_untrusted_metadata():
    secret = 'secret-in-exception-or-identity'
    exc = FinancialArgumentError('invalid_binding_account')
    expected = exc.rejection()
    exc.args = (secret,)
    exc.field = secret
    exc.allowed = [secret]
    exc.original_params = {'session_id': secret}
    assert exc.rejection() == expected
    exc.code = secret
    assert secret not in json.dumps(exc.rejection())
    with pytest.raises(ValueError) as invalid:
        FinancialArgumentError(secret)
    assert secret not in str(invalid.value)


def test_identity_permissions_and_infrastructure_errors_never_become_repair_results(tmp_path, monkeypatch):
    owner, claim = bridge(tmp_path)
    args = {'mention_id': claim.claim_id, 'binding': {**binding(claim), 'account': '营业收入'}}
    with pytest.raises(ToolDenied, match='role cannot'):
        invoke(owner, 'verifier', 'mj_verify', {'binding_id': claim.claim_id}, 'forbidden')
    envelope = {'session_id': 'planner', 'role': 'verifier', 'run_id': owner.run_id,
                'resource_lock': owner.resource_lock, 'tool_name': 'mj_submit_binding', 'params': args, 'call_id': 'spoof'}
    with pytest.raises(ToolDenied, match='identity'):
        owner._incoming('mj/tool.dispatch', envelope)
    def failed(_):
        raise OSError('arbitrary infrastructure detail')
    monkeypatch.setattr(owner.dispatcher, 'dispatch', failed)
    with pytest.raises(OSError):
        invoke(owner, 'planner', 'mj_submit_binding', args, 'infra')


def test_hash_only_denied_inputs_preserve_logical_session_replay_equality(tmp_path):
    from mingjing.agent_replay import logical_session_trace
    traces = []
    for index in range(2):
        owner, claim = bridge(tmp_path/str(index))
        args = {'mention_id': claim.claim_id, 'binding': {**binding(claim), 'account': '营业收入'}}
        invoke(owner, 'planner', 'mj_submit_binding', args, f'retry-{index}')
        traces.append(logical_session_trace(owner.dispatcher.trace, [{'session_id': 'planner', 'role': 'planner', 'mention_ids': [claim.claim_id]}]))
    assert traces[0] == traces[1]


def test_missing_token_budget_uses_generous_default_and_preserves_explicit_lock(tmp_path):
    config = AppConfig().to_dict()
    config['agent']['budget'].pop('max_total_tokens')
    owner = FinancialAgent({}, config, lambda _: '', tmp_path/'default')
    assert owner.budget['max_total_tokens'] == 1000000
    config['agent']['budget']['max_total_tokens'] = 250000
    explicit = FinancialAgent({}, config, lambda _: '', tmp_path/'locked')
    assert explicit.budget['max_total_tokens'] == 250000
    assert config['agent']['budget']['max_total_tokens'] == 250000
