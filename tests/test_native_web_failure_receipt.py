"""Controlled receipt failure fixtures: no provider, HTTP server or Harness runs."""
from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from mingjing.config import AppConfig
from scripts import source_native_web_acceptance as acceptance


@pytest.fixture
def controlled_web(tmp_path, monkeypatch):
    from mingjing import server as host
    import fitz

    monkeypatch.setattr(acceptance, 'ROOT', tmp_path)
    source = tmp_path/'runs/exposed-fixture'
    source.mkdir(parents=True)
    pdf = source/'original.pdf'
    with fitz.open() as document:
        document.new_page().insert_text((50, 50), 'Invented local receipt fixture')
        document.save(pdf)
    original = {'run_id': 'exposed-fixture', 'draft_text': 'invented excerpt',
        'results': [{'status': 'verified', 'source_span': {'start': 0, 'end': 1}}],
        'coverage': {'pages': [{'start': 0, 'end': 20, 'page': 1}]}}
    request = {'draft': str(pdf), 'company': 'Invented company', 'period': '2023Y',
               'report_date': '2024-04-30', 'corpus': str(source/'corpus')}
    (source/'results.json').write_text(json.dumps(original), encoding='utf-8')
    (source/'agent_state.json').write_text(json.dumps({'request': request}), encoding='utf-8')
    saved = tmp_path/'read-only-profile'
    saved.mkdir()
    (saved/'config.json').write_text('{}', encoding='utf-8')
    config = AppConfig().to_dict()
    token = 'fixture-only-key-never-a-real-provider'
    monkeypatch.setattr(acceptance, 'app_root', lambda: saved)
    monkeypatch.setattr(acceptance, 'load_config', lambda *_: SimpleNamespace(to_dict=lambda: config))
    monkeypatch.setattr(acceptance, 'resolve_route', lambda *_: {
        'credential_ref': 'invented-reference', 'provider': 'fixture', 'model': 'fixture-model'})
    monkeypatch.setattr(acceptance, 'SecretStore', lambda *_: SimpleNamespace(get=lambda _: token))
    monkeypatch.setattr(acceptance, 'save_config', lambda *_: None)
    monkeypatch.setattr(acceptance, 'kernel_fingerprint', lambda: {'fixture': 'unchanged'})
    app = SimpleNamespace(config=SimpleNamespace(_manager=SimpleNamespace()), tasks={})
    monkeypatch.setattr(host, 'AppState', lambda: app)
    httpd = SimpleNamespace(server_port=1, serve_forever=lambda: None, shutdown=lambda: None, server_close=lambda: None)
    monkeypatch.setattr(host, 'MingJingServer', lambda *_: httpd)
    task = {'id': 'controlled-web-run', 'out': str(tmp_path/'controlled-output'), 'status': 'queued'}
    result = {'mode': 'harness', 'model': {'status': 'success', 'binding_submissions': {'planner': 10, 'verifier': 10}},
        'summary': {'verified': 0, 'violated': 0, 'abstained': 23}, 'partial': False}
    calls = []
    final_task_status = {'value': 'completed'}

    class Proof:
        def __init__(self, *_):
            self.records = calls

        def request(self, method, path, *_args, **_kwargs):
            calls.append((method, path))
            if path == '/api/config':
                return {'capabilities': {'pdf': True}}
            if path == '/api/run':
                return dict(task)
            if path == '/api/runs/'+task['id']:
                return dict(task, status=final_task_status['value'])
            if path.endswith('/results'):
                return result
            raise AssertionError('Failed numerical acceptance must not execute replay or further actions')

    monkeypatch.setattr(acceptance, 'HTTPProof', Proof)
    args = SimpleNamespace(source_run=source, out=tmp_path/'new-failed-receipt', timeout=30)
    return SimpleNamespace(args=args, task=task, result=result, calls=calls,
                           final_task_status=final_task_status, host=host, token=token)


def test_failed_numeric_vertical_slice_keeps_actual_run_counts_and_not_executed_replays(controlled_web):
    fixture = controlled_web
    row = acceptance.execute(fixture.args)
    persisted = json.loads((fixture.args.out/'acceptance.json').read_text(encoding='utf-8'))
    assert row == persisted and row['status'] == 'failed'
    assert row['run_id'] == fixture.task['id'] and row['out'] == fixture.task['out']
    assert row['task_status'] == 'completed' and row['partial'] is False
    assert row['summary'] == fixture.result['summary'] and row['model_run'] == fixture.result['model']
    assert row['kernel_replay'] == row['agent_replay'] == {'status': 'not_executed'}
    assert row['http_requests'] == len(fixture.calls) == 4
    assert row['error'].endswith('Real A/B numeric vertical slice incomplete')
    assert row['user_profile_unchanged'] and row['kernel_unchanged']
    assert fixture.token not in json.dumps(row)


def test_registered_task_failure_keeps_registration_and_http_count(controlled_web):
    fixture = controlled_web
    fixture.final_task_status['value'] = 'cancelled'
    row = acceptance.execute(fixture.args)
    assert row['status'] == 'failed' and row['task_status'] == 'cancelled'
    assert row['run_id'] == fixture.task['id'] and row['out'] == fixture.task['out']
    assert row['http_requests'] == len(fixture.calls) == 3
    assert row['kernel_replay'] == row['agent_replay'] == {'status': 'not_executed'}
    assert 'model_run' not in row and 'summary' not in row


def test_failure_before_http_initialization_marks_usage_unknown_without_echoing_key(controlled_web, monkeypatch):
    fixture = controlled_web
    def fail_server(*_):
        raise HTTPError('https://fixture.invalid/'+fixture.token, 401, 'private '+fixture.token, {}, io.BytesIO())
    monkeypatch.setattr(fixture.host, 'MingJingServer', fail_server)
    row = acceptance.execute(fixture.args)
    assert row['status'] == 'failed' and row['http_requests'] == 'unknown'
    assert row['kernel_replay'] == row['agent_replay'] == {'status': 'not_executed'}
    assert 'run_id' not in row and 'out' not in row
    assert fixture.token not in json.dumps(row) and row['error'] == 'HTTP request failed (status 401)'
