"""Diagnostic boundary tests; fixtures do not certify real provider access."""
import copy
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import provider_api
from mingjing.config import AppConfig


@pytest.fixture
def boundary(monkeypatch):
    captured = SimpleNamespace(instances=[], specs=[], prompts=[], audits=[], reply='MingJing connected', finish='stop')

    class AgentBoundary:
        def __init__(self, request, cfg, secret_resolver, out, **kwargs):
            self.config, self.request, self.out = cfg, request, out
            self.budget = dict(cfg['agent']['budget'])
            self.cancelled = None
            self.native_request_count, self.total_tokens, self.usage_known = 1, 42, True
            self.resource_lock = 'locked-fictitious-resource'
            self.runtime = None
            self.audit = SimpleNamespace(emit=lambda *a, **kw: captured.audits.append((a, kw)))
            self.dispatcher = SimpleNamespace(sessions={}, register_session=self.register_session)
            captured.instances.append(self)

        def register_session(self, sid, role, ids):
            self.dispatcher.sessions[sid] = {'active': True, 'role': role}

        def prepare(self):
            pass

        def start_runtime(self):
            self.runtime = SimpleNamespace(open_session=lambda **spec: captured.specs.append(spec),
                                           prompt=self.prompt, close_session=lambda sid: None,
                                           close=lambda: None)

        def prompt(self, sid, text, **kwargs):
            captured.prompts.append(text)
            return {'final_response': captured.reply, 'finish_reason': captured.finish}

    monkeypatch.setattr(provider_api, 'FinancialAgent', AgentBoundary)
    return captured


def config(protocol):
    return AppConfig.from_dict({'providers': {'selected': {'protocol': protocol, 'base_url': 'https://example.invalid/v1',
        'credential_ref': 'private-key-reference', 'models': [{'id': 'chosen-model', 'reasoning': 'auto'}],
        'material_access': False}}, 'agent': {'routing': {'default': {'provider': 'selected', 'model': 'chosen-model'}}}}).ensure_defaults().to_dict()


@pytest.mark.parametrize('protocol', ['deepseek', 'openai-completions', 'openai-responses', 'anthropic-messages'])
def test_plain_connection_uses_selected_native_protocol_with_zero_tools(tmp_path, boundary, protocol):
    original = config(protocol)
    before = copy.deepcopy(original)
    result = provider_api.test_connection(original, 'selected', lambda ref: 'private-token-never-exported', tmp_path)
    assert result['ok'] and result['status'] == 'passed'
    assert result['protocol'] == protocol and result['model'] == 'chosen-model'
    assert result['agent_capability'] == 'not_tested' and result['tool_roundtrip'] is False
    assert result['usage'] == {'model_requests': 1, 'tokens': 42}
    assert original == before
    spec = boundary.specs[0]
    assert spec['route']['protocol'] == protocol and spec['route']['model'] == 'chosen-model'
    assert spec['tool_definitions'] == [] and spec['allowed_tools'] == []
    assert spec['budget']['max_model_calls'] == 1 and spec['budget']['max_tool_calls'] == 0
    assert boundary.prompts == ['请仅回复：MingJing connected']
    assert 'private-token-never-exported' not in json.dumps(result)
    assert all(not session['active'] for session in boundary.instances[0].dispatcher.sessions.values())


@pytest.mark.parametrize('reply,finish,reason', [('', 'stop', 'empty_content'), (None, 'stop', 'empty_content'),
    ('reasoning-only', 'max-tokens', 'incomplete_turn'), ('OK', None, 'incomplete_turn'), ('OK', 'error', 'incomplete_turn')])
def test_connection_rejects_empty_or_incomplete_native_turn(tmp_path, boundary, reply, finish, reason):
    boundary.reply, boundary.finish = reply, finish
    result = provider_api.test_connection(config('deepseek'), 'selected', lambda ref: '', tmp_path)
    assert result['ok'] is False and result['reason'] == reason
    assert result['agent_capability'] == 'not_tested'


def test_connection_reports_unknown_usage_and_does_not_echo_response(tmp_path, boundary):
    boundary.reply = 'Provider reflected a secret-looking arbitrary payload'
    original = provider_api.FinancialAgent

    def unknown_usage(*args, **kwargs):
        agent = original(*args, **kwargs)
        agent.usage_known = False
        return agent

    from unittest.mock import patch
    with patch.object(provider_api, 'FinancialAgent', unknown_usage):
        result = provider_api.test_connection(config('openai-responses'), 'selected', lambda ref: '', tmp_path)
    assert result['usage']['tokens'] == 'unknown'
    assert boundary.reply not in json.dumps(result)


SOURCE_RUNTIME = Path(__file__).resolve().parents[2] / 'agent架构参考/deepseek-harness-master/python/sdk-runtime/src/deepseek_harness_runtime/runtime/node/runtime-bootstrap.mjs'


@pytest.mark.skipif(not shutil.which('node') or not SOURCE_RUNTIME.is_file(), reason='Pinned source runtime unavailable')
def test_plain_probe_runs_real_native_loop_against_local_sse_fixture(tmp_path):
    """A native-loop fixture receipt, explicitly not real vendor acceptance."""
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'path': self.path, 'body': body})
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            chunks = [
                {'id': 'connection-fixture', 'model': 'chosen-model', 'choices': [{'index': 0,
                    'delta': {'content': 'MingJing connected'}, 'finish_reason': None}]},
                {'id': 'connection-fixture', 'model': 'chosen-model', 'choices': [{'index': 0,
                    'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}},
            ]
            for chunk in chunks:
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()

    endpoint = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    worker = threading.Thread(target=endpoint.serve_forever, daemon=True)
    worker.start()
    try:
        cfg = config('deepseek')
        cfg['providers']['selected']['base_url'] = f'http://127.0.0.1:{endpoint.server_port}'
        cfg['providers']['selected']['retries'] = 0
        result = provider_api.test_connection(cfg, 'selected', lambda ref: 'keyless-local-fixture', tmp_path)
        assert result['ok'] and result['native_runtime'], result
        assert result['finish_reason'] == 'completed'
        assert result['agent_capability'] == 'not_tested' and result['tool_roundtrip'] is False
        assert result['usage'] == {'model_requests': 1, 'tokens': 15}
        assert len(requests) == 1 and requests[0]['path'] == '/chat/completions'
        assert not requests[0]['body'].get('tools')
        assert cfg['providers']['selected']['material_access'] is False
        assert 'keyless-local-fixture' not in (Path(result['artifact_path']) / 'connection.json').read_text(encoding='utf-8')
    finally:
        endpoint.shutdown()
        endpoint.server_close()
        worker.join(2)
