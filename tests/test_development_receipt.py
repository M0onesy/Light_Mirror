"""Receipt consistency tests use invented files; no provider or D3 is executed."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import development_receipt as receipt


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def entry(path, relative=None):
    raw = path.read_bytes()
    return {'path': relative or str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def chain(path, run_id, events):
    previous, lines = '', []
    for event in events:
        event = dict(event, run_id=run_id, prev_event_hash=previous)
        previous = receipt._digest(event)
        lines.append(json.dumps(dict(event, event_hash=previous)))
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')


@pytest.fixture
def frozen_run(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, 'ROOT', tmp_path)
    root = tmp_path/'runs'/'original'
    root.mkdir(parents=True)
    config = {'revision': 3, 'agent': {'routing': {'default': {'provider': 'deepseek', 'model': 'fixture'}}},
              'providers': {'deepseek': {'base_url': 'https://api.deepseek.com'}}}
    lock = {'config': config, 'revision': 3, 'kernel': {'module:judge': 'original-semantic-hash'},
            'agent': {'modules': {'agent': 'original-agent-hash'}, 'runtime': {'lock_schema_version': 2}}}
    lock['resource_lock'] = receipt._digest(lock)
    model = {'status': 'success', 'mode': 'harness', 'request_count': 1, 'tokens': 'unknown', 'cost': 'unknown'}
    data = {'run_id': 'original-run', 'resource_lock': lock['resource_lock'], 'task_type': 'check',
            'model': model, 'summary': {'verified': 0, 'violated': 0, 'abstained': 1},
            'partial': False, 'claims': [{'account': 'ROE_UNSPECIFIED'}], 'facts': [{'account': 'ROE'}],
            'pending_claim_ids': []}
    source = write(root/'input.json', {'kind': 'invented_not_real_API'})
    for name, value in [('results.json', data), ('claims.json', data['claims']), ('facts.json', data['facts']),
                        ('agent_lock.json', lock), ('agent_state.json', {}), ('bindings.json', {}),
                        ('agent_model_cache.json', {'request': [{'type': 'text-delta', 'text': 'invented'}]})]:
        write(root/name, value)
    chain(root/'audit.jsonl', data['run_id'], [
        {'type': 'runtime_describe', 'output': {'native_loop': True, 'profile': 'mingjing', 'shell': False, 'code_mode': False}},
        {'type': 'model_request', 'cache_hit': False, 'replay_only': False, 'request_hash': 'request'},
        {'type': 'model_call', 'model': 'fixture', 'role': 'planner'},
    ])
    manifest = {'schema_version': 2, 'run_id': data['run_id'], 'native_agent': True,
                'resource_lock': data['resource_lock'], 'agent_config_revision': 3,
                'kernel_fingerprint': lock['kernel'], 'inputs': [entry(source)],
                'outputs': [entry(path, path.name) for path in root.iterdir() if path.name != 'input.json']}
    write(root/'run_manifest.json', manifest)
    recorded = {'status': 'technical_flow_passed_quality_pending', 'provider': 'deepseek', 'model': 'fixture',
                'model_run': copy.deepcopy(model), 'summary': copy.deepcopy(data['summary']),
                'partial': False, 'claims': 1, 'facts': 1}
    return SimpleNamespace(root=root, recorded=recorded, data=data, lock=lock, manifest=manifest)


def test_original_lock_is_read_and_unknown_usage_is_preserved(frozen_run):
    before = (frozen_run.root/'agent_lock.json').read_bytes()
    result = receipt._run(frozen_run.root, frozen_run.recorded, runtime_lock=frozen_run.lock['agent']['runtime'])
    assert result['status'] == 'passed' and result['live_logged_model_requests'] == 1
    assert frozen_run.recorded['model_run']['tokens'] == frozen_run.recorded['model_run']['cost'] == 'unknown'
    assert (frozen_run.root/'agent_lock.json').read_bytes() == before


@pytest.mark.parametrize('filename', ['results.json', 'agent_lock.json', 'audit.jsonl', 'agent_model_cache.json', 'input.json'])
def test_changed_original_file_cannot_be_accepted_by_receipt_status(frozen_run, filename):
    path = frozen_run.root/filename
    path.write_bytes(path.read_bytes()+b' ')
    assert receipt._attempt(lambda: receipt._run(frozen_run.root, frozen_run.recorded))['status'] == 'failed'


@pytest.mark.parametrize('field,value', [('partial', True), ('summary', {'verified': 999}),
                                       ('model', 'different'), ('provider', 'different')])
def test_receipt_disagreement_with_frozen_run_is_rejected(frozen_run, field, value):
    recorded = dict(frozen_run.recorded, **{field: value})
    assert receipt._attempt(lambda: receipt._run(frozen_run.root, recorded))['status'] == 'failed'


def test_runtime_lock_mismatch_is_rejected_even_if_replay_claimed_passed(frozen_run):
    actual = receipt._attempt(lambda: receipt._run(frozen_run.root, frozen_run.recorded,
                             runtime_lock={'lock_schema_version': 2, 'plugin': 'changed'}))
    assert actual['status'] == 'failed' and 'runtime' in actual['reason']


def test_run_from_another_series_is_rejected(frozen_run):
    actual = receipt._attempt(lambda: receipt._run(frozen_run.root, frozen_run.recorded,
                             expected_run_id='source-real-stable-05'))
    assert actual['status'] == 'failed' and 'another acceptance series' in actual['reason']


def test_unlocked_output_and_path_escape_are_rejected(frozen_run):
    manifest = copy.deepcopy(frozen_run.manifest)
    manifest['outputs'] = [row for row in manifest['outputs'] if row['path'] != 'agent_lock.json']
    assert receipt._attempt(lambda: receipt._outputs(manifest, frozen_run.root, {'agent_lock.json'}))['status'] == 'failed'
    outside = write(frozen_run.root.parent/'outside.json', {})
    manifest['outputs'].append(entry(outside, '../outside.json'))
    assert receipt._attempt(lambda: receipt._outputs(manifest, frozen_run.root, set()))['status'] == 'failed'


def test_d3_path_is_rejected_before_a_read(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, 'ROOT', tmp_path)
    # Do not create, enumerate or inspect a real holdout. The lexical boundary
    # rejects the nominated fixture name before resolve/read.
    with pytest.raises(ValueError, match='D3'):
        receipt.read_receipt(tmp_path/'sealed-D3'/'acceptance.json')


@pytest.mark.parametrize('address', ['http://127.0.0.1:9999', 'https://example.invalid', 'http://localhost'])
def test_local_or_mock_model_routes_are_not_live_provider_evidence(frozen_run, address):
    cfg = copy.deepcopy(frozen_run.lock['config'])
    cfg['providers']['deepseek']['base_url'] = address
    with pytest.raises(ValueError, match='not live'):
        receipt._remote_route(cfg)


def test_boolean_or_unknown_network_count_cannot_be_offline_replay():
    row = {'status': 'passed', 'numeric_equal': True, 'real_harness_loop': True}
    assert not receipt._replay(dict(row, network_calls='unknown'), agent=True)
    assert not receipt._replay(dict(row, network_calls=False), agent=True)
    assert receipt._replay(dict(row, network_calls=0), agent=True)


@pytest.mark.parametrize('xml,passed,consistent', [
    ('<testsuite tests="1" skipped="1"><testcase><skipped/></testcase></testsuite>', 0, True),
    ('<testsuite tests="1" failures="1"><testcase/></testsuite>', 1, False),
    ('<testsuite tests="1" failures="0"><testcase/></testsuite>', 1, True),
])
def test_junit_actual_cases_and_declared_failures_are_both_checked(tmp_path, monkeypatch, xml, passed, consistent):
    monkeypatch.setattr(receipt, 'ROOT', tmp_path)
    path = tmp_path/'regression.xml'
    path.write_text(xml, encoding='utf-8')
    result, _ = receipt._regression(path)
    assert result['passed'] == passed and result['declared_counts_consistent'] is consistent


def test_not_yet_executed_series_keeps_known_previous_failure_and_release_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, 'ROOT', tmp_path)
    monkeypatch.setattr(receipt, 'verify', lambda *_: {'status': 'passed_source_to_assets'})
    prior = {'status': 'needs_improvement', 'child': {'partial': True,
        'model_run': {'status': 'partial', 'binding_submissions': {'planner': 0, 'verifier': 1}, 'tokens': 'unknown'}},
        'numeric_value_from_original_pdf': False, 'page_evidence_available': False}
    write(tmp_path/'runs/source-native-reparse-stable-04/acceptance.json', prior)
    regression = tmp_path/'regression.xml'
    regression.write_text('<testsuite tests="1"><testcase><skipped/></testcase></testsuite>', encoding='utf-8')
    code = write(tmp_path/'code.json', receipt.source_lock())
    args = SimpleNamespace(series='stable-05', regression=regression, code_lock=code,
        web_receipt=tmp_path/'web.json', runtime_receipt=tmp_path/'runtime.json', out=tmp_path/'new-summary')
    result = receipt.collect(args)
    assert result['status'] == 'needs_improvement'
    assert result['receipt_statuses']['report'] == result['receipt_statuses']['native_reparse'] == 'not_executed'
    assert not result['checks']['coherent_regression']
    assert result['previous_failed_attempts'][0]['child']['model_run']['binding_submissions']['planner'] == 0
    assert result['previous_failed_attempts'][0]['child']['model_run']['tokens'] == 'unknown'
    assert not result['professional_acceptance'] and not result['release_acceptance']
    assert 'sealed_fresh_D3' in result['unexecuted_release_prerequisites']
    assert 'independent_clean_Windows' in result['unexecuted_release_prerequisites']
    assert 'Kimi_and_GPT_real_API_without_credentials' in result['unexecuted_release_prerequisites']


def test_generated_measurement_hash_is_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, 'ROOT', tmp_path)
    root = tmp_path/'generated'
    root.mkdir()
    cases = [{'set': 'B', 'id': 'invented'}]
    predictions = [{'expected_status': 'violated', 'status': 'violated'}]
    for name, rows in [('generated_cases.jsonl', cases), ('predictions.jsonl', predictions)]:
        (root/name).write_text('\n'.join(json.dumps(row) for row in rows)+'\n', encoding='utf-8')
    metrics = {'status': 'generated_pipeline_measured_not_professional_acceptance', 'total': 1,
               'suite_sha256': receipt._digest(cases), 'records_sha256': receipt._digest(predictions)}
    write(root/'metrics.json', metrics)
    write(root/'measurement_audit.json', {'professional_acceptance': False,
        'metrics_sha256': receipt._sha(root/'metrics.json'), 'input_sha256': receipt._sha(root/'generated_cases.jsonl'),
        'predictions_sha256': receipt._sha(root/'predictions.jsonl')})
    assert receipt._generated(root, metrics)['status'] == 'passed'
    (root/'predictions.jsonl').write_text('{"status":"verified"}\n', encoding='utf-8')
    assert receipt._attempt(lambda: receipt._generated(root, metrics))['status'] == 'failed'


def test_source_freeze_covers_builtin_samples_and_resource_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, 'ROOT', tmp_path)
    resources = ['samples/demo.md', 'samples/intro.txt', 'samples/corpus/facts.json',
        'samples/corpus/annual.pdf', 'samples/corpus/index.csv', 'rulepack/rulepack.lock',
        'runtime/harness/manifest.json', 'runtime/finance-carrier/staged/carrier_manifest.json']
    for name in resources:
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'invented resource')
    # No large external corpus or annotation subtree enters the code lock.
    write(tmp_path/'corpus/annual.json', {})
    write(tmp_path/'bench/v1/annotations/example.json', {})
    write(tmp_path/'bench/v1/mechanism.py', {})
    frozen = receipt.source_lock()
    assert set(resources) <= frozen.keys()
    assert 'bench/v1/mechanism.py' in frozen
    assert 'corpus/annual.json' not in frozen and 'bench/v1/annotations/example.json' not in frozen
    for name in resources:
        path = tmp_path/name
        original = path.read_bytes()
        path.write_bytes(original+b' changed')
        assert receipt.source_lock()[name] != frozen[name]
        path.write_bytes(original)


@pytest.fixture
def frozen_followup(frozen_run):
    parent = frozen_run
    parent_validation = receipt._run(parent.root, parent.recorded)
    root = parent.root.parent/'followup'
    sid, mid = 'session-fixture', 'message-1'
    message_root = root/'sessions'/sid/mid
    message_root.mkdir(parents=True)
    lock = {'session_id': sid, 'message_id': mid, 'run_id': parent.data['run_id'],
        'resource_lock': parent.data['resource_lock'], 'parent_manifest': parent.manifest,
        'parent_results_sha256': receipt._sha(parent.root/'results.json'),
        'parent_run_path': str(parent.root), 'config': parent.lock['config'],
        'kernel': parent.lock['kernel'], 'agent': parent.lock['agent']}
    output = {'answer': 'Invented fixture; not a model or professional conclusion.'}
    write(message_root/'message_lock.json', lock)
    write(message_root/'message_output.json', output)
    write(message_root/'agent_model_cache.json', {'request': [{'type': 'text-delta', 'text': 'invented'}]})
    chain(message_root/'audit.jsonl', parent.data['run_id'], [
        {'type': 'model_request', 'cache_hit': False, 'replay_only': False, 'request_hash': 'request'},
        {'type': 'tool_call', 'tool': 'invented_read_only_tool'},
    ])
    manifest = {key: lock[key] for key in ('session_id', 'message_id', 'run_id', 'resource_lock')}
    manifest['outputs'] = [entry(path, path.name) for path in message_root.iterdir()]
    write(message_root/'message_manifest.json', manifest)
    state = {'run_id': parent.data['run_id'], 'resource_lock': parent.data['resource_lock'],
        'config': parent.lock['config'], 'messages': [{'role': 'assistant', 'message_id': mid,
        'text': output['answer'], 'agent_message_manifest_sha256': receipt._sha(message_root/'message_manifest.json')}]}
    state_path = write(root/'sessions'/(sid+'.json'), state)
    recorded = {'session_id': sid, 'source_run': parent.data['run_id'],
        'route': {'provider': 'deepseek', 'model': 'fixture'},
        'usage': {'model_requests': 1, 'tool_calls': 1, 'tokens': 'unknown'},
        'message_agent_replays': [{'status': 'passed', 'real_harness_loop': True,
            'network_calls': 0, 'output_sha256': receipt._digest(output)}]}
    return SimpleNamespace(root=root, message_root=message_root, state=state, state_path=state_path,
        recorded=recorded, parent=parent, validation=parent_validation, manifest=manifest)


def test_followup_verifies_original_parent_manifest_and_preserves_unknown_usage(frozen_followup):
    fixture = frozen_followup
    result = receipt._followup(fixture.root, fixture.recorded, fixture.parent.root, fixture.validation)
    assert result['status'] == 'passed' and result['live_logged_model_requests'] == 1
    assert result['tool_calls_verified'] == 1 and result['usage']['tokens'] == 'unknown'


@pytest.mark.parametrize('target', ['message_manifest', 'message_output', 'parent_results'])
def test_changed_followup_or_parent_artifact_is_rejected(frozen_followup, target):
    fixture = frozen_followup
    path = fixture.parent.root/'results.json' if target == 'parent_results' else fixture.message_root/(target+'.json')
    path.write_bytes(path.read_bytes()+b' ')
    result = receipt._attempt(lambda: receipt._followup(fixture.root, fixture.recorded,
                                     fixture.parent.root, fixture.validation))
    assert result['status'] == 'failed'


def test_followup_missing_original_model_cache_is_rejected(frozen_followup):
    fixture = frozen_followup
    write(fixture.message_root/'agent_model_cache.json', {})
    fixture.manifest['outputs'] = [entry(path, path.name) for path in fixture.message_root.iterdir()
        if path.name != 'message_manifest.json']
    write(fixture.message_root/'message_manifest.json', fixture.manifest)
    fixture.state['messages'][0]['agent_message_manifest_sha256'] = receipt._sha(fixture.message_root/'message_manifest.json')
    write(fixture.state_path, fixture.state)
    result = receipt._attempt(lambda: receipt._followup(fixture.root, fixture.recorded,
                                     fixture.parent.root, fixture.validation))
    assert result['status'] == 'failed' and 'response cache' in result['reason']


@pytest.mark.parametrize('usage', [{'model_requests': 2, 'tool_calls': 1},
                                 {'model_requests': 1, 'tool_calls': True}])
def test_followup_receipt_cannot_invent_request_or_tool_counts(frozen_followup, usage):
    fixture = frozen_followup
    recorded = dict(fixture.recorded, usage=usage)
    result = receipt._attempt(lambda: receipt._followup(fixture.root, recorded,
                                     fixture.parent.root, fixture.validation))
    assert result['status'] == 'failed' and 'counts differ' in result['reason']


@pytest.fixture
def keyless_runtime(tmp_path, monkeypatch):
    from mingjing import harness_client
    monkeypatch.setattr(receipt, 'ROOT', tmp_path)
    lock = {'lock_schema_version': 2, 'manifest_sha256': 'fixture-manifest',
        'plugin_sha256': 'fixture-plugin', 'source_archive_sha256': 'fixture-archive',
        'source_snapshot_sha256': 'fixture-snapshot', 'harness_version': 'fixture-version',
        'launch': {'dependency_lock_sha256': 'fixture-manifest',
                   'node_sha256': 'fixture-node', 'entry_sha256': 'fixture-entry'}}
    base = tmp_path/'runs/native-keyless'
    base.mkdir(parents=True)
    for name in ('resolved-modules.jsonl', 'carrier-import-guard.mjs'):
        (base/name).write_bytes(b'invented keyless fixture')
    linked = {'runtime_lock': lock, 'status': 'passed_staged_finance_runtime_functional_fixture',
        'real_provider_api': 'not_executed', 'describe': {'native_loop': True}, 'replay_network_requests': 0,
        'import_trace_sha256': receipt._sha(base/'resolved-modules.jsonl'),
        'import_guard_sha256': receipt._sha(base/'carrier-import-guard.mjs')}
    linked_path = write(base/'acceptance.json', linked)
    source = tmp_path/'mingjing/transport.py'
    source.parent.mkdir()
    source.write_bytes(b'# invented source proof')
    recorded = {'runtime_lock': lock, 'native_fixture': {
        'receipt': linked_path.relative_to(tmp_path).as_posix(), 'sha256': receipt._sha(linked_path)},
        'tests': [{'exit_code': 0, 'passed': 1}],
        'frozen_source_inputs': [entry(source, source.relative_to(tmp_path).as_posix())]}
    carrier = {'manifest_sha256': 'fixture-manifest', 'plugin_sha256': 'fixture-plugin',
        'source_sha256': 'fixture-archive', 'source_snapshot_sha256': 'fixture-snapshot',
        'harness_version': 'fixture-version', 'node': 'node.exe', 'bootstrap': 'entry.mjs',
        'files': [{'path': 'node.exe', 'sha256': 'fixture-node'}, {'path': 'entry.mjs', 'sha256': 'fixture-entry'}]}
    monkeypatch.setattr(harness_client, 'verify_finance_carrier', lambda *_: carrier)
    return SimpleNamespace(recorded=recorded, linked_path=linked_path, carrier=carrier)


def test_runtime_keyless_fixture_remains_keyless(keyless_runtime):
    result = receipt._runtime(keyless_runtime.recorded)
    assert result['status'] == 'passed' and result['real_provider_api'] == 'not_executed'
    assert result['evidence_kind'] == 'original_harness_loop_keyless_fixture'


@pytest.mark.parametrize('changed', ['linked_receipt', 'actual_carrier'])
def test_runtime_original_receipt_and_actual_resources_must_match(keyless_runtime, changed):
    if changed == 'linked_receipt':
        keyless_runtime.linked_path.write_bytes(keyless_runtime.linked_path.read_bytes()+b' ')
    else:
        keyless_runtime.carrier['plugin_sha256'] = 'changed-plugin'
    result = receipt._attempt(lambda: receipt._runtime(keyless_runtime.recorded))
    assert result['status'] == 'failed'
