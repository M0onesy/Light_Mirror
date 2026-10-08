"""Actual original Harness loop with a local model fixture, then offline replay.

This validates transport/tool/cache behavior, not live API or model quality.
"""
from __future__ import annotations

import copy
import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mingjing.agent_replay import logical_session_trace
from mingjing.financial_tools import tool_definitions
from mingjing.harness_client import HarnessRuntime, runtime_fingerprint
from mingjing.providers import ModelConfig, ProviderConfig, resolve_route

from test_binding_argument_contract import binding, bridge
from test_native_runtime_safety import loop_carrier


@pytest.mark.parametrize('invalid_case', ['schema', 'source_conflict'])
def test_native_rejected_binding_feeds_model_and_hash_trace_replays_offline(tmp_path, loop_carrier, invalid_case):
    online, claim = bridge(tmp_path/'online')
    base = binding(claim)
    state = {'requests': 0, 'saw_rejection': False}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            step = state['requests']
            state['requests'] += 1
            if step == 0:
                # The locked native serializer omits maxLength, so this
                # reaches authoritative host validation rather than being
                # pre-rejected by the runtime's enum validation.
                selected = ({**base, 'period': 'p'*201} if invalid_case == 'schema'
                            else {**base, 'account': 'COST'})
            elif step == 1:
                expected_code = 'invalid_binding_arguments' if invalid_case == 'schema' else 'binding_source_conflict'
                state['saw_rejection'] = expected_code in json.dumps(body)
                assert state['saw_rejection'], 'Model must observe the host rejection before repairing'
                if invalid_case == 'source_conflict':
                    assert 'source_dimensions' in json.dumps(body) and 'REVENUE' in json.dumps(body)
                selected = base
            else:
                selected = None
            if selected is not None:
                delta = {'tool_calls': [{'index': 0, 'id': f'fixture-{step}', 'type': 'function',
                    'function': {'name': 'mj_submit_binding', 'arguments': json.dumps({'mention_id': claim.claim_id, 'binding': selected})}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'content': 'Finished controlled repair.'}, 'stop'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for chunk in (
                {'id': 'fixture', 'model': 'fixture', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {'id': 'fixture', 'model': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}},
            ):
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    config = copy.deepcopy(online.config)
    config['providers'] = {'fixture': asdict(ProviderConfig(protocol='deepseek',
        base_url=f'http://127.0.0.1:{provider.server_port}', credential_ref='fixture_key',
        models=[ModelConfig(id='fixture', reasoning='off')], timeout_s=10, retries=0))}
    config['agent']['routing']['default'] = {'provider': 'fixture', 'model': 'fixture'}
    online.config = config
    online.secret_resolver = lambda _: 'keyless-only'

    def run(owner, home):
        runtime = HarnessRuntime(home, owner._incoming, event_handler=owner._event,
                                 expected_runtime_lock=runtime_fingerprint())
        runtime.start()
        try:
            definitions = [tool for tool in tool_definitions('planner') if tool['name'] == 'mj_submit_binding']
            runtime.open_session(session_id='planner', role='planner', run_id=owner.run_id,
                resource_lock=owner.resource_lock, route=resolve_route(config, 'planner'),
                tool_definitions=definitions, allowed_tools=['mj_submit_binding'], max_steps=4,
                replay_only=owner.replay_only)
            result = runtime.prompt('planner', 'Exercise the controlled host rejection and bounded repair.', timeout_s=20)
            assert result['native_loop'] and result['finish_reason'] == 'completed', result
        finally:
            runtime.close()

    try:
        run(online, tmp_path/'online-runtime')
    finally:
        provider.shutdown()
        provider.server_close()
    assert state == {'requests': 3, 'saw_rejection': True}
    assert [event['status'] for event in online.dispatcher.trace] == ['denied', 'success']
    rejection = online.dispatcher.trace[0]
    assert rejection['output']['status'] == 'rejected'
    assert set(rejection['input']) == {'input_sha256'}
    assert online.dispatcher.bindings['planner'][claim.claim_id] == base
    assert len(online.model_cache) == 3 and online.native_request_count == 3

    offline, _ = bridge(tmp_path/'offline')
    offline.config = config
    offline.model_cache = copy.deepcopy(online.model_cache)
    offline.replay_only = True
    offline.secret_resolver = lambda _: (_ for _ in ()).throw(AssertionError('Offline replay cannot resolve a key'))
    run(offline, tmp_path/'offline-runtime')
    assert offline.replay_cache_misses == [] and offline.native_request_count == 3
    assert offline.dispatcher.bindings == online.dispatcher.bindings
    records = [{'session_id': 'planner', 'role': 'planner', 'mention_ids': [claim.claim_id]}]
    assert logical_session_trace(offline.dispatcher.trace, records) == logical_session_trace(online.dispatcher.trace, records)
    assert state['requests'] == 3  # Provider was stopped before replay.
