"""Real local HTTP acceptance for source/frozen analysis and native path UI."""
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import native_picker, server
from mingjing.replay import replay_run


@pytest.fixture
def local_web(tmp_path, monkeypatch):
    monkeypatch.setenv('MINGJING_HOME', str(tmp_path / 'profile'))
    state = server.AppState()
    httpd = server.MingJingServer(('127.0.0.1', 0), state)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{httpd.server_port}'

    def request(path, value=None, method=None):
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8') if value is not None else None
        query = urllib.request.Request(base + path, data=raw, method=method,
                                       headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(query, timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))

    try:
        yield state, request
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(2)


@pytest.mark.parametrize('frozen', [False, True], ids=['source-child', 'frozen-in-process'])
def test_analysis_accepts_corpus_without_draft_and_replays_metrics(local_web, monkeypatch, frozen):
    pytest.importorskip('fitz')
    if frozen:
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
    else:
        monkeypatch.delattr(sys, 'frozen', raising=False)
    state, request = local_web
    sample = request('/api/demo', {}, 'POST')
    payload = {key: sample[key] for key in ('corpus', 'company', 'period', 'report_date')}
    payload.update(task_type='analysis', mode='kernel')
    launched = request('/api/run', payload, 'POST')
    task = state.get_task(launched['id'])
    task.thread.join(10)
    assert task.status == 'completed', task.error
    result = request(f'/api/runs/{task.id}/results')
    assert result['task_type'] == 'analysis'
    assert result['claims'] == [] and result['results'] == []
    draft = Path(result['draft'])
    assert draft.parent == task.out / 'input'
    assert draft.name == 'financial_analysis.md'
    assert any(event['event'] == 'analysis_intent' and len(event['sha256']) == 64 for event in task.events)
    metrics = result['analysis']['metrics']
    revenue = next(item for item in metrics if item['period'] == '2024Y' and item['metric'] == 'REVENUE')
    margin = next(item for item in metrics if item['period'] == '2024Y' and item['metric'] == 'GROSS_MARGIN')
    assert revenue['status'] == 'verified' and revenue['value'] == '1000000000.00'
    assert margin['status'] == 'verified' and margin['value'] == '0.25'
    assert margin['formula'] == '(REVENUE-COST)/REVENUE'
    assert len(margin['operands']) == 2 and margin['evidence']
    assert all(identifier in result['evidence_resources'] for identifier in margin['evidence'])
    for resource in result['evidence_resources'].values():
        assert (task.out / resource['image']).is_file()
    replay = request('/api/replay', {'run': str(task.out)}, 'POST')
    assert replay['status'] == 'passed' and replay['numeric_equal']
    assert replay['analysis_recomputed'] and replay['analysis_metrics_recomputed'] == len(metrics)
    assert replay['analysis_digest'] == replay['original_analysis_digest']
    assert replay['claims_recomputed'] == 0

    # Changing only the analysis value must fail the numeric comparison even
    # though this material task has no draft claims to recompute.
    revenue['value'] = '999999'
    (task.out / 'results.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **kw: pytest.fail('analysis replay used network'))
    failed = replay_run(task.out)
    assert failed['status'] == 'failed' and not failed['numeric_equal']
    assert any(item['field'].startswith('analysis.metrics[') and item['field'].endswith('.value') for item in failed['differences'])


def test_check_still_requires_actual_draft(local_web):
    _state, request = local_web
    with pytest.raises(urllib.error.HTTPError) as response:
        request('/api/run', {'corpus': 'unused', 'task_type': 'check'}, 'POST')
    assert response.value.code == 400
    assert json.loads(response.value.read())['fields'] == ['draft']


def test_native_picker_cancel_does_not_change_config(local_web, monkeypatch):
    state, request = local_web
    original = state.config.get()
    monkeypatch.setattr(native_picker, 'pick_path', lambda kind: None)
    assert request('/api/paths/pick', {'kind': 'file'}, 'POST') == {'cancelled': True}
    assert state.config.get() == original


def test_native_picker_respects_root_kind_and_parameter_guards(local_web, monkeypatch, tmp_path):
    state, request = local_web
    allowed = tmp_path / 'allowed'
    allowed.mkdir()
    inside = allowed / '财报.txt'
    inside.write_text('锁定材料', encoding='utf-8')
    outside = tmp_path / 'outside.txt'
    outside.write_text('不可选择', encoding='utf-8')
    state.config.update({'security': {'allow_external_paths': False,
                                     'allowed_roots': [str(allowed), str(server._default_root())]}})
    monkeypatch.setattr(native_picker, 'pick_path', lambda kind: str(inside))
    assert request('/api/paths/pick', {'kind': 'file'}, 'POST')['path'] == str(inside.resolve())
    with pytest.raises(urllib.error.HTTPError) as wrong_kind:
        request('/api/paths/pick', {'kind': 'dir'}, 'POST')
    assert wrong_kind.value.code == 400
    monkeypatch.setattr(native_picker, 'pick_path', lambda kind: str(outside))
    with pytest.raises(urllib.error.HTTPError) as wrong_root:
        request('/api/paths/pick', {'kind': 'file'}, 'POST')
    assert wrong_root.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as extra_path:
        request('/api/paths/pick', {'kind': 'file', 'path': str(inside)}, 'POST')
    assert extra_path.value.code == 400
    with pytest.raises(ValueError, match='allowed_roots'):
        state.config.update({'security': {'allowed_roots': []}})
    assert state.config.get()['security']['allowed_roots']


def test_native_picker_serializes_dialogs_and_releases_lock(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(native_picker, 'os', SimpleNamespace(name='nt'))

    def dialog(kind):
        entered.set()
        assert release.wait(3)
        return None

    monkeypatch.setattr(native_picker, '_windows_pick', dialog)
    first = threading.Thread(target=native_picker.pick_path, args=('file',))
    first.start()
    assert entered.wait(3)
    try:
        with pytest.raises(ValueError, match='已有路径选择窗口'):
            native_picker.pick_path('dir')
    finally:
        release.set()
        first.join(3)
    assert not first.is_alive()
    assert native_picker.pick_path('dir') is None


def _completed_demo(local_web, monkeypatch):
    state, request = local_web
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    sample = request('/api/demo', {}, 'POST')
    launched = request('/api/run', sample, 'POST')
    task = state.get_task(launched['id'])
    task.thread.join(10)
    assert task.status == 'completed', task.error
    return task


def test_session_get_persists_restart_interruption_and_resume_preserves_turn(local_web, monkeypatch):
    _completed_demo(local_web, monkeypatch)
    state, request = local_web
    run_id = next(iter(state.tasks))
    session = request('/api/sessions', {'run_id': run_id}, 'POST')
    sid = session['session_id']
    interrupted = state.sessions.get(sid)
    interrupted['status'] = 'running'
    interrupted['owner_instance'] = 'old-process'
    interrupted['pending_turn'] = {'id': 'pending-001', 'question': '请解释营业收入', 'attempts': 1}
    interrupted['messages'].append({'role': 'user', 'text': '请解释营业收入', 'turn_id': 'pending-001'})
    state.sessions.save(interrupted)
    restarted = server.AppState()
    restored = restarted.sessions.get(sid)
    assert restored['status'] == 'interrupted'
    assert json.loads(restarted.sessions.path(sid).read_text(encoding='utf-8'))['status'] == 'interrupted'
    assert restarted.sessions.list()[0]['status'] == 'interrupted'
    assert len(restored['messages']) == 1 and restored['pending_turn']['id'] == 'pending-001'
    with pytest.raises(ValueError, match='先恢复'):
        restarted.sessions.submit(sid, '另一问题')

    entered, release = threading.Event(), threading.Event()

    def prepare_answer(session_id, question):
        entered.set()
        assert question == '请解释营业收入'
        assert release.wait(3)
        with restarted.sessions.lock:
            saved = restarted.sessions.get(session_id)
            saved['messages'].append({'role': 'assistant', 'text': 'fixture response'})
            saved.pop('pending_turn', None)
            saved['status'] = 'ready'
            restarted.sessions.save(saved)

    monkeypatch.setattr(restarted.sessions, '_answer_inner', prepare_answer)
    resumed = restarted.sessions.resume(sid)
    assert resumed['status'] == 'running' and resumed['pending_turn']['attempts'] == 2
    assert entered.wait(3)
    assert restarted.sessions.get(sid)['status'] == 'running'
    assert sid not in restarted.sessions.active and sid in restarted.sessions.inflight
    with pytest.raises(ValueError, match='上一条消息'):
        restarted.sessions.submit(sid, '重复提交')
    with pytest.raises(ValueError, match='仍在运行'):
        restarted.sessions.resume(sid)
    release.set()
    deadline = time.monotonic() + 3
    while sid in restarted.sessions.inflight and time.monotonic() < deadline:
        time.sleep(0.01)
    final = restarted.sessions.get(sid)
    assert final['status'] == 'ready'
    assert len([item for item in final['messages'] if item['role'] == 'user']) == 1
    assert sid not in restarted.sessions.inflight and sid not in restarted.sessions.cancel_events
    assert 'owner_instance' not in restarted.sessions.public(final)


def test_session_cancel_during_preparation_does_not_allow_overlapping_resume(local_web, monkeypatch):
    task = _completed_demo(local_web, monkeypatch)
    state, request = local_web
    sid = request('/api/sessions', {'run_id': task.id}, 'POST')['session_id']
    entered, release = threading.Event(), threading.Event()

    def blocked_prepare(session_id, question):
        entered.set()
        assert release.wait(3)
        assert state.sessions.cancel_events[session_id].is_set()
        raise InterruptedError('preparation cancelled')

    monkeypatch.setattr(state.sessions, '_answer_inner', blocked_prepare)
    request(f'/api/sessions/{sid}/messages', {'question': '核查结果如何？'}, 'POST')
    assert entered.wait(3)
    assert request(f'/api/sessions/{sid}')['status'] == 'running'
    assert request(f'/api/sessions/{sid}/cancel', {}, 'POST')['status'] == 'cancelled'
    with pytest.raises(urllib.error.HTTPError) as overlapping:
        request(f'/api/sessions/{sid}/resume', {}, 'POST')
    assert overlapping.value.code == 400
    release.set()
    deadline = time.monotonic() + 3
    while sid in state.sessions.inflight and time.monotonic() < deadline:
        time.sleep(0.01)
    saved = request(f'/api/sessions/{sid}')
    assert saved['status'] == 'cancelled'
    assert saved['pending_turn']['question'] == '核查结果如何？'
    assert saved['messages'][-1]['text'] == '追问准备已取消'


def test_run_activity_projects_native_tools_without_private_payload(local_web, monkeypatch):
    task = _completed_demo(local_web, monkeypatch)
    _state, request = local_web
    detail = {'sessionId': 'real-role-session', 'event': {'type': 'tool/call', 'data': {
        'name': 'mj_get_fact', 'callId': 'call-001', 'arguments': '{"api_key":"not-public"}'}}}
    task.emit('agent_event', detail=detail)
    task.emit('agent_event', detail={'sessionId': 'real-role-session', 'event': {'type': 'tool/result', 'data': {
        'message': {'source': {'callId': 'call-001'}, 'content': [{'type': 'tool-result', 'isError': False,
        'content': [{'type': 'text', 'text': 'raw financial/model payload must not appear'}]}]}}}})
    task.emit('agent_event', detail={'sessionId': 'real-role-session', 'event': {'type': 'assistant/message',
        'data': {'content': 'private thought or unsupported conclusion', 'reasoning_content': 'private-reasoning'}}})
    projected = request(f'/api/runs/{task.id}/events')
    assert projected['event_format'] == 'activity.v1'
    calls = [event for event in projected['events'] if event['event'] == 'tool_call']
    results = [event for event in projected['events'] if event['event'] == 'tool_result']
    assert calls[-1]['tool'] == results[-1]['tool'] == 'mj_get_fact'
    assert calls[-1]['call_id'] == results[-1]['call_id'] == 'call-001'
    assert calls[-1]['status'] == 'running' and results[-1]['status'] == 'completed'
    assert all(text not in json.dumps(projected) for text in ('not-public', 'raw financial', 'private thought', 'private-reasoning'))
    assert projected['raw_event_count'] > len(projected['events'])
    assert task.events[-3]['detail'] == detail


def test_shutdown_cancels_preparing_sessions_without_changing_ready_history(local_web, monkeypatch):
    task = _completed_demo(local_web, monkeypatch)
    state, request = local_web
    ready = request('/api/sessions', {'run_id': task.id}, 'POST')['session_id']
    active = request('/api/sessions', {'run_id': task.id}, 'POST')['session_id']
    before = state.sessions.path(ready).read_bytes()
    entered = threading.Event()

    def preparing(session_id, question):
        entered.set()
        assert state.sessions.cancel_events[session_id].wait(3)
        raise InterruptedError('shutdown stopped preparation')

    monkeypatch.setattr(state.sessions, '_answer_inner', preparing)
    request(f'/api/sessions/{active}/messages', {'question': '解释证据'}, 'POST')
    assert entered.wait(3)
    assert state.sessions.workers[active].is_alive()
    response = request('/api/shutdown', {}, 'POST')
    assert response['ok']
    assert state.sessions.wait_running(3)
    assert state.sessions.get(active)['status'] == 'cancelled'
    assert state.sessions.path(ready).read_bytes() == before
    assert not state.sessions.workers and not state.sessions.active
    started = time.monotonic()
    assert state.sessions.wait_running(3)
    assert time.monotonic() - started < 0.1


def test_server_close_bounds_hung_diagnostic_and_kills_owned_child(local_web):
    state, _request = local_web
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    entered, release = threading.Event(), threading.Event()
    diagnostic = SimpleNamespace(run_id='diagnostic-hung-fixture', cancelled=threading.Event(),
                                 runtime=SimpleNamespace(_process=child))

    def hung_handler():
        state.diagnostic_lifecycle('start', diagnostic)
        entered.set()
        try:
            release.wait(15)
        finally:
            state.diagnostic_lifecycle('end', diagnostic)

    worker = threading.Thread(target=hung_handler, daemon=True)
    worker.start()
    assert entered.wait(3)
    second = server.MingJingServer(('127.0.0.1', 0), state)
    try:
        started = time.monotonic()
        second.server_close()
        elapsed = time.monotonic() - started
        assert elapsed < 4.5
        assert diagnostic.cancelled.is_set()
        assert child.wait(timeout=2) != 0
    finally:
        release.set()
        worker.join(2)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)
        second.server_close()
