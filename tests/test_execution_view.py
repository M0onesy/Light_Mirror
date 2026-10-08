"""Pending work must not inflate completed financial abstention statistics."""
import copy
import hashlib
import io
import json
import threading
import time
import urllib.error
import zipfile
from pathlib import Path

import pytest

from mingjing import server
from mingjing.audit import digest, kernel_fingerprint
from mingjing.agent import orchestration_fingerprint
from mingjing.execution import project_execution, project_result_view
from test_server_analysis_product import local_web


def legacy_partial():
    return {'run_id': 'partial-fixture', 'partial': True,
        'summary': {'verified': 1, 'violated': 0, 'abstained': 2},
        'claims': [{'claim_id': 'done', 'text': '已核查陈述', 'start': 1, 'end': 3},
                   {'claim_id': 'abstained', 'text': '合法弃权陈述', 'start': 4, 'end': 6},
                   {'claim_id': 'waiting', 'text': '未执行陈述', 'start': 7, 'end': 9}],
        'results': [{'claim_id': 'done', 'status': 'verified'}, {'claim_id': 'abstained', 'status': 'abstained'},
                    {'claim_id': 'waiting', 'status': 'abstained'}],
        'coverage': {'mentions': [{'claim_id': 'done', 'status': 'judged'}, {'claim_id': 'abstained', 'status': 'abstained'},
                    {'claim_id': 'waiting', 'status': 'pending', 'reason': 'native_binding_not_submitted'},
                    {'id': 'unbound-cell', 'status': 'pending'}, {'id': 'axis-id', 'status': 'rule_excluded'}]}}


def test_legacy_projection_separates_pending_without_altering_frozen_kernel_payload():
    original = legacy_partial()
    before = copy.deepcopy(original)
    view = project_result_view(original)
    execution = view['execution']
    assert execution['summary'] == {'verified': 1, 'violated': 0, 'abstained': 1, 'pending': 1, 'unknown': 0}
    assert execution['processed_claims'] == 2 and execution['claim_count'] == 3
    assert execution['pending_mentions'] == 2 and execution['unbound_pending_mentions'] == 1
    assert execution['financial_candidates'] == 4 and execution['processed_mentions'] == 2
    assert execution['coverage'] == 0.5 and execution['status'] == 'partial'
    assert view['summary']['abstained'] == 2
    assert view['results'][-1]['status'] == 'abstained'
    assert view['results'][-1]['display_status'] == 'pending'
    assert view['pending_items'][0]['text'] == '未执行陈述'
    assert original == before


def test_current_partial_with_omitted_pending_results_uses_declared_ids():
    current = legacy_partial()
    current['results'].pop()
    current['summary']['abstained'] = 1
    current['coverage']['mentions'][2]['reason'] = 'native_verification_not_executed'
    current['pending_claim_ids'] = ['waiting']
    current['execution'] = {'pending_claim_ids': ['waiting'], 'judged_claim_ids': ['done', 'abstained'],
                            'native_verified_claim_ids': ['done'], 'candidate_value': 'must-never-appear'}
    result = project_result_view(current)
    assert result['execution']['summary']['pending'] == 1
    assert result['execution']['summary']['abstained'] == 1
    assert len(result['results']) == 2 and len(result['pending_items']) == 1
    assert result['pending_items'][0]['reason'] == 'native_verification_not_executed'
    assert result['execution']['native_tool_checked_claims'] == 1
    assert 'must-never-appear' not in json.dumps(result['execution'])


def test_missing_coverage_cannot_claim_full_document_completion():
    legacy = legacy_partial()
    legacy.pop('coverage')
    legacy['partial'] = False
    metadata = project_execution(legacy)
    assert metadata['status'] == 'unknown_coverage'
    assert metadata['coverage_known'] is False and metadata['financial_candidates'] is None
    assert metadata['recall'] == 'unknown_requires_independent_gold'


def test_partial_http_summary_results_export_and_replay_keep_pending_visible(local_web, monkeypatch):
    state, request = local_web
    task = server.RunTask('partial-fixture', {}, state.runs_root, state.config)
    task.out.mkdir()
    payload = legacy_partial()
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    (task.out / 'results.json').write_bytes(raw)
    (task.out / 'run_manifest.json').write_text('{}', encoding='utf-8')
    task.status = 'completed'
    state.tasks[task.id] = task
    summary = request(f'/api/runs/{task.id}')
    assert summary['status'] == 'partial' and summary['frozen']
    assert not summary['can_resume'] and summary['resume_reason'] == 'frozen_results_read_only'
    assert summary['execution']['summary']['abstained'] == 1
    assert summary['execution']['pending_mentions'] == 2
    view = request(f'/api/runs/{task.id}/results')
    assert view['view_schema'] == 'run-result-view.v1'
    assert view['results'][-1]['display_status'] == 'pending'
    with zipfile.ZipFile(io.BytesIO(server._export_run(task, state.config))) as archive:
        exported = json.loads(archive.read('execution_view.json'))
        assert archive.read('results.json') == raw
        assert exported['source_results_sha256'] == hashlib.sha256(raw).hexdigest()
        assert exported['execution']['summary']['pending'] == 1
    from mingjing import replay
    monkeypatch.setattr(replay, 'replay_run', lambda path: {'status': 'passed', 'numeric_equal': True})
    outcome = request('/api/replay', {'run': str(task.out)}, 'POST')
    assert outcome['status'] == 'passed' and outcome['validation_scope'] == 'reproducibility_only'
    assert outcome['source_execution']['partial'] and outcome['source_execution']['pending_mentions'] == 2
    assert (task.out / 'results.json').read_bytes() == raw


def test_cli_partial_status_survives_history_reconstruction_and_config_changes(local_web):
    state, request = local_web
    draft = state.runs_root.parent / 'source.md'
    draft.write_text('原始材料', encoding='utf-8')
    corpus = state.runs_root.parent / 'corpus'
    corpus.mkdir()
    task = server.RunTask('history-partial', {'draft': str(draft), 'corpus': str(corpus)}, state.runs_root, state.config)
    task.out.mkdir()
    payload = legacy_partial()
    payload['run_id'] = task.id
    (task.out / 'results.json').write_text(json.dumps(payload), encoding='utf-8')
    (task.out / 'run_manifest.json').write_text('{}', encoding='utf-8')
    (task.out / 'run_request.json').write_text(json.dumps({'draft': str(draft), 'corpus': str(corpus), 'mode': 'kernel'}), encoding='utf-8')
    task._complete_cli()
    assert task.status == 'partial'
    assert task.events[-1]['partial'] is True
    state._register_task(task)
    state.config.update({'workspace': {'corpus_root': 'different-corpus'}})
    restored = server.AppState().get_task(task.id)
    assert restored.summary()['status'] == 'partial'
    assert restored.summary()['request']['corpus'] == str(corpus)


def test_mutable_cancelled_result_remains_cancelled_after_restart(local_web):
    state, _request = local_web
    task = server.RunTask('cancelled-mutable', {}, state.runs_root, state.config)
    task.out.mkdir()
    payload = legacy_partial()
    payload['run_id'] = task.id
    (task.out / 'results.json').write_text(json.dumps(payload), encoding='utf-8')
    task.status = 'cancelled'
    state._register_task(task)
    restored = server.AppState().get_task(task.id)
    assert restored.status == 'cancelled' and restored.summary()['partial']
    assert not restored.summary()['frozen']


def test_resume_is_serialized_and_lock_failure_does_not_modify_old_results(local_web, monkeypatch):
    state, _request = local_web
    task = server.RunTask('resume-race', {}, state.runs_root, state.config)
    task.out.mkdir()
    task.status = 'cancelled'
    cfg = state.config.get()
    draft = task.out/'input'/'frozen-draft.md'
    draft.parent.mkdir()
    draft.write_text('冻结草稿', encoding='utf-8')
    from mingjing.evidence import sha256_file
    from mingjing.agent import AGENT_LOCK_SCHEMA, task_execution_semantics
    request = {'draft': str(draft), 'corpus': str(task.root)}
    lock = {'schema_version': AGENT_LOCK_SCHEMA, 'task_semantics': task_execution_semantics(request, '', []),
            'config': cfg, 'kernel': kernel_fingerprint(), 'agent': orchestration_fingerprint(cfg),
            'resources': [{'id': 'draft', 'path': str(draft), 'sha256': sha256_file(draft)}]}
    lock['resource_lock'] = digest(lock)
    checkpoint = {'run_id': task.id, 'stage': 'cancelled_partial', 'resource_lock': lock['resource_lock'],
                  'request': request}
    (task.out / 'agent_state.json').write_text(json.dumps(checkpoint), encoding='utf-8')
    (task.out / 'agent_lock.json').write_text(json.dumps(lock), encoding='utf-8')
    entered, release = threading.Event(), threading.Event()

    def resumed_worker():
        entered.set()
        release.wait(3)

    monkeypatch.setattr(task, '_run', resumed_worker)
    task.resume()
    assert entered.wait(3)
    with pytest.raises(ValueError, match='仍在运行'):
        task.resume()
    release.set()
    task.thread.join(3)
    original = (task.out / 'agent_state.json').read_bytes()
    lock['kernel'] = {'modified': 'incompatible'}
    lock['resource_lock'] = digest({key: value for key, value in lock.items() if key != 'resource_lock'})
    (task.out / 'agent_lock.json').write_text(json.dumps(lock), encoding='utf-8')
    checkpoint['resource_lock'] = lock['resource_lock']
    (task.out / 'agent_state.json').write_text(json.dumps(checkpoint), encoding='utf-8')
    original = (task.out / 'agent_state.json').read_bytes()
    with pytest.raises(ValueError, match='锁已改变'):
        task.resume()
    assert (task.out / 'agent_state.json').read_bytes() == original


def test_rerun_uses_original_snapshot_current_settings_and_a_new_id(local_web, monkeypatch):
    state, request = local_web
    original_draft = state.runs_root.parent / 'original.md'
    original_draft.write_text('被核查原稿', encoding='utf-8')
    corpus = state.runs_root.parent / 'original-corpus'
    corpus.mkdir()
    source = server.RunTask('frozen-source', {'draft': str(original_draft), 'corpus': str(corpus)}, state.runs_root, state.config)
    (source.out / 'input').mkdir(parents=True)
    snapshot = source.out / 'input' / 'original.md'
    snapshot.write_text('冻结原稿', encoding='utf-8')
    raw = json.dumps(legacy_partial(), ensure_ascii=False).encode('utf-8')
    (source.out / 'results.json').write_bytes(raw)
    (source.out / 'run_manifest.json').write_text('{}', encoding='utf-8')
    saved_request = {'draft': str(snapshot), 'corpus': str(corpus), 'mode': 'harness',
                     'company': '原公司', 'report_date': '2025-03-31'}
    (source.out / 'run_request.json').write_text(json.dumps(saved_request), encoding='utf-8')
    source.status = 'partial'
    state.tasks[source.id] = source
    cfg = state.config.update({'workspace': {'draft_path': 'other-draft', 'corpus_root': 'other-corpus'},
                              'agent': {'budget': {'max_model_calls': 150}}})
    monkeypatch.setattr(server.RunTask, 'start', lambda self: None)
    result = request(f'/api/runs/{source.id}/rerun', {'company': '修正公司'}, 'POST')
    rerun = state.tasks[result['id']]
    assert result['id'] != source.id and result['rerun_scope'] == 'complete_new_task'
    assert result['request']['parent_run_id'] == source.id
    assert result['request']['draft'] == str(snapshot) and result['request']['corpus'] == str(corpus)
    assert result['request']['company'] == '修正公司' and result['request']['mode'] == 'harness'
    assert rerun.config_snapshot['revision'] == cfg['revision'] == result['config_revision']
    assert rerun.config_snapshot['agent']['budget']['max_model_calls'] == 150
    assert (source.out / 'results.json').read_bytes() == raw
    assert json.loads((source.out / 'run_request.json').read_text(encoding='utf-8')) == saved_request


def test_rerun_rejects_unknown_parameters_running_sources_and_missing_materials(local_web, monkeypatch):
    state, request = local_web
    source = server.RunTask('cannot-rerun', {'draft': 'missing', 'corpus': 'missing'}, state.runs_root, state.config)
    source.status = 'partial'
    state.tasks[source.id] = source
    monkeypatch.setattr(server.RunTask, 'start', lambda self: pytest.fail('invalid rerun was started'))
    for overrides in ({'candidate_value': '100'}, {'api_key': 'private'}, {'company': 12}):
        with pytest.raises(urllib.error.HTTPError) as exc:
            request(f'/api/runs/{source.id}/rerun', overrides, 'POST')
        assert exc.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(f'/api/runs/{source.id}/rerun', {}, 'POST')
    assert exc.value.code == 400
    source.status = 'running'
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(f'/api/runs/{source.id}/rerun', {}, 'POST')
    assert exc.value.code == 400
    assert list(state.tasks) == [source.id]


def test_analysis_rerun_requires_only_existing_corpus(local_web, monkeypatch):
    state, request = local_web
    corpus = state.runs_root.parent / 'analysis-corpus'
    corpus.mkdir()
    source = server.RunTask('analysis-source', {'task_type': 'analysis', 'corpus': str(corpus)}, state.runs_root, state.config)
    source.status = 'completed'
    state.tasks[source.id] = source
    monkeypatch.setattr(server.RunTask, 'start', lambda self: None)
    assert source.summary()['can_rerun']
    outcome = request(f'/api/runs/{source.id}/rerun', {}, 'POST')
    assert outcome['request']['task_type'] == 'analysis'
    assert outcome['request']['corpus'] == str(corpus)
    assert not outcome['request'].get('draft')
