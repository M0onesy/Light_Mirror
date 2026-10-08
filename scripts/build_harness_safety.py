"""Exercise real AgentLoop cancellation, crashing and step guards without keys."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.harness_client import HarnessError, HarnessRuntime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'runs/harness-safety')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    state = {'requests': 0, 'tools': 0}
    started, release, settled = threading.Event(), threading.Event(), threading.Event()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            state['requests'] += 1
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            events = [
                {'id': 'fixture', 'model': 'fixture', 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': f"slow-{state['requests']}", 'type': 'function', 'function': {'name': 'mj_read_context', 'arguments': '{}'}}]}, 'finish_reason': None}]},
                {'id': 'fixture', 'model': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}},
            ]
            for event in events:
                self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    definition = {'name': 'mj_read_context', 'description': 'Read only a locked financial resource.', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
    base = {'role': 'probe', 'resource_lock': 'safety-locked', 'run_id': 'native-safety', 'max_steps': 2,
            'tool_definitions': [definition], 'allowed_tools': [definition['name']], 'model_cache': False,
            'route': {'provider': 'fixture', 'model': 'fixture', 'protocol': 'deepseek', 'credential_ref': 'fixture',
                      'base_url': f'http://127.0.0.1:{server.server_port}', 'reasoning': 'off', 'timeout_s': 10, 'retries': 0}}

    def dispatch(method, params):
        if method == 'mj/credentials.resolve':
            return {'api_key': 'keyless-fixture'}
        assert method == 'mj/tool.dispatch'
        assert params['role'] == 'probe' and params['resource_lock'] == 'safety-locked'
        state['tools'] += 1
        started.set()
        if not release.wait(10):
            raise RuntimeError('Fixture host operation was not released')
        settled.set()
        return {'evidence_id': 'fixture:p1', 'value': 'masked'}

    results = {}
    runtime = HarnessRuntime(args.out / 'cancel-home', dispatch)
    runtime.start()
    runtime.open_session(session_id='native-cancel', **base)
    worker_result = {}

    def prompt_cancel():
        try:
            worker_result['run'] = runtime.prompt('native-cancel', 'Read the locked financial resource.', timeout_s=15)
        except HarnessError as exc:
            worker_result['error'] = str(exc)

    worker = threading.Thread(target=prompt_cancel)
    worker.start()
    assert started.wait(10), 'The original native tool was not called'
    timer = threading.Timer(1.2, release.set)
    timer.start()
    begin = time.monotonic()
    cancelled = runtime.cancel('native-cancel')
    elapsed = time.monotonic() - begin
    assert cancelled['quiescent'] and settled.is_set() and elapsed >= 1, 'Cancellation returned before the financial host settled'
    worker.join(5)
    assert not worker.is_alive()
    results['cancel'] = {'quiescent': True, 'host_settled_before_return': True, 'wait_s': round(elapsed, 3)}
    runtime.close()

    started.clear()
    release.clear()
    settled.clear()
    runtime = HarnessRuntime(args.out / 'crash-home', dispatch)
    runtime.start()
    runtime.open_session(session_id='native-crash', **base)
    crash_result = {}

    def prompt_crash():
        try:
            runtime.prompt('native-crash', 'Read the locked financial resource.', timeout_s=30)
        except HarnessError as exc:
            crash_result['error'] = str(exc)

    worker = threading.Thread(target=prompt_crash)
    worker.start()
    assert started.wait(10)
    begin = time.monotonic()
    runtime._process.kill()
    worker.join(3)
    elapsed = time.monotonic() - begin
    release.set()
    assert not worker.is_alive() and 'subprocess stopped' in crash_result.get('error', ''), 'Crash did not fail promptly'
    runtime.close()
    results['crash'] = {'failed_promptly': True, 'elapsed_s': round(elapsed, 3), 'reason': crash_result['error']}

    started.clear()
    release.set()
    runtime = HarnessRuntime(args.out / 'step-home', dispatch)
    runtime.start()
    runtime.open_session(session_id='native-step', **{**base, 'max_steps': 1})
    requests_before, tools_before = state['requests'], state['tools']
    run = runtime.prompt('native-step', 'Read repeatedly.', timeout_s=15)
    runtime.close()
    assert state['requests'] - requests_before == 1 and state['tools'] - tools_before == 1
    reason = next(event['data']['reason'] for event in reversed(run['events']) if event['type'] == 'turn/end')
    assert reason == {'kind': 'aborted', 'reason': {'kind': 'hook', 'reason': 'MingJing step budget exhausted'}}, reason
    results['step_guard'] = {'model_requests': 1, 'tool_calls': 1, 'finish_reason': run['finish_reason']}
    server.shutdown()
    server.server_close()
    receipt = {'status': 'passed_native_safety', **results}
    (args.out / 'acceptance.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
