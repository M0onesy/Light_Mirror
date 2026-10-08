import json
from pathlib import Path

import pytest

from mingjing.config import ConfigError, configured_temp_root
from mingjing.server import RunTask, _append_product_log, _effective_run_request


def config(tmp_path, level='INFO'):
    return {'workspace': {'temp_root': str(tmp_path/'custom-temp')},
            'logging': {'directory': str(tmp_path/'custom-logs'), 'level': level},
            'security': {'allow_external_paths': False, 'allowed_roots': [str(tmp_path)]}}


def test_knowledge_path_uses_task_then_saved_defaults_and_is_frozen_at_admission(tmp_path):
    cfg = config(tmp_path)
    cfg['workspace']['knowledge_root'] = str(tmp_path/'saved-kb')
    assert _effective_run_request({}, cfg)['knowledge_root'] == str(tmp_path/'saved-kb')
    assert _effective_run_request({'knowledge_root': str(tmp_path/'task-kb')}, cfg)['knowledge_root'] == str(tmp_path/'task-kb')

    class Configuration:
        def get(self):
            return cfg

    task = RunTask('frozen-knowledge', {}, tmp_path/'runs', Configuration())
    cfg['workspace']['knowledge_root'] = str(tmp_path/'later-kb')
    assert task.request['knowledge_root'] == str(tmp_path/'saved-kb')
    assert task.config_snapshot['workspace']['knowledge_root'] == str(tmp_path/'saved-kb')
    assert task.summary()['request']['knowledge_root'] == str(tmp_path/'saved-kb')


def test_temp_root_is_created_under_frozen_path_policy(tmp_path):
    cfg = config(tmp_path)
    expected = tmp_path/'custom-temp'
    assert configured_temp_root(cfg) == expected
    assert expected.is_dir()
    cfg['workspace']['temp_root'] = str(tmp_path.parent/'outside-controlled-root')
    with pytest.raises(ConfigError, match='outside allowed'):
        configured_temp_root(cfg)


def test_product_logs_use_configured_directory_level_and_redacted_keys_and_values(tmp_path):
    cfg = config(tmp_path, 'WARNING')
    log = tmp_path/'custom-logs/application.jsonl'
    _append_product_log(cfg, {'event': 'started'}, [])
    assert not log.exists()
    secret = 'private-test-token-never-log'
    _append_product_log(cfg, {'event': 'failed', 'run_id': 'task-1', 'error': 'failed '+secret,
                              'payload': {'api_key': 'unrecognized-private-payload', secret: secret}}, [secret])
    raw = log.read_text(encoding='utf-8')
    assert secret not in raw and 'unrecognized-private-payload' not in raw
    row = json.loads(raw)
    assert row['level'] == 'ERROR' and row['run_id'] == 'task-1'
    assert row['payload']['api_key'] == '[REDACTED]'


def test_task_credential_wrapper_protects_product_and_local_logs(tmp_path):
    class Configuration:
        def get(self):
            return config(tmp_path)

        def secret(self, reference):
            return 'fixture-'+reference

    task = RunTask('redacted-task', {}, tmp_path/'runs', Configuration())
    token = task._task_secret('role-secret')
    task.emit('failed', error='provider returned '+token)
    for path in [task.out/'web_events.jsonl', tmp_path/'custom-logs/application.jsonl']:
        assert path.is_file() and token not in path.read_text(encoding='utf-8')
    assert task.events[-1]['error'] == 'provider returned [REDACTED]'


def test_unavailable_log_directory_is_visible_without_breaking_task_events(tmp_path):
    class Configuration:
        def get(self):
            cfg = config(tmp_path)
            cfg['logging']['directory'] = str(tmp_path.parent/'blocked-log-path')
            return cfg

    task = RunTask('visible-storage-failure', {}, tmp_path/'runs', Configuration())
    task.emit('started')
    task.emit('phase', name='checking')
    assert sum(row.get('reason') == 'product_log_unavailable' for row in task.events) == 1
    assert (task.out/'web_events.jsonl').is_file()


def test_http_admission_uses_saved_paths_and_reports_staged_capability_as_declared(tmp_path, monkeypatch):
    import threading
    import urllib.request
    from mingjing import server

    monkeypatch.setenv('MINGJING_HOME', str(tmp_path/'profile'))
    bundle = tmp_path/'bundle'
    carrier = bundle/'runtime/finance-carrier/staged/carrier_manifest.json'
    carrier.parent.mkdir(parents=True)
    carrier.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(server, '_BUNDLE_ROOT', bundle)
    monkeypatch.setattr(RunTask, 'start', lambda self: None)
    draft, corpus, facts = tmp_path/'draft.md', tmp_path/'reports', tmp_path/'facts.json'
    draft.write_text('甲公司2024年营业收入100元。', encoding='utf-8')
    corpus.mkdir()
    facts.write_text('[]', encoding='utf-8')
    state = server.AppState()
    state.config.update({'workspace': {'draft_path': str(draft), 'corpus_root': str(corpus), 'facts_path': str(facts)}})
    httpd = server.MingJingServer(('127.0.0.1', 0), state)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    try:
        with urllib.request.urlopen(base+'/api/config') as response:
            capability = json.load(response)['capabilities']
        assert capability['harness_runtime_configured'] is True
        assert capability['harness_capability_status'] == 'declared_requires_health_check'
        request = urllib.request.Request(base+'/api/run', data=b'{"run_id":"saved-path-task","mode":"kernel"}',
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response:
            accepted = json.load(response)['request']
        assert accepted['draft'] == str(draft) and accepted['corpus'] == str(corpus)
        assert accepted['facts'] == str(facts)
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(2)
