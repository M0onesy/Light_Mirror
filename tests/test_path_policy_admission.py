"""Task/config path policy is checked before any admission side effects."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import server
from mingjing.config import (AppConfig, ConfigError, ConfigManager, SecretStore,
                             ensure_workspace, validate_path)


@pytest.fixture
def permitted_state(tmp_path, monkeypatch):
    allowed = tmp_path/'allowed'
    allowed.mkdir()
    profile = allowed/'profile'
    monkeypatch.setenv('MINGJING_HOME', str(profile))
    starts = []
    monkeypatch.setattr(server.RunTask, 'start', lambda task: starts.append(task.id))
    state = server.AppState()
    state.config.update({'security': {'allow_external_paths': False, 'allowed_roots': [str(allowed)]}})
    draft, corpus, facts, kb = allowed/'draft.md', allowed/'corpus', allowed/'facts.json', allowed/'kb'
    draft.write_text('Locked source material.', encoding='utf-8')
    corpus.mkdir()
    facts.write_text('[]', encoding='utf-8')
    kb.mkdir()
    return state, starts, allowed, {'draft': str(draft), 'corpus': str(corpus),
                                    'facts': str(facts), 'knowledge_root': str(kb)}


def saved_state(state):
    return state.registry_path.read_bytes(), json.dumps(state.run_registry, sort_keys=True), list(state.history_roots)


def assert_rejected_without_admission(state, starts, request, *, missing=None):
    before = saved_state(state)
    with pytest.raises(ConfigError):
        state.launch({'run_id': 'policy-rejected', 'mode': 'kernel', **request})
    assert not starts and not state.tasks
    assert 'policy-rejected' not in state.run_registry
    assert saved_state(state) == before
    if missing is not None:
        assert not missing.exists()


def test_original_outside_output_counterexample_is_transactionally_rejected(permitted_state, tmp_path):
    state, starts, allowed, request = permitted_state
    outside = tmp_path/'outside-runs'
    before_cfg = state.config.get()
    saved = state.config._manager.path.read_bytes()
    registry = saved_state(state)
    with pytest.raises(ConfigError, match='workspace.output_root'):
        state.config.update({'workspace': {'output_root': str(outside)}})
    assert state.config.get() == before_cfg and state.config._manager.path.read_bytes() == saved
    assert not outside.exists() and not starts and not state.tasks
    assert saved_state(state) == registry


@pytest.mark.parametrize('section,field', [
    ('workspace', 'data_root'), ('workspace', 'output_root'), ('workspace', 'temp_root'),
    ('logging', 'directory'), ('workspace', 'corpus_index'),
])
def test_launch_independently_rejects_unsafe_effective_storage_before_any_write(permitted_state, tmp_path, section, field):
    state, starts, allowed, request = permitted_state
    fresh_output = allowed/'not-yet-created'
    state.config._manager.config.workspace.output_root = str(fresh_output)
    outside = tmp_path/f'outside-{field}'
    if field == 'corpus_index':
        outside.write_text('path,sha256\n', encoding='utf-8')
    setattr(getattr(state.config._manager.config, section), field, str(outside))
    assert_rejected_without_admission(state, starts, request, missing=fresh_output)
    if field != 'corpus_index':
        assert not outside.exists()


@pytest.mark.parametrize('field,setting,kind', [
    ('draft', 'draft_path', 'file'), ('corpus', 'corpus_root', 'dir'),
    ('facts', 'facts_path', 'file'), ('knowledge_root', 'knowledge_root', 'dir'),
])
@pytest.mark.parametrize('source', ['task', 'saved_default'])
def test_effective_materials_task_then_config_are_validated_before_registration(permitted_state, tmp_path, field, setting, kind, source):
    state, starts, allowed, request = permitted_state
    fresh_output = allowed/'fresh-output'
    state.config._manager.config.workspace.output_root = str(fresh_output)
    outside = tmp_path/f'outside-{field}'
    outside.mkdir() if kind == 'dir' else outside.write_text('[]', encoding='utf-8')
    selected = dict(request)
    if source == 'task':
        selected[field] = str(outside)
    else:
        setattr(state.config._manager.config.workspace, setting, str(outside))
        selected.pop(field)
    assert_rejected_without_admission(state, starts, selected, missing=fresh_output)


def test_explicit_task_material_supersedes_unused_saved_path_without_expanding_roots(permitted_state, tmp_path):
    state, starts, allowed, request = permitted_state
    before_roots = list(state.config.get()['security']['allowed_roots'])
    state.config._manager.config.workspace.draft_path = str(tmp_path/'unused-outside-draft')
    task = state.launch({'run_id': 'task-overrides-source', 'mode': 'kernel', **request})
    assert task.request['draft'] == request['draft']
    assert starts == ['task-overrides-source']
    assert task.config_snapshot['security']['allowed_roots'] == before_roots


def test_explicit_permitted_root_and_app_defaults_are_usable(permitted_state):
    state, starts, allowed, request = permitted_state
    task = state.launch({'run_id': 'legitimate', 'mode': 'kernel', **request})
    assert task.out.is_relative_to(allowed)
    assert state.run_registry['legitimate']['out'] == str(task.out)
    assert starts == ['legitimate']
    assert task.config_snapshot['security']['allowed_roots'] == [str(allowed)]


def test_missing_optional_external_runtime_is_admitted_and_really_falls_back(permitted_state):
    state, starts, allowed, request = permitted_state
    missing = allowed / 'missing-native.exe'
    Path(request['draft']).write_text('甲公司2024年营业收入100元。', encoding='utf-8')
    state.config.update({'harness': {'enabled': True, 'runtime_mode': 'external',
                                     'executable': str(missing)}})
    running = state.launch({'run_id': 'missing-native-fallback', 'mode': 'harness', **request})
    assert starts == ['missing-native-fallback']
    running._run()
    assert running.status == 'completed'
    data = json.loads((running.out / 'results.json').read_text(encoding='utf-8'))
    assert data['model']['status'] == 'fallback' and data['model']['mode'] == 'kernel'
    assert any(event.get('event') == 'fallback' and event.get('requested_mode') == 'harness'
               for event in running.events)
    assert not missing.exists()


def test_missing_external_runtime_does_not_bypass_allowed_roots(permitted_state, tmp_path):
    state, starts, allowed, request = permitted_state
    # Simulate a stale/untrusted saved configuration so task admission must
    # independently enforce the policy even though the file does not exist.
    cfg = state.config._manager.config.harness
    cfg.enabled, cfg.runtime_mode, cfg.executable = True, 'external', str(tmp_path/'outside.exe')
    assert_rejected_without_admission(state, starts, dict(request, mode='harness'))


def test_missing_external_patch_is_still_rejected_before_admission(permitted_state):
    state, starts, allowed, request = permitted_state
    cfg = state.config._manager.config.harness
    cfg.enabled, cfg.runtime_mode = True, 'external'
    cfg.executable, cfg.patch_file = str(allowed/'optional.exe'), str(allowed/'required-patch.yml')
    assert_rejected_without_admission(state, starts, dict(request, mode='harness'))


def test_existing_history_root_cannot_grant_itself_permission(permitted_state, tmp_path):
    state, starts, allowed, request = permitted_state
    outside = tmp_path/'outside-runs'
    outside.mkdir()
    state.history_roots.append(str(outside))  # Historical registration is not a security grant.
    before = saved_state(state)
    with pytest.raises(ConfigError):
        state._remember_root(outside)
    assert saved_state(state) == before
    state.run_registry['old-outside'] = {'out': str(outside/'old-outside')}
    assert state._registered_out('old-outside') is None


def test_service_profile_paths_must_be_explicitly_allowed_when_restricted(tmp_path, monkeypatch):
    allowed = tmp_path/'materials'
    allowed.mkdir()
    profile = tmp_path/'profile'
    monkeypatch.setenv('MINGJING_HOME', str(profile))
    manager = ConfigManager(profile/'config.json', SecretStore(profile))
    manager.save()
    saved = manager.path.read_bytes()
    with pytest.raises(ConfigError, match='service.registry_root'):
        manager.update_from_dict({'workspace': {'data_root': str(allowed/'data'),
            'output_root': str(allowed/'runs'), 'temp_root': str(allowed/'tmp')},
            'logging': {'directory': str(allowed/'logs')},
            'security': {'allow_external_paths': False, 'allowed_roots': [str(allowed)]}})
    assert manager.path.read_bytes() == saved
    assert list(allowed.iterdir()) == []


def test_workspace_preflight_rejects_all_before_creating_the_first_directory(tmp_path, monkeypatch):
    monkeypatch.setenv('MINGJING_HOME', str(tmp_path/'allowed/profile'))
    allowed = tmp_path/'allowed'
    cfg = AppConfig().ensure_defaults()
    cfg.security.allow_external_paths = False
    cfg.security.allowed_roots = [str(allowed)]
    cfg.workspace.data_root = str(allowed/'first-legitimate')
    cfg.workspace.output_root = str(tmp_path/'outside-runs')
    with pytest.raises(ConfigError, match='workspace.output_root'):
        ensure_workspace(cfg)
    assert not (allowed/'first-legitimate').exists()
    assert not (tmp_path/'outside-runs').exists()


def linked_directory(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            pytest.skip('Directory symlinks unavailable')
        made = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                              capture_output=True, text=True)
        if made.returncode:
            pytest.skip('Directory symlinks/junctions unavailable')


@pytest.mark.parametrize('follow', [False, True])
def test_output_link_or_junction_cannot_escape_root_even_with_source_opt_in(permitted_state, tmp_path, follow):
    state, starts, allowed, request = permitted_state
    outside = tmp_path/'outside-target'
    outside.mkdir()
    link = allowed/'linked-runs'
    linked_directory(link, outside)
    state.config._manager.config.security.follow_symlinks = follow
    state.config._manager.config.workspace.output_root = str(link)
    assert_rejected_without_admission(state, starts, request)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('follow', [False, True])
def test_material_link_or_junction_escape_is_rejected(permitted_state, tmp_path, follow):
    state, starts, allowed, request = permitted_state
    outside = tmp_path/'outside-target'
    outside.mkdir()
    (outside/'draft.md').write_text('source', encoding='utf-8')
    link = allowed/'linked-material'
    linked_directory(link, outside)
    state.config._manager.config.security.follow_symlinks = follow
    assert_rejected_without_admission(state, starts, {**request, 'draft': str(link/'draft.md')})


def test_explicit_empty_allowed_roots_never_mean_unrestricted(tmp_path):
    with pytest.raises(ConfigError, match='empty'):
        validate_path(tmp_path/'file', allowed_roots=[])


def test_event_emission_cannot_create_an_outside_run_directory(permitted_state, tmp_path):
    state, _, _, _ = permitted_state
    outside = tmp_path/'outside-events'
    task = server.RunTask('forbidden-output', {}, outside, state.config)
    task.emit('started')
    assert task.events[0]['event'] == 'started'
    assert not outside.exists()


def test_worker_rechecks_input_junction_before_copy_or_execution(permitted_state, tmp_path, monkeypatch):
    state, _, allowed, request = permitted_state
    task = state.launch({'run_id': 'changed-input-link', 'mode': 'kernel', **request})
    task.out.mkdir()
    outside = tmp_path/'outside-input'
    outside.mkdir()
    linked_directory(task.out/'input', outside)
    calls = []
    monkeypatch.setattr(server.subprocess, 'Popen', lambda *args, **kwargs: calls.append(args))
    task._run()
    assert task.status == 'failed' and not calls
    assert not list(outside.iterdir())
    assert not (task.out/'run_request.json').exists()
    assert not any(row['event'] == 'started' for row in task.events)


def test_event_file_link_is_rejected_even_when_task_directory_is_safe(permitted_state, tmp_path):
    state, _, _, request = permitted_state
    task = state.launch({'run_id': 'changed-events-link', 'mode': 'kernel', **request})
    task.out.mkdir()
    outside = tmp_path/'outside-events'
    outside.mkdir()
    linked_directory(task.out/'web_events.jsonl', outside)
    task.emit('phase', name='checking')
    assert task.events[-1]['event'] == 'phase'
    assert not list(outside.iterdir())


def test_reparse_context_rejects_output_junction_before_packet_creation(permitted_state, tmp_path, monkeypatch):
    from mingjing import reparse_inputs
    state, _, _, request = permitted_state
    task = state.launch({'run_id': 'reparse-source', 'mode': 'kernel', **request})
    task.status = 'completed'
    outside = tmp_path/'outside-reparse'
    outside.mkdir()
    linked_directory(task.root/'reparse', outside)
    monkeypatch.setattr(reparse_inputs, 'load_parent_context',
                        lambda *args, **kwargs: {'data': {'run_id': task.id}, 'company': 'source'})
    with pytest.raises(ConfigError, match='reparse_output'):
        state.reparse_context(task.id)
    assert not list(outside.iterdir())


def test_harness_health_rejects_diagnostic_junction_before_runtime_start(permitted_state, tmp_path, monkeypatch):
    from mingjing.harness_client import HarnessRuntime
    state, _, _, _ = permitted_state
    outside = tmp_path/'outside-diagnostics'
    outside.mkdir()
    linked_directory(server._default_root()/'diagnostics', outside)
    starts = []
    monkeypatch.setattr(HarnessRuntime, 'start', lambda runtime: starts.append(runtime))
    handler = server.Handler.__new__(server.Handler)
    handler.server = SimpleNamespace(state=state)
    with pytest.raises(ConfigError, match='harness_diagnostics'):
        handler._test_harness({})
    assert not starts and not list(outside.iterdir())


@pytest.mark.parametrize('action', ['test', 'test-agent'])
def test_provider_http_diagnostics_reject_output_junction_before_provider_call(permitted_state, tmp_path, monkeypatch, action):
    from mingjing import provider_api
    state, _, _, _ = permitted_state
    outside = tmp_path/'outside-provider-diagnostics'
    outside.mkdir()
    linked_directory(server._default_root()/'diagnostics', outside)
    calls = []
    monkeypatch.setattr(provider_api, 'test_connection', lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(provider_api, 'test_agent', lambda *args, **kwargs: calls.append(args))
    httpd = server.MingJingServer(('127.0.0.1', 0), state)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}/api/providers/deepseek/{action}',
                                         data=b'{}', headers={'Content-Type': 'application/json'}, method='POST')
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request)
        assert rejected.value.code == 400
        assert not calls and not list(outside.iterdir())
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(2)


@pytest.mark.parametrize('endpoint,method', [('/api/config', 'PATCH'), ('/api/config/import', 'POST')])
def test_http_path_rejection_is_transactional(permitted_state, tmp_path, endpoint, method):
    state, starts, _, _ = permitted_state
    before_cfg, before_file = state.config.get(), state.config._manager.path.read_bytes()
    before_registry = saved_state(state)
    outside = tmp_path/'outside-config-output'
    patch = {'workspace': {'output_root': str(outside)}}
    payload = {'config': patch} if endpoint.endswith('/import') else patch
    httpd = server.MingJingServer(('127.0.0.1', 0), state)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}'+endpoint,
                                         data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json'}, method=method)
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request)
        assert rejected.value.code == 400
        assert state.config.get() == before_cfg and state.config._manager.path.read_bytes() == before_file
        assert saved_state(state) == before_registry and not state.tasks and not starts
        assert not outside.exists()
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(2)


@pytest.mark.parametrize('endpoint,raw,method', [
    ('/api/run', b'{"run_id":"overflow","timeout_s":1e999,"token":"do-not-echo"}', 'POST'),
    ('/api/config', b'{"run":{"timeout_s":-1e999},"token":"do-not-echo"}', 'PATCH'),
    ('/api/config/import', b'{"config":{"llm":{"temperature":1e999}},"token":"do-not-echo"}', 'POST'),
])
def test_http_overflow_floats_return_400_before_mutating_state(permitted_state, endpoint, raw, method):
    state, starts, _, _ = permitted_state
    before_cfg, before_file = state.config.get(), state.config._manager.path.read_bytes()
    before_registry = saved_state(state)
    httpd = server.MingJingServer(('127.0.0.1', 0), state)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}'+endpoint, data=raw,
                                         headers={'Content-Type': 'application/json'}, method=method)
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request)
        assert rejected.value.code == 400
        assert b'do-not-echo' not in rejected.value.read()
        assert state.config.get() == before_cfg and state.config._manager.path.read_bytes() == before_file
        assert saved_state(state) == before_registry and not state.tasks and not starts
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(2)
