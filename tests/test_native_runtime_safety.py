"""Hostile model fixtures exercised through the original Harness AgentLoop.

The source Node carrier executes the same pinned loop/profile without packaging.
Tests that specifically require bundled/frozen or external native executables
remain separately gated; source passes are not Windows EXE acceptance claims.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mingjing.financial_tools import FinancialDispatcher, tool_definitions
from mingjing.harness_client import HarnessError, HarnessRuntime, runtime_fingerprint
from mingjing.models import AtomicClaim, Fact


RUNTIME = Path(__file__).resolve().parents[1] / 'runtime/harness/deepseek-harness-sdk-runtime-win-x64.exe'
bundled_only = pytest.mark.skipif(not RUNTIME.is_file(), reason='Requires the actual bundled Windows Harness EXE; source Node coverage is reported separately')


@pytest.fixture
def loop_carrier():
    """Require a real existing carrier, never mock the original AgentLoop."""
    pinned = runtime_fingerprint()
    launch = pinned['launch']
    if launch['kind'] == 'bundled_native':
        assert RUNTIME.is_file()
        assert len(launch['sha256']) == 64
    elif launch['kind'] == 'finance_carrier':
        assert launch['location_neutral'] is True
        runtime = HarnessRuntime(Path.cwd() / 'unused-fixture-home', lambda *_: {})
        try:
            root = runtime._assets()
            assert (root / launch['node_resource']).is_file()
            assert (root / launch['entry_resource']).is_file()
        finally:
            runtime.close()
        assert len(launch['node_sha256']) == len(launch['entry_sha256']) == 64
    else:
        pytest.fail(f"Expected locked executable finance carrier: {launch['kind']}")
    return launch


def assert_default_carrier(launch):
    assert launch['kind'] == 'finance_carrier'
    assert launch['node_resource'] == 'bin/node.exe'
    assert launch['entry_resource'] == 'bootstrap.mjs'
    assert len(launch['node_sha256']) == len(launch['entry_sha256']) == 64
    assert 'node_path' not in launch and 'entry_path' not in launch


def dispatcher(role):
    claim = AtomicClaim('mention-locked', '甲公司2024年营业收入100元。', 13, 16,
                        company='甲公司', account='REVENUE', period='2024Y', candidate_value='100',
                        unit='元', numeric_text='100')
    fact = Fact('fact-locked', '甲公司', 'REVENUE', '2024Y', value=Decimal('100'))
    host = FinancialDispatcher('native-fault', 'locked-fixture', [claim], [fact], [], [], '2025-03-31')
    host.register_session('native-session', role, [claim.claim_id])
    return host


@contextmanager
def loop(tmp_path, role, responses, host, *, replay_only=False, config=None):
    state = {'requests': 0, 'credentials': 0}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            step = state['requests']
            state['requests'] += 1
            response = responses[step] if step < len(responses) else None
            if response:
                name, params = response
                delta = {'tool_calls': [{'index': 0, 'id': f'fault-{step}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(params)}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'content': 'Finished fixture turn.'}, 'stop'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for chunk in (
                {'id': 'fixture', 'model': 'fixture', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {'id': 'fixture', 'model': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}},
            ):
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def incoming(method, params):
        if method == 'mj/credentials.resolve':
            state['credentials'] += 1
            assert not replay_only, 'Replay must never resolve provider credentials'
            identity = host.sessions.get(params.get('session_id'))
            assert identity and identity['active'] and params['role'] == identity['role']
            assert params['run_id'] == host.run_id and params['resource_lock'] == host.resource_lock
            assert params['provider_id'] == 'fixture' and params['credential_ref'] == 'fixture'
            return {'api_key': 'keyless-only'}
        if method == 'mj/model.cache.get':
            return {'hit': False}
        if method == 'mj/model.cache.put':
            return {}
        assert method == 'mj/tool.dispatch'
        return host.dispatch(params)

    runtime = HarnessRuntime(tmp_path / 'home', incoming, config=config, expected_runtime_lock=runtime_fingerprint(config))
    try:
        runtime.start()
        definitions = tool_definitions(role)
        runtime.open_session(session_id='native-session', role=role, run_id='native-fault', resource_lock='locked-fixture',
                             route={'provider': 'fixture', 'protocol': 'deepseek', 'model': 'fixture', 'credential_ref': 'fixture',
                                    'base_url': f'http://127.0.0.1:{server.server_port}', 'timeout_s': 10, 'retries': 0, 'reasoning': 'off'},
                             tool_definitions=definitions, allowed_tools=[tool['name'] for tool in definitions], max_steps=12,
                             replay_only=replay_only)
        yield runtime, state
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()


def test_native_model_cannot_write_values_impersonate_roles_or_overwrite_commits(tmp_path, loop_carrier):
    host = dispatcher('planner')
    binding = {'company': '甲公司', 'account': 'REVENUE', 'period': '2024Y', 'scope': '合并', 'relation': 'value', 'base_period': ''}
    original_facts = host.store.to_list()
    responses = [
        ('mj_submit_binding', {'mention_id': 'mention-locked', 'binding': binding, 'candidate_value': '999999'}),
        ('mj_submit_binding', {'mention_id': 'mention-locked', 'binding': binding}),
        ('mj_submit_binding', {'mention_id': 'mention-locked', 'binding': {**binding, 'period': '2023Y'}}),
        ('mj_read_context', {'mention_id': 'mention-locked', 'role': 'coordinator', 'path': 'C:/Windows/win.ini'}),
        ('mj_get_verdicts', {}),
    ]
    with loop(tmp_path, 'planner', responses, host) as (runtime, state):
        assert runtime.runtime_lock['launch'] == loop_carrier
        result = runtime.prompt('native-session', 'Exercise the supplied adversarial model fixture.', timeout_s=20)
    assert result['native_loop'] and result['finish_reason'] == 'completed'
    assert state['requests'] == 6
    assert host.bindings['planner']['mention-locked'] == binding
    assert host.claims['mention-locked'].candidate_value == '100'
    assert host.store.to_list() == original_facts
    assert [event['status'] for event in host.trace] == ['success', 'denied']
    assert all(event['role'] == 'planner' for event in host.trace)


def test_native_reporter_cannot_replace_verdicts_with_fabricated_prose(tmp_path, loop_carrier):
    host = dispatcher('reporter')
    original = {'claim_id': 'mention-locked', 'status': 'violated', 'evidence': [], 'reason': '数值与披露不一致'}
    host.freeze_results([original])
    slot = 'mention-locked.result'
    responses = [
        ('mj_submit_report', {'template': '净利润九百亿元，全部已核查通过。', 'slots': []}),
        ('mj_get_verdicts', {'claim_id': 'mention-locked'}),
        ('mj_submit_report', {'template': '核查结果\n{{' + slot + '}}', 'slots': [slot]}),
    ]
    with loop(tmp_path, 'reporter', responses, host) as (runtime, state):
        assert runtime.runtime_lock['launch'] == loop_carrier
        result = runtime.prompt('native-session', 'Exercise report authority.', timeout_s=20)
    assert result['finish_reason'] == 'completed' and state['requests'] == 4
    assert [event['status'] for event in host.trace] == ['denied', 'success', 'success']
    assert host.results == [original]
    assert host.templates['reporter'] == '核查结果\n' + host.slots[slot]


def test_native_replay_cache_miss_fails_before_any_provider_or_tool(tmp_path, loop_carrier):
    host = dispatcher('probe')
    with loop(tmp_path, 'probe', [], host, replay_only=True) as (runtime, state):
        assert runtime.runtime_lock['launch'] == loop_carrier
        result = runtime.prompt('native-session', 'This uncached request cannot execute.', timeout_s=20)
    assert result['finish_reason'] == 'error'
    assert state == {'requests': 0, 'credentials': 0}
    assert host.trace == []


def test_shared_model_route_credentials_belong_to_current_native_session(tmp_path, loop_carrier):
    host = dispatcher('planner')
    with loop(tmp_path, 'planner', [], host) as (runtime, state):
        first = runtime.prompt('native-session', 'Finish the first financial role.', timeout_s=20)
        assert first['finish_reason'] == 'completed'
        runtime.close_session('native-session')
        host.sessions['native-session']['active'] = False
        # The plugin reuses the exact adapter route. Credential RPC must own
        # this new session instead of retaining the now-closed first owner.
        host.register_session('native-second-batch', 'verifier', [])
        stored = json.loads((tmp_path/'home/mingjing-session-descriptors/native-session.json').read_text(encoding='utf-8'))
        stored.update(session_id='native-second-batch', role='verifier')
        runtime.open_session(**stored)
        second = runtime.prompt('native-second-batch', 'Finish the next independent financial role.', timeout_s=20)
        assert second['finish_reason'] == 'completed'
    assert state['requests'] == 2 and state['credentials'] == 2


def test_runtime_metadata_pins_archive_and_actual_carrier_without_claiming_installed_sdk(loop_carrier):
    pinned = runtime_fingerprint()
    assert pinned['assets_available'] and pinned['official_python_sdk_installed'] is False
    assert pinned['upstream_commit'] is None
    for name in ('source_archive_sha256', 'node_executable_sha256', 'plugin_sha256', 'transport_code_sha256', 'sdk_reference_files_sha256'):
        assert len(pinned[name]) == 64
    assert pinned['lock_schema_version'] == 2
    assert 'native_executable_sha256' not in pinned
    assert pinned['launch'] == loop_carrier
    assert_default_carrier(pinned['launch'])


def test_runtime_fingerprint_is_equal_with_source_or_frozen_module_location(monkeypatch, tmp_path):
    import sys
    from mingjing import harness_client
    original = runtime_fingerprint()
    import shutil
    root = Path(__file__).resolve().parents[1]
    bundle = tmp_path / 'random-onefile-extraction'
    (bundle / 'runtime/finance-carrier').mkdir(parents=True)
    (bundle / 'runtime/harness').mkdir()
    shutil.copyfile(root / 'runtime/finance-carrier/staged/carrier_manifest.json', bundle / 'runtime/finance-carrier/carrier_manifest.json')
    shutil.copyfile(root / 'runtime/harness/source_snapshot.json', bundle / 'runtime/harness/source_snapshot.json')
    # PyInstaller's module __file__ does not refer to a readable .py source;
    # resources live under MEIPASS while Python code comes from its archive.
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(bundle), raising=False)
    monkeypatch.setattr(harness_client, '__file__', str(tmp_path / 'archive/mingjing/harness_client.pyc'))
    for name in ('mingjing', 'mingjing.config', 'mingjing.audit'):
        module = sys.modules[name]
        monkeypatch.setattr(module, '__file__', str(bundle / 'mingjing' / (name.rsplit('.', 1)[-1]+'.pyc')))
    frozen = runtime_fingerprint()
    assert frozen == original
    assert frozen['launch']['kind'] == 'finance_carrier'
    assert 'path' not in frozen['launch']


def test_replaced_external_carrier_fails_before_process_launch(tmp_path):
    external = tmp_path / 'controlled-runtime.exe'
    external.write_bytes(b'MZ-original-locked-test-fixture')
    config = {'dsh_bin': str(external)}
    locked = runtime_fingerprint(config)
    assert locked['launch']['path'] == str(external.resolve())
    assert locked['launch']['kind'] == 'external_executable'
    external.write_bytes(b'MZ-replaced-test-fixture')
    current = runtime_fingerprint(config)
    assert current['launch']['sha256'] != locked['launch']['sha256']
    runtime = HarnessRuntime(tmp_path / 'owned-home', lambda *_: {}, config=config, expected_runtime_lock=locked)
    try:
        with pytest.raises(HarnessError, match='task snapshot'):
            runtime.start()
        assert runtime._process is None
        runtime.runtime_lock = locked
        with pytest.raises(HarnessError, match='external native executable changed'):
            runtime._launch()
    finally:
        runtime.close()


@bundled_only
def test_web_nested_external_binary_uses_the_real_controlled_native_profile(tmp_path):
    config = {'harness': {'enabled': True, 'runtime_mode': 'external', 'executable': str(RUNTIME),
                          'working_dir': str(tmp_path/'untrusted-cwd'), 'patch_file': str(tmp_path/'untrusted.patch.yml')}}
    config_lock = runtime_fingerprint(config)
    assert config_lock['launch']['kind'] == 'external_executable'
    assert config_lock['launch']['path'] == str(RUNTIME.resolve())
    host = dispatcher('probe')
    with loop(tmp_path, 'probe', [('mj_list_reports', {})], host, config=config) as (runtime, state):
        assert runtime._launch() == [str(RUNTIME.resolve())]
        result = runtime.prompt('native-session', 'Read the financial resource list.', timeout_s=20)
        description = runtime.describe()
        profile = (runtime.home/'profiles/mingjing/cordis.patch.yml').read_text(encoding='utf-8')
    assert result['native_loop'] and result['finish_reason'] == 'completed'
    assert description['native_loop'] and state['requests'] == 2
    assert [event['tool'] for event in host.trace] == ['mj_list_reports']
    assert 'untrusted.patch.yml' not in profile and 'untrusted-cwd' not in profile
    assert not (tmp_path/'untrusted-cwd').exists()


def test_missing_web_external_binary_is_locked_then_falls_back_at_launch(tmp_path):
    import json
    from dataclasses import asdict
    from mingjing.agent import FinancialAgent
    from mingjing.config import AppConfig
    missing = tmp_path/'absent-native.exe'
    config = AppConfig().to_dict()
    config['harness'].update(enabled=True, runtime_mode='external', executable=str(missing))
    pinned = runtime_fingerprint(config)
    assert pinned['launch'] == {'kind': 'external_executable_missing', 'path': str(missing.resolve())}
    draft = tmp_path/'draft.md'
    draft.write_text('甲公司2024年营业收入100元。', encoding='utf-8')
    corpus = tmp_path/'corpus'
    corpus.mkdir()
    fact = Fact('missing-runtime-fixture', '甲公司', 'REVENUE', '2024Y', value=Decimal('100'))
    (corpus/'facts.json').write_text(json.dumps([fact.to_dict()], ensure_ascii=False), encoding='utf-8')
    owner = FinancialAgent({'draft': str(draft), 'corpus': str(corpus), 'company': '甲公司', 'period': '2024Y'}, config,
                           lambda *_: (_ for _ in ()).throw(AssertionError('Missing runtime cannot resolve credentials')),
                           tmp_path/'out', run_id='missing-native-fixture')
    result = owner.run()
    assert result['model']['status'] == 'fallback' and result['model']['mode'] == 'kernel'
    assert 'Configured dsh runtime executable is missing' in result['model']['error']
    assert owner.runtime._process is None
    assert owner.snapshot['agent']['runtime']['launch'] == pinned['launch']
    assert (owner.out/'run_manifest.json').is_file()


def test_disabled_or_empty_web_external_setting_selects_default_carrier(loop_carrier):
    for config in ({'harness': {'enabled': False, 'runtime_mode': 'external', 'executable': 'absent.exe'}},
                   {'harness': {'enabled': True, 'runtime_mode': 'external', 'executable': ''}}):
        launch = runtime_fingerprint(config)['launch']
        assert launch == loop_carrier
        assert_default_carrier(launch)


@pytest.mark.parametrize('legacy,directory', [(False, False), (True, False), (False, True)])
def test_unavailable_external_runtime_fails_before_cold_resource_extraction(tmp_path, monkeypatch, legacy, directory):
    from mingjing import harness_client

    unavailable = tmp_path / 'absent-native.exe'
    if directory:
        unavailable.mkdir()
    config = ({'dsh_bin': str(unavailable)} if legacy else
              {'harness': {'enabled': True, 'runtime_mode': 'external', 'executable': str(unavailable)}})
    runtime = HarnessRuntime(tmp_path / 'owned-home', lambda *_: {}, config=config)

    def forbidden(*args, **kwargs):
        raise AssertionError('An unavailable external runtime must not load embedded resources')

    monkeypatch.setattr(harness_client, 'runtime_fingerprint', forbidden)
    monkeypatch.setattr(runtime, '_assets', forbidden)
    monkeypatch.setattr(runtime, '_prepare_profile', forbidden)
    try:
        with pytest.raises(HarnessError, match='Configured dsh runtime executable is missing'):
            runtime.start()
        assert runtime._process is None
        assert runtime.runtime_lock is None
        assert not (tmp_path / 'owned-home' / 'profiles').exists()
    finally:
        runtime.close()


def test_legacy_web_external_path_is_retained_but_defaults_to_default_carrier(loop_carrier):
    old_path = 'C:/Users/Legacy/Desktop/DeepSeek Harness 0.1.5-RC1/deepseek-harness.exe'
    for mode in (None, 'embedded'):
        nested = {'enabled': True, 'executable': old_path, 'profile': 'sdk-minimal'}
        if mode is not None:
            nested['runtime_mode'] = mode
        config = {'harness': nested}
        launch = runtime_fingerprint(config)['launch']
        assert launch == loop_carrier
        assert_default_carrier(launch)
        assert config['harness']['executable'] == old_path
        assert config['harness']['profile'] == 'sdk-minimal'
