"""Custom paths/redirect policy through the actual native model adapters."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mingjing.harness_client import HarnessRuntime, runtime_fingerprint
from test_native_runtime_safety import loop_carrier


@pytest.fixture
def endpoints():
    state = {'hits': [], 'redirect': None, 'http_error': False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            state['hits'].append((self.server.label, self.path, self.headers.get('Authorization')))
            if self.server.label == 'source' and state['redirect'] and self.path != '/canonical':
                self.send_response(307)
                self.send_header('Location', state['redirect'])
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if state['http_error']:
                raw = json.dumps({'error': {'message': 'Echo '+self.headers.get('Authorization', '')}}).encode()
                self.send_response(401)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for data in (
                {'id': 'local', 'model': 'fixture', 'choices': [{'index': 0, 'delta': {'content': 'Tool-neutral local fixture.'}, 'finish_reason': None}]},
                {'id': 'local', 'model': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}},
            ):
                self.wfile.write(('data: '+json.dumps(data)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')

    servers = []
    for label in ('source', 'foreign'):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.label = label
        threading.Thread(target=lambda srv=server: srv.serve_forever(poll_interval=.02), daemon=True).start()
        servers.append(server)
    try:
        yield state, *(f'http://127.0.0.1:{server.server_port}' for server in servers)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def execute(tmp_path, protocol, base_url):
    identity = {'session_id': 'route-probe', 'run_id': 'route-test', 'role': 'probe', 'resource_lock': 'locked'}
    def incoming(method, params):
        assert method == 'mj/credentials.resolve'
        assert all(params.get(key) == value for key, value in identity.items())
        assert params['credential_ref'] == 'fixture-key' and params['provider_id'] == 'fixture'
        return {'api_key': 'local-only-secret'}
    runtime = HarnessRuntime(tmp_path/'home', incoming, expected_runtime_lock=runtime_fingerprint())
    try:
        runtime.start()
        runtime.open_session(**identity, route={'provider': 'fixture', 'protocol': protocol, 'model': 'fixture',
            'credential_ref': 'fixture-key', 'base_url': base_url+'/api', 'chat_path': '/custom-completions',
            'retries': 0, 'timeout_s': 10, 'max_tokens': 256, 'reasoning': 'off'},
            tool_definitions=[], allowed_tools=[], model_cache=False, max_steps=2)
        return runtime.prompt(identity['session_id'], 'Return a local diagnostic response.', timeout_s=15)
    finally:
        runtime.close()


@pytest.mark.parametrize('protocol', ['deepseek', 'openai-completions'])
def test_custom_chat_path_reaches_real_protocol_adapter(tmp_path, endpoints, loop_carrier, protocol):
    state, origin, _ = endpoints
    result = execute(tmp_path, protocol, origin)
    assert result['finish_reason'] == 'completed', result
    assert state['hits'] == [('source', '/api/custom-completions', 'Bearer local-only-secret')]


@pytest.mark.parametrize('protocol', ['deepseek', 'openai-completions'])
def test_cross_origin_redirect_never_forwards_material_or_key(tmp_path, endpoints, loop_carrier, protocol):
    state, origin, foreign = endpoints
    state['redirect'] = foreign+'/steal'
    result = execute(tmp_path, protocol, origin)
    assert result['finish_reason'] == 'error'
    assert state['hits'] == [('source', '/api/custom-completions', 'Bearer local-only-secret')]
    assert 'local-only-secret' not in json.dumps(result)


def test_same_origin_redirect_remains_functional(tmp_path, endpoints, loop_carrier):
    state, origin, _ = endpoints
    state['redirect'] = origin+'/canonical'
    result = execute(tmp_path, 'deepseek', origin)
    assert result['finish_reason'] == 'completed'
    assert [row[1] for row in state['hits']] == ['/api/custom-completions', '/canonical']


def test_provider_http_errors_do_not_echo_credential_in_native_session(tmp_path, endpoints, loop_carrier):
    state, origin, _ = endpoints
    state['http_error'] = True
    result = execute(tmp_path, 'deepseek', origin)
    assert result['finish_reason'] == 'error' and 'local-only-secret' not in json.dumps(result)
    for path in (tmp_path/'home/sessions').rglob('*.jsonl'):
        assert 'local-only-secret' not in path.read_text(encoding='utf-8')
