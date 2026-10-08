"""Acceptance entrypoint regressions with no real key, API or native runtime."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mingjing.config import AppConfig
from scripts import source_agent_acceptance as agent_probe
from scripts import source_session_acceptance as session_probe
from scripts import source_native_web_acceptance as web_probe
from scripts.source_acceptance_inputs import verify_seed_materials


ROOT = Path(__file__).resolve().parents[1]


def agent_args(tmp_path, **changes):
    values = dict(state=tmp_path/'seed/agent_state.json', out=tmp_path/'acceptance', task_type='check',
                  expect_event=[], corpus=None, knowledge_root=None, scope_declaration=None,
                  model_calls=128, tool_calls=1000, tokens=1000000, followup_tokens=None, timeout=1800)
    values.update(changes)
    return SimpleNamespace(**values)


def session_args(tmp_path, **changes):
    values = dict(run=tmp_path/'parent', out=tmp_path/'acceptance', question='Read the registered revenue metric',
                  metric='REVENUE_YOY', period='2023Y', expect='numeric', literal=None, source_contains=[])
    values.update(changes)
    return SimpleNamespace(**values)


def reject_side_effects(monkeypatch, module):
    def forbidden(*_args, **_kwargs):
        pytest.fail('Invalid arguments reached output creation, saved profile, credential or runtime preparation')
    for name in ('new_output', 'app_root', 'SecretStore', 'FinancialAgent', 'FinancialSessions'):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, forbidden)


@pytest.mark.parametrize('changes', [
    {'expect_event': ['policy_change'], 'task_type': 'analysis'},
    {'expect_event': ['accounting_policy_change']}, {'tokens': 0}, {'tokens': -1},
    {'model_calls': True}, {'tool_calls': 1.5}, {'timeout': '1800'}, {'followup_tokens': 0},
    {'followup_tokens': True}, {'scope_declaration': 'unknown'},
])
def test_invalid_agent_parameters_rejected_before_any_side_effect(tmp_path, monkeypatch, changes):
    reject_side_effects(monkeypatch, agent_probe)
    with pytest.raises(ValueError):
        agent_probe.execute(agent_args(tmp_path, **changes))
    assert not (tmp_path/'acceptance').exists()


@pytest.mark.parametrize('changes', [
    {'expect': 'knowledge_quote'}, {'expect': 'knowledge_quote', 'literal': ''},
    {'expect': 'knowledge_quote', 'literal': '  \t'}, {'literal': '\x00'},
    {'expect': 'source_quote', 'source_contains': ['']}, {'source_contains': [' \t']},
    {'question': ' '}, {'question': 'x'*3001}, {'metric': 'FAKE_METRIC'}, {'period': '2023-12-31'},
])
def test_invalid_followup_parameters_rejected_before_any_side_effect(tmp_path, monkeypatch, changes):
    reject_side_effects(monkeypatch, session_probe)
    with pytest.raises(ValueError):
        session_probe.execute(session_args(tmp_path, **changes))
    assert not (tmp_path/'acceptance').exists()


@pytest.mark.parametrize('timeout', [0, -1, True, '300', 29, 1801])
def test_native_web_validates_programmatic_budget_before_side_effects(tmp_path, monkeypatch, timeout):
    reject_side_effects(monkeypatch, web_probe)
    with pytest.raises(ValueError):
        web_probe.execute(SimpleNamespace(source_run=tmp_path/'seed', out=tmp_path/'acceptance', timeout=timeout))


def test_source_quote_default_is_retained_but_knowledge_subject_is_explicit(tmp_path):
    source = session_args(tmp_path, expect='source_quote')
    session_probe.validate_args(source)
    assert source.literal == '会计政策'
    knowledge = session_args(tmp_path, expect='knowledge_quote', literal='追溯调整法')
    session_probe.validate_args(knowledge)
    assert knowledge.literal == '追溯调整法'


@pytest.mark.parametrize('module', ['source_agent_acceptance', 'source_session_acceptance', 'source_native_web_acceptance',
                                   'source_financial_repairs_acceptance', 'source_native_reparse_acceptance'])
@pytest.mark.parametrize('style', ['direct', 'module'])
def test_source_entrypoints_help_resolves_current_checkout_without_install(module, style, tmp_path):
    command = [sys.executable, '-X', 'utf8']
    if style == 'direct':
        command += [str(ROOT/'scripts'/f'{module}.py')]
        cwd = tmp_path
    else:
        command += ['-m', f'scripts.{module}']
        cwd = ROOT
    result = subprocess.run(command+['--help'], cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout and 'ModuleNotFoundError' not in result.stderr


@pytest.mark.parametrize('module', ['source_agent_acceptance', 'source_session_acceptance', 'source_native_web_acceptance'])
def test_import_source_assertion_rejects_an_already_mixed_application(module, tmp_path):
    # A module already loaded from the historical backend cannot be fixed by
    # changing sys.path; the entrypoint must reject it before config reads.
    code = """
import importlib.util, pathlib, runpy, sys
original = importlib.util.find_spec
def changed(name, *args, **kwargs):
    if name == 'mingjing.agent':
        return importlib.util.spec_from_file_location(name, pathlib.Path(sys.argv[2])/'mingjing/agent.py')
    return original(name, *args, **kwargs)
importlib.util.find_spec = changed
runpy.run_path(sys.argv[1], run_name='__main__')
"""
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code,
        str(ROOT/'scripts'/f'{module}.py'), str(tmp_path), '--help'], cwd=ROOT,
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode != 0 and 'different checkout: mingjing.agent' in result.stderr


@pytest.fixture
def saved_fixture(tmp_path):
    profile = tmp_path/'profile'
    profile.mkdir()
    (profile/'config.json').write_text('{}', encoding='utf-8')
    cfg = AppConfig().to_dict()
    cfg['providers']['fixture'] = {'credential_ref': 'fixture-reference'}
    return profile, cfg, 'fixture-private-key-only'


def install_agent_fixture(tmp_path, monkeypatch, saved_fixture):
    profile, cfg, key = saved_fixture
    seed = tmp_path/'seed'
    seed.mkdir()
    draft = seed/'draft.md'
    draft.write_text('Invented financial claim', encoding='utf-8')
    (seed/'agent_state.json').write_text(json.dumps({'request': {'draft': str(draft), 'corpus': str(seed)}}), encoding='utf-8')
    monkeypatch.setattr(agent_probe, 'app_root', lambda: profile)
    monkeypatch.setattr(agent_probe, 'load_config', lambda *_: SimpleNamespace(to_dict=lambda: cfg))
    monkeypatch.setattr(agent_probe, 'resolve_route', lambda *_: {
        'credential_ref': 'fixture-reference', 'provider': 'fixture', 'model': 'fixture-model'})
    monkeypatch.setattr(agent_probe, 'SecretStore', lambda *_: SimpleNamespace(get=lambda _: key))
    return key


def test_agent_constructor_failure_finishes_and_redacts_receipt(tmp_path, monkeypatch, saved_fixture):
    key = install_agent_fixture(tmp_path, monkeypatch, saved_fixture)
    def fail(*_args, **_kwargs):
        raise RuntimeError('Runtime construction failed: '+key)
    monkeypatch.setattr(agent_probe, 'FinancialAgent', fail)
    args = agent_args(tmp_path)
    assert agent_probe.execute(args) == 1
    row = json.loads((args.out/'source_acceptance.json').read_text(encoding='utf-8'))
    assert row['status'] == 'failed' and row['phase'] == 'construct_agent'
    assert row['model_run']['status'] == 'not_executed' and row['usage'] == 'unknown'
    assert row['kernel_replay'] == row['agent_replay'] == {'status': 'not_executed'}
    assert row['user_profile_unchanged'] and not row['secret_scan']['plaintext_found']
    assert key not in json.dumps(row) and '[REDACTED]' in row['error']


@pytest.mark.parametrize('stage', ['initialize', 'resolve'])
def test_unresolved_credential_failures_cannot_echo_unknown_key(tmp_path, monkeypatch, saved_fixture, stage):
    key = install_agent_fixture(tmp_path, monkeypatch, saved_fixture)
    def fail(*_):
        raise RuntimeError('Unsafe backend message: '+key)
    monkeypatch.setattr(agent_probe, 'SecretStore', fail if stage == 'initialize' else
        lambda *_: SimpleNamespace(get=fail))
    monkeypatch.setattr(agent_probe, 'FinancialAgent', lambda *_args, **_kwargs: pytest.fail('No resolved credential'))
    args = agent_args(tmp_path)
    assert agent_probe.execute(args) == 1
    row = json.loads((args.out/'source_acceptance.json').read_text(encoding='utf-8'))
    assert row['status'] == 'failed' and row['phase'] == 'resolve_credentials'
    assert 'Credential ' in row['error'] and key not in json.dumps(row)
    assert row['agent_replay']['status'] == 'not_executed'


def test_native_agent_execution_failure_keeps_actual_usage_and_sessions(tmp_path, monkeypatch, saved_fixture):
    key = install_agent_fixture(tmp_path, monkeypatch, saved_fixture)
    class FailingAgent:
        native_request_count, total_tokens, usage_known = 3, 48000, True
        session_records = [{'role': 'planner', 'session_id': 'actual-fixture-session', 'error': key}]
        def __init__(self, *_args, **_kwargs):
            pass
        def run(self):
            raise RuntimeError('Fixture provider failure: '+key)
    monkeypatch.setattr(agent_probe, 'FinancialAgent', FailingAgent)
    args = agent_args(tmp_path)
    assert agent_probe.execute(args) == 1
    row = json.loads((args.out/'source_acceptance.json').read_text(encoding='utf-8'))
    assert row['status'] == 'failed' and row['phase'] == 'execute_native_task'
    assert row['usage'] == {'model_requests': 3, 'tokens': 48000}
    assert row['model_run'] == {'status': 'failed', 'reason': 'task_failed_after_native_model_requests'}
    assert row['sessions'][0]['session_id'] == 'actual-fixture-session'
    assert row['agent_replay']['status'] == 'not_executed' and key not in json.dumps(row)


def install_parent(tmp_path, monkeypatch, saved_fixture):
    profile, cfg, key = saved_fixture
    parent = tmp_path/'parent'
    parent.mkdir()
    (parent/'agent_lock.json').write_text(json.dumps({'config': cfg}), encoding='utf-8')
    (parent/'results.json').write_text(json.dumps({'run_id': 'parent-fixture', 'company': 'Invented company',
        'analysis': {'scope': '合并'}}), encoding='utf-8')
    monkeypatch.setattr(session_probe, 'app_root', lambda: profile)
    return key


def test_session_constructor_failure_receipt_requires_no_credential_access(tmp_path, monkeypatch, saved_fixture):
    install_parent(tmp_path, monkeypatch, saved_fixture)
    def fail(*_):
        raise RuntimeError('Fixture session storage unavailable')
    monkeypatch.setattr(session_probe, 'FinancialSessions', fail)
    monkeypatch.setattr(session_probe, 'SecretStore', lambda *_: pytest.fail('Construction must precede credentials'))
    args = session_args(tmp_path)
    assert session_probe.execute(args) == 1
    row = json.loads((args.out/'acceptance.json').read_text(encoding='utf-8'))
    assert row['status'] == 'failed' and row['phase'] == 'construct_session_service'
    assert row['session_status'] == 'not_created' and row['real_provider_api'] == 'not_executed'
    assert row['message_replay_status'] == 'not_executed' and row['usage'] == 'unknown'
    assert 'storage unavailable' in row['error'] and row['user_profile_unchanged']


class FrozenSessionFixture:
    def __init__(self, root, get_run, config):
        self.config = config
        self.inflight, self.workers = set(), {}
        self.state = {'status': 'ready', 'messages': [], 'events': [],
                      'usage': {'model_requests': 1, 'tool_calls': 1, 'tokens': 30000}}
    def create(self, _):
        return {'session_id': 'session-fixture'}
    def get(self, _):
        return self.state
    def check_locks(self, _):
        pass
    def submit(self, *_):
        self.config.secret('fixture-reference')
    def cancel_running(self):
        pass
    def wait_running(self, _):
        return True


def test_parent_lock_failure_is_persisted_before_reading_any_key(tmp_path, monkeypatch, saved_fixture):
    install_parent(tmp_path, monkeypatch, saved_fixture)
    class BrokenParent(FrozenSessionFixture):
        def check_locks(self, _):
            raise ValueError('Frozen parent resource hash changed')
    monkeypatch.setattr(session_probe, 'FinancialSessions', BrokenParent)
    monkeypatch.setattr(session_probe, 'SecretStore', lambda *_: pytest.fail('Parent lock failure must not resolve a key'))
    args = session_args(tmp_path)
    assert session_probe.execute(args) == 1
    row = json.loads((args.out/'acceptance.json').read_text(encoding='utf-8'))
    assert row['phase'] == 'validate_parent_locks' and 'resource hash changed' in row['error']
    assert row['session_id'] == 'session-fixture' and row['real_provider_api'] == 'not_executed'


def test_followup_model_failure_keeps_diagnostic_and_redacts_full_receipt(tmp_path, monkeypatch, saved_fixture):
    key = install_parent(tmp_path, monkeypatch, saved_fixture)
    class FailingTurn(FrozenSessionFixture):
        def submit(self, *_):
            resolved = self.config.secret('fixture-reference')
            self.state.update(status='failed', messages=[{'role': 'system', 'text': 'Failed '+resolved}])
            raise RuntimeError('Fixture turn failed '+resolved)
    monkeypatch.setattr(session_probe, 'FinancialSessions', FailingTurn)
    monkeypatch.setattr(session_probe, 'SecretStore', lambda *_: SimpleNamespace(get=lambda _: key))
    args = session_args(tmp_path)
    assert session_probe.execute(args) == 1
    row = json.loads((args.out/'acceptance.json').read_text(encoding='utf-8'))
    assert row['status'] == 'failed' and row['session_status'] == 'failed'
    assert row['usage']['tokens'] == 30000 and row['messages'][0]['role'] == 'system'
    assert row['message_replay_status'] == 'not_executed' and row['real_provider_api'] is True
    assert key not in json.dumps(row) and '[REDACTED]' in row['error']


def test_followup_scoring_uses_current_default_without_relaxing_financial_criteria(tmp_path, monkeypatch, saved_fixture):
    key = install_parent(tmp_path, monkeypatch, saved_fixture)
    monkeypatch.setattr(session_probe, 'FinancialSessions', FrozenSessionFixture)
    monkeypatch.setattr(session_probe, 'SecretStore', lambda *_: SimpleNamespace(get=lambda _: key))
    args = session_args(tmp_path)
    assert session_probe.execute(args) == 1
    row = json.loads((args.out/'acceptance.json').read_text(encoding='utf-8'))
    assert row['within_budget'] and row['budget']['max_total_tokens'] == 150000
    assert row['status'] == 'needs_improvement' and not row['answer_present']
    assert not row['message_replay_passed'] and not row['numeric_evidence_complete']


def test_material_seed_allows_new_task_across_code_freezes_but_rejects_changed_original(tmp_path):
    from mingjing.evidence import sha256_file
    seed = tmp_path/'seed'
    seed.mkdir()
    material = seed/'original.md'
    material.write_text('Original financial material', encoding='utf-8')
    (seed/'agent_lock.json').write_text(json.dumps({'kernel': 'historical-code-not-current',
        'resources': [{'path': str(material), 'sha256': sha256_file(material)}]}), encoding='utf-8')
    proof = verify_seed_materials(seed, [material])
    assert proof['status'] == 'passed' and proof['current_kernel_compatibility'].startswith('not_required')
    original_lock = (seed/'agent_lock.json').read_bytes()
    material.write_text('Changed original', encoding='utf-8')
    with pytest.raises(ValueError, match='bytes changed'):
        verify_seed_materials(seed, [material])
    assert (seed/'agent_lock.json').read_bytes() == original_lock
