"""Local product checks for scoped assumptions; no provider or native API work."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import urllib.error

import pytest

from mingjing import server
from mingjing.cli import build_parser, command_check
from mingjing.models import Fact
from mingjing.replay import replay_run
from test_agent_lifecycle import lifecycle, new_agent
from test_server_analysis_product import local_web


SPECS = [{'scope': '合并', 'selector': {'accounts': ['REVENUE']}}]
TEXT = ('甲公司2024年营业收入100元。\n'
        '甲公司2024年合并净利润8元。\n'
        '甲公司2024年经营活动现金流量净额20元。\n')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def material(root):
    root.mkdir(parents=True, exist_ok=True)
    draft = root/'draft.md'
    draft.write_text(TEXT, encoding='utf-8')
    corpus = root/'corpus'
    corpus.mkdir()
    report = corpus/'report.txt'
    report.write_text('甲公司合并财务报表\n2024年营业收入100元；净利润8元；经营活动现金流量净额20元。', encoding='utf-8')
    facts = [Fact('fixture-'+account, '甲公司', account, '2024Y',
                  value=Decimal(value), publish_date='2025-03-01', source_path=str(report))
             for account, value in [('REVENUE', '100'), ('NP', '8'), ('CFO', '20')]]
    (corpus/'facts.json').write_text(json.dumps([fact.to_dict() for fact in facts], ensure_ascii=False), encoding='utf-8')
    return draft, corpus


def cli_run(root):
    draft, corpus = material(root/'materials')
    output = root/'run'
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(corpus),
        '--company', '甲公司', '--period', '2024Y', '--report-date', '2025-03-31',
        '--out', str(output), '--mode', 'kernel', '--scope-clarifications-json',
        json.dumps(SPECS, ensure_ascii=False)])
    assert command_check(args) == 0
    return output, draft, corpus


def test_cli_grouped_material_is_frozen_replayable_and_never_applies_to_unselected_claim(tmp_path):
    output, draft, _ = cli_run(tmp_path)
    data = read(output/'results.json')
    resource = read(output/'scope_clarifications.json')
    resource_sha = hashlib.sha256((output/'scope_clarifications.json').read_bytes()).hexdigest()
    assert resource['schema_version'] == 'scope-clarifications.v2'
    assert resource['draft_sha256'] == hashlib.sha256(draft.read_bytes()).hexdigest()
    assert data['scope_clarifications'] == SPECS and data['scope_declaration'] == ''
    assert data['draft_text'] == TEXT and len(data['claims']) == len(data['results']) == 3
    by_account = {claim['account']: (claim, next(row for row in data['results']
                       if row['claim_id'] == claim['claim_id'])) for claim in data['claims']}
    revenue, revenue_result = by_account['REVENUE']
    assert revenue['candidate_value'] == '100' and revenue['numeric_text'] == '100元'
    assert revenue_result['status'] == 'verified' and revenue_result['source_dimensions']['conditional_verification'] is True
    assert revenue_result['source_dimensions']['scope_provenance'] == 'user_supplied_scope'
    assert revenue_result['source_dimensions']['scope_declaration_sha256'] == resource_sha
    assert 'A3' in revenue_result['advisories']
    assert by_account['NP'][1]['status'] == 'verified'
    assert by_account['NP'][1]['source_dimensions']['scope_provenance'] != 'user_supplied_scope'
    assert by_account['CFO'][1]['status'] == 'abstained' and by_account['CFO'][1]['abstention_code'] == 'X3'
    assert 'scope_declaration' not in by_account['CFO'][0]['table_context']
    assert data['coverage_statistics']['core']['conditional_judged'] == 1
    assert data['coverage_statistics']['core']['source_supported_judgement_coverage'] == 1 / 3
    assert data['scope_clarification_groups'][0]['claim_ids'] == [by_account['CFO'][0]['claim_id']]
    manifest = read(output/'run_manifest.json')
    assert any(Path(item['path']).name == 'scope_clarifications.json' and item['sha256'] == resource_sha
               for item in manifest['inputs'])
    replay = replay_run(output)
    assert replay['status'] == 'passed' and replay['numeric_equal']
    resource['groups'][0]['scope'] = '母公司'
    (output/'scope_clarifications.json').write_text(json.dumps(resource, ensure_ascii=False), encoding='utf-8')
    assert replay_run(output)['status'] == 'failed'


def test_agent_prepare_archives_grouped_resource_and_checks_original_occurrences(lifecycle):
    lifecycle.boundary.cancel_first_binding = False
    Path(lifecycle.request['draft']).write_text(TEXT, encoding='utf-8')
    facts_file = Path(lifecycle.request['corpus'])/'facts.json'
    facts = read(facts_file)
    facts.extend(Fact('new-'+account, '甲公司', account, '2024Y', value=Decimal(value),
                      publish_date='2025-03-01', source_path='report.txt').to_dict()
                 for account, value in [('NP', '8'), ('CFO', '20')])
    facts_file.write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
    lifecycle.request['scope_clarifications'] = deepcopy(SPECS)
    owner = new_agent(lifecycle)
    result = owner.run()
    assert result['model']['status'] == 'success', result['model'].get('error')
    resource = result['clarifications'][0]
    assert resource['schema_version'] == 'scope-clarifications.v2'
    assert any(row['id'] == resource['resource_id'] and row['sha256'] == resource['sha256']
               for row in owner.snapshot['resources'])
    assert result['draft_text'] == TEXT
    assert len(result['claims']) == 3 and result['scope_clarifications'] == SPECS
    result_map = {row['claim_id']: row for row in result['results']}
    for claim in result['claims']:
        row = result_map[claim['claim_id']]
        if claim['account'] == 'REVENUE':
            assert row['status'] == 'verified' and row['source_dimensions']['conditional_verification'] is True
            assert claim['claim_id'] in result['execution']['native_verified_claim_ids']
        elif claim['account'] == 'NP':
            assert row['status'] == 'verified' and row['source_dimensions'].get('conditional_verification') is not True
        else:
            assert row['status'] == 'abstained' and row['abstention_code'] == 'X3'
            assert claim['claim_id'] in result['execution']['preflight_claim_ids']
    assert replay_run(lifecycle.out)['status'] == 'passed'


def frozen_web_parent(local_web, monkeypatch, *, global_parent=False):
    state, request = local_web
    draft, corpus = material(state.runs_root.parent/'grouped-materials')
    monkeypatch.setattr(server.RunTask, 'start', lambda self: None)
    body = {'draft': str(draft), 'corpus': str(corpus), 'company': '甲公司', 'period': '2024Y',
            'report_date': '2025-03-31', 'mode': 'kernel', 'scope_clarifications': deepcopy(SPECS)}
    if global_parent:
        body.update(scope_declaration='合并', scope_clarifications=[])
    launched = request('/api/run', body, 'POST')
    task = state.tasks[launched['id']]
    task.status = 'completed'
    task.out.mkdir()
    saved = deepcopy(body)
    (task.out/'run_request.json').write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
    (task.out/'results.json').write_text(json.dumps({**saved, 'claims': [], 'facts': [], 'results': []}, ensure_ascii=False), encoding='utf-8')
    (task.out/'run_manifest.json').write_text(json.dumps({'inputs': [{'path': str(draft),
        'sha256': hashlib.sha256(draft.read_bytes()).hexdigest()}]}), encoding='utf-8')
    before = {path.name: path.read_bytes() for path in task.out.iterdir() if path.is_file()}
    return state, request, task, body, before


def test_web_rerun_and_session_create_child_groups_preserve_parent_and_clear_on_new_draft(local_web, monkeypatch):
    state, request, task, body, before = frozen_web_parent(local_web, monkeypatch)
    alternate = [{'scope': '母公司', 'selector': {'accounts': ['REVENUE']}}]
    rerun = request(f'/api/runs/{task.id}/rerun', {'scope_clarifications': alternate}, 'POST')
    assert rerun['request']['scope_clarifications'] == alternate
    assert rerun['request']['parent_run_id'] == task.id
    next_draft = Path(body['draft']).with_name('new.md')
    next_draft.write_text('甲公司2023年营业收入50元。', encoding='utf-8')
    changed = request(f'/api/runs/{task.id}/rerun', {'draft': str(next_draft)}, 'POST')
    assert changed['request'].get('scope_clarifications', []) == []
    from mingjing import replay
    monkeypatch.setattr(replay, '_check_integrity', lambda *_args: [])
    session = state.sessions.create(task.id)
    captured = []
    state.sessions.launch_run = lambda value: captured.append(deepcopy(value)) or SimpleNamespace(id='group-child', status='queued')
    response = state.sessions.add_materials(session['session_id'], {'scope_clarifications': alternate})
    assert response['new_version'] and response['parent_run_id'] == task.id
    assert captured[-1]['scope_clarifications'] == alternate
    state.sessions.add_materials(session['session_id'], {'draft': str(next_draft)})
    assert captured[-1]['scope_clarifications'] == []
    assert {path.name: path.read_bytes() for path in task.out.iterdir() if path.is_file()} == before


@pytest.mark.parametrize('specs', [None, True, {}, ['合并'],
    [{'scope': '合并', 'selector': {}}],
    [{'scope': '合并', 'selector': {'accounts': []}}],
    [{'scope': '合并', 'selector': {'accounts': ['REVENUE', 'REVENUE']}}],
    [{'scope': '合并', 'selector': {'accounts': ['REVENUE']}, 'value': '9999'}],
    [{'scope': '合并', 'selector': {'path': ['../facts.json']}}],
])
def test_web_launch_rerun_and_session_reject_invalid_group_lists(local_web, monkeypatch, specs):
    state, request, task, body, before = frozen_web_parent(local_web, monkeypatch)
    with pytest.raises(urllib.error.HTTPError) as launch:
        request('/api/run', dict(body, scope_clarifications=specs), 'POST')
    assert launch.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as rerun:
        request(f'/api/runs/{task.id}/rerun', {'scope_clarifications': specs}, 'POST')
    assert rerun.value.code == 400
    from mingjing import replay
    monkeypatch.setattr(replay, '_check_integrity', lambda *_args: [])
    session = state.sessions.create(task.id)
    with pytest.raises(urllib.error.HTTPError) as session_response:
        request(f"/api/sessions/{session['session_id']}/materials", {'scope_clarifications': specs}, 'POST')
    assert session_response.value.code == 400
    assert {path.name: path.read_bytes() for path in task.out.iterdir() if path.is_file()} == before


def test_launch_snapshot_is_detached_from_callers_nested_group_parameters(local_web, monkeypatch):
    state, _request = local_web
    draft, corpus = material(state.runs_root.parent/'queue-materials')
    monkeypatch.setattr(server.RunTask, 'start', lambda self: None)
    body = {'draft': str(draft), 'corpus': str(corpus), 'mode': 'kernel', 'scope_clarifications': deepcopy(SPECS)}
    task = state.launch(body)
    body['scope_clarifications'][0]['scope'] = '母公司'
    body['scope_clarifications'][0]['selector']['accounts'].append('CFO')
    assert task.request['scope_clarifications'] == SPECS


@pytest.mark.parametrize('frozen', [False, True], ids=['actual-cli-child', 'frozen-in-process'])
def test_web_worker_passes_grouped_snapshot_to_actual_kernel_command(local_web, monkeypatch, frozen):
    state, request = local_web
    if frozen:
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
    else:
        monkeypatch.delattr(sys, 'frozen', raising=False)
    draft, corpus = material(state.runs_root.parent/'worker-materials')
    specs = deepcopy(SPECS)
    body = {'draft': str(draft), 'corpus': str(corpus), 'company': '甲公司', 'period': '2024Y',
            'report_date': '2025-03-31', 'mode': 'kernel', 'scope_clarifications': specs}
    task = state.launch(body)
    specs[0]['scope'] = '母公司'
    specs[0]['selector']['accounts'].append('CFO')
    task.thread.join(15)
    assert not task.thread.is_alive() and task.status == 'completed', task.error
    frozen_request = read(task.out/'run_request.json')
    data = request(f'/api/runs/{task.id}/results')
    assert frozen_request['scope_clarifications'] == data['scope_clarifications'] == SPECS
    assert data['coverage_statistics']['core']['conditional_counts']['verified'] == 1
    assert data['coverage_statistics']['core']['counts']['abstained'] == 1
    assert Path(data['draft']).parent == task.out/'input'
    assert (task.out/'scope_clarifications.json').is_file()
    replay = request('/api/replay', {'run': str(task.out)}, 'POST')
    assert replay['status'] == 'passed', replay


def test_global_and_grouped_scope_cannot_mix_on_web_launch(local_web, monkeypatch):
    state, request = local_web
    draft, corpus = material(state.runs_root.parent/'mixed-materials')
    monkeypatch.setattr(server.RunTask, 'start', lambda self: None)
    with pytest.raises(urllib.error.HTTPError) as response:
        request('/api/run', {'draft': str(draft), 'corpus': str(corpus), 'mode': 'kernel',
                           'scope_clarifications': SPECS, 'scope_declaration': '合并'}, 'POST')
    assert response.value.code == 400
    assert not state.tasks


@pytest.mark.parametrize('global_parent', [False, True], ids=['group-to-global', 'global-to-group'])
def test_rerun_and_session_explicit_scope_mode_replaces_opposite_parent_mode(local_web, monkeypatch, global_parent):
    state, request, task, _, before = frozen_web_parent(local_web, monkeypatch, global_parent=global_parent)
    overrides = ({'scope_clarifications': deepcopy(SPECS)} if global_parent else {'scope_declaration': '母公司'})
    child = request(f'/api/runs/{task.id}/rerun', overrides, 'POST')['request']
    if global_parent:
        assert child['scope_clarifications'] == SPECS and child['scope_declaration'] == ''
    else:
        assert child['scope_declaration'] == '母公司' and child.get('scope_clarifications', []) == []
    from mingjing import replay
    monkeypatch.setattr(replay, '_check_integrity', lambda *_args: [])
    session = state.sessions.create(task.id)
    captured = []
    state.sessions.launch_run = lambda value: captured.append(deepcopy(value)) or SimpleNamespace(id='mode-child', status='queued')
    state.sessions.add_materials(session['session_id'], overrides)
    if global_parent:
        assert captured[-1]['scope_clarifications'] == SPECS and captured[-1]['scope_declaration'] == ''
    else:
        assert captured[-1]['scope_declaration'] == '母公司' and captured[-1]['scope_clarifications'] == []
    assert {path.name: path.read_bytes() for path in task.out.iterdir() if path.is_file()} == before


def test_explicitly_mixed_modes_remain_rejected_on_rerun_and_session(local_web, monkeypatch):
    state, request, task, _, before = frozen_web_parent(local_web, monkeypatch)
    overrides = {'scope_declaration': '母公司', 'scope_clarifications': deepcopy(SPECS)}
    with pytest.raises(urllib.error.HTTPError) as response:
        request(f'/api/runs/{task.id}/rerun', overrides, 'POST')
    assert response.value.code == 400
    from mingjing import replay
    monkeypatch.setattr(replay, '_check_integrity', lambda *_args: [])
    session = state.sessions.create(task.id)
    with pytest.raises(ValueError, match='不能同时'):
        state.sessions.add_materials(session['session_id'], overrides)
    assert {path.name: path.read_bytes() for path in task.out.iterdir() if path.is_file()} == before
