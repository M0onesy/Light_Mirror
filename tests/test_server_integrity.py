"""Regressions for request framing, lifecycle admission and frozen export."""
import io
import json
import threading
import zipfile
from email.message import Message
from types import SimpleNamespace

import pytest

from mingjing import server
from mingjing.evidence import sha256_file
from mingjing.import_integrity import assert_application_origin


def body(headers, raw=b'{}'):
    message = Message()
    for key, value in headers:
        message[key] = value
    return SimpleNamespace(headers=message, rfile=io.BytesIO(raw))


@pytest.mark.parametrize('headers,raw', [
    ([('Content-Length', '-1')], b''),
    ([('Content-Length', '2'), ('Content-Length', '2')], b'{}'),
    ([('Transfer-Encoding', 'chunked')], b'0\r\n\r\n'),
    ([('Content-Length', str(8*1024*1024+1))], b'{}'),
    ([('Content-Length', '10')], b'{}'),
    ([('Content-Length', '9')], b'{"x":NaN}'),
])
def test_invalid_http_framing_never_enters_config_or_run(headers, raw):
    with pytest.raises(ValueError):
        server._body(body(headers, raw))


def test_request_timeout_is_restored():
    connection = SimpleNamespace(value=4)
    connection.gettimeout = lambda: connection.value
    connection.settimeout = lambda value: setattr(connection, 'value', value)
    handler = body([('Content-Length', '2')])
    handler.connection = connection
    assert server._body(handler) == {}
    assert connection.value == 4


@pytest.mark.parametrize('raw', [b'{"timeout_s":1e999}', b'{"timeout_s":-1e999}',
    b'{"nested":{"items":[0,1e999]}}', b'{"token":"do-not-echo","x":1e999}'])
def test_json_float_overflow_is_rejected_without_echoing_body(raw):
    with pytest.raises(ValueError, match='finite') as rejected:
        server._body(body([('Content-Length', str(len(raw)))], raw))
    assert 'do-not-echo' not in str(rejected.value)


def test_finite_json_numbers_keep_public_types():
    raw = b'{"large_float":1e308,"integer":123456789012345678901234567890,"small":0.1}'
    value = server._body(body([('Content-Length', str(len(raw)))], raw))
    assert type(value['large_float']) is float and type(value['small']) is float
    assert type(value['integer']) is int


def test_shutdown_during_launch_preparation_refuses_admission(tmp_path, monkeypatch):
    monkeypatch.setenv('MINGJING_HOME', str(tmp_path/'home'))
    state = server.AppState()
    entered, release = threading.Event(), threading.Event()
    original_get = state.config.get

    def delayed_get():
        entered.set()
        assert release.wait(3)
        return original_get()

    state.config.get = delayed_get
    starts, errors = [], []
    monkeypatch.setattr(server.RunTask, 'start', lambda task: starts.append(task.id))

    def launch():
        try:
            state.launch({'run_id': 'race', 'corpus': str(tmp_path), 'draft': str(tmp_path/'draft.md')})
        except ValueError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=launch)
    worker.start()
    assert entered.wait(3)
    with state.lock:
        state.sessions.closing = True
    release.set()
    worker.join(3)
    assert not worker.is_alive() and errors and not starts
    assert 'race' not in state.tasks and 'race' not in state.run_registry


def test_export_preserves_frozen_supplement_files_and_refuses_tampering(tmp_path):
    out = tmp_path/'run'
    supplement = out/'staged_reparse'/'packet'/'proof.json'
    supplement.parent.mkdir(parents=True)
    supplement.write_text('{"proof":"original page"}', encoding='utf-8')
    manifest = {'outputs': [{'path': supplement.relative_to(out).as_posix(),
                             'bytes': supplement.stat().st_size, 'sha256': sha256_file(supplement)}]}
    (out/'run_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    config = SimpleNamespace(get=lambda: {}, secret=lambda _: None)
    task = SimpleNamespace(out=out)
    raw = server._export_run(task, config)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.read('staged_reparse/packet/proof.json') == supplement.read_bytes()
    supplement.write_text('tampered', encoding='utf-8')
    with pytest.raises(ValueError, match='Frozen output changed'):
        server._export_run(task, config)


def test_actual_import_origins_are_current_checkout(tmp_path):
    root = server.PROJECT_ROOT
    origins = assert_application_origin(root)
    assert all(str(root/'mingjing') in path for path in origins.values())
    with pytest.raises(RuntimeError, match='different checkout'):
        assert_application_origin(tmp_path)


def test_paths_and_parameters_use_one_queue_time_configuration_snapshot(tmp_path):
    values = {'revision': 7, 'workspace': {'draft_path': 'default.md', 'corpus_root': 'corpus', 'facts_path': 'fallback.json'},
              'run': {'company': 'Default', 'period': '2024Y', 'report_date': '2025-03-31', 'mode': 'harness', 'scope': '合并'}}
    config = SimpleNamespace(get=lambda: values)
    task = server.RunTask('configured', {'draft': 'chosen.md', 'company': 'Chosen'}, tmp_path, config)
    assert task.request['draft'] == 'chosen.md' and task.request['company'] == 'Chosen'
    assert task.request['facts'] == 'fallback.json' and task.request['corpus'] == 'corpus'
    assert task.request['report_date'] == '2025-03-31'
    assert 'scope_declaration' not in task.request
    values['workspace']['facts_path'] = 'later.json'
    values['revision'] = 8
    assert task.config_snapshot['revision'] == 7 and task.request['facts'] == 'fallback.json'
    analysis = server._effective_run_request({'task_type': 'analysis'}, task.config_snapshot)
    assert 'draft' not in analysis


@pytest.mark.parametrize('field', ['reparse_inheritance', 'inherit_supplements_from'])
def test_browser_cannot_submit_internal_supplement_plan(tmp_path, monkeypatch, field):
    monkeypatch.setenv('MINGJING_HOME', str(tmp_path/'home'))
    state = server.AppState()
    with pytest.raises(ValueError):
        state.launch({'corpus': str(tmp_path), field: {'source_run': str(tmp_path)}})
    assert not state.tasks
