"""Supplemental reports preserve original facts and receive fresh source locks."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error

import pytest

from mingjing import server
from mingjing.cli import build_parser, command_check
from mingjing.execution import project_result_view
from mingjing.materials import normalize_corpus_additions
from mingjing.replay import replay_run
from test_server_analysis_product import local_web


def _facts(root, period, value):
    root.mkdir()
    report = root / 'report.txt'
    report.write_text('虚构财报材料，供受控产品回归使用。', encoding='utf-8')
    row = {'fact_id': 'revenue-' + period, 'company': '公司甲', 'account': 'REVENUE',
           'period': period, 'scope': '合并', 'value': str(value), 'publish_date': '2025-03-01',
           'source_path': report.name}
    (root / 'facts.json').write_text(json.dumps([row], ensure_ascii=False), encoding='utf-8')
    return root


@pytest.mark.parametrize('value', ['file.pdf', {}, [12], [''], ['x'] * 33])
def test_additional_materials_reject_nonpath_or_unbounded_requests(tmp_path, value):
    with pytest.raises(ValueError, match='corpus_additions'):
        normalize_corpus_additions(value, tmp_path)


def test_additions_are_normalized_deduplicated_and_constrained_to_allowed_roots(tmp_path):
    main = tmp_path / 'main'
    main.mkdir()
    addition = tmp_path / 'new.txt'
    addition.write_text('材料', encoding='utf-8')
    values = normalize_corpus_additions([str(main), str(addition), str(addition)], main, allowed_roots=[str(tmp_path)])
    assert values == [str(addition.resolve())]
    with pytest.raises(ValueError):
        normalize_corpus_additions([str(addition)], main, allowed_roots=[str(main)])


def _frozen_source(state, tmp_path):
    draft = tmp_path / 'source.md'
    draft.write_text('公司合并口径2023年营业收入为100元。', encoding='utf-8')
    corpus = _facts(tmp_path / 'primary-corpus', '2023Y', 100)
    source = server.RunTask('supplement-source', {'draft': str(draft), 'corpus': str(corpus),
                            'company': '公司甲', 'period': '2024Y', 'report_date': '2025-03-31',
                            'mode': 'kernel'}, state.runs_root, state.config)
    source.out.mkdir()
    (source.out / 'run_request.json').write_text(json.dumps(source.request), encoding='utf-8')
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(corpus),
        '--company', '公司甲', '--period', '2024Y', '--report-date', '2025-03-31',
        '--out', str(source.out), '--run-id', source.id])
    assert command_check(args) == 0
    source.status = 'completed'
    state.tasks[source.id] = source
    return source


def test_http_scope_only_material_creates_new_version_without_changing_source(local_web, tmp_path):
    state, request = local_web
    source = _frozen_source(state, tmp_path)
    original = (source.out / 'run_request.json').read_bytes()
    session = request('/api/sessions', {'run_id': source.id}, 'POST')
    captured = []
    state.sessions.launch_run = lambda body: captured.append(body) or SimpleNamespace(id='scope-next', status='queued')
    response = request(f"/api/sessions/{session['session_id']}/materials", {'scope_declaration': '母公司'}, 'POST')
    assert response['new_version'] and response['parent_run_id'] == source.id
    assert captured[0]['scope_declaration'] == '母公司'
    assert captured[0]['corpus'] == source.request['corpus']
    assert (source.out / 'run_request.json').read_bytes() == original
    for invalid in ({'scope_declaration': '随意口径'}, {'scope_declaration': {'scope': '合并'}},
                    {'scope_declaration': '合并', 'candidate_value': '99999'}, {}):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(f"/api/sessions/{session['session_id']}/materials", invalid, 'POST')
        assert error.value.code == 400
    assert len(captured) == 1


def test_http_material_supplement_preserves_original_corpus_and_creates_new_version(local_web, tmp_path):
    state, request = local_web
    source = _frozen_source(state, tmp_path)
    additional = _facts(tmp_path / 'additional-corpus', '2024Y', 120)
    saved_results = (source.out / 'results.json').read_bytes()
    saved_request = (source.out / 'run_request.json').read_bytes()
    session = request('/api/sessions', {'run_id': source.id}, 'POST')
    captured = []
    state.sessions.launch_run = lambda body: captured.append(body) or SimpleNamespace(id='supplement-next', status='queued')
    response = request(f"/api/sessions/{session['session_id']}/materials", {'corpus': str(additional)}, 'POST')
    assert response['new_version'] and response['parent_run_id'] == source.id
    assert captured[0]['corpus'] == source.request['corpus']
    assert captured[0]['corpus_additions'] == [str(additional.resolve())]
    assert captured[0]['source_session_id'] == session['session_id']
    assert captured[0]['report_date'] == '2025-03-31'
    assert (source.out / 'results.json').read_bytes() == saved_results
    assert (source.out / 'run_request.json').read_bytes() == saved_request


@pytest.mark.parametrize('frozen', [False, True])
def test_web_kernel_carries_additions_and_preserves_them_after_restart_and_rerun(local_web, monkeypatch, tmp_path, frozen):
    state, request = local_web
    primary = _facts(tmp_path / 'old', '2023Y', 100)
    additional = _facts(tmp_path / 'new', '2024Y', 120)
    draft = tmp_path / 'draft.md'
    draft.write_text('公司合并口径2023年营业收入100元。公司合并口径2024年营业收入120元。', encoding='utf-8')
    if frozen:
        monkeypatch.setattr(server.sys, 'frozen', True, raising=False)
    launched = request('/api/run', {'draft': str(draft), 'corpus': str(primary), 'corpus_additions': [str(additional)],
        'company': '公司甲', 'period': '2024Y', 'report_date': '2025-03-31', 'mode': 'kernel'}, 'POST')
    task = state.tasks[launched['id']]
    task.thread.join(10)
    assert task.status == 'completed', task.error
    data = request(f'/api/runs/{task.id}/results')
    assert data['summary'] == {'verified': 2, 'violated': 0, 'abstained': 0}
    assert data['period'] == '2024Y' and data['corpus_additions'] == [str(additional.resolve())]
    source_paths = {Path(f['source_path']) for f in data['facts']}
    assert source_paths == {primary / 'report.txt', additional / 'report.txt'}
    manifest = json.loads((task.out / 'run_manifest.json').read_text(encoding='utf-8'))
    locked = {Path(row['path']) for row in manifest['inputs']}
    assert {primary / 'facts.json', primary / 'report.txt', additional / 'facts.json', additional / 'report.txt'} <= locked
    assert replay_run(task.out)['status'] == 'passed'
    original_results = (task.out / 'results.json').read_bytes()
    restored = server.AppState().get_task(task.id)
    assert restored.summary()['request']['corpus_additions'] == [str(additional.resolve())]
    monkeypatch.setattr(server.RunTask, 'start', lambda self: None)
    rerun = request(f'/api/runs/{task.id}/rerun', {}, 'POST')
    assert rerun['request']['corpus'] == str(primary.resolve())
    assert rerun['request']['corpus_additions'] == [str(additional.resolve())]
    assert (task.out / 'results.json').read_bytes() == original_results
    (additional / 'facts.json').write_text('[]', encoding='utf-8')
    assert replay_run(task.out)['status'] == 'failed'


def test_primary_fallback_facts_survive_supplemental_material(tmp_path):
    primary = tmp_path / 'empty-primary'
    primary.mkdir()
    additional = _facts(tmp_path / 'additional', '2024Y', 120)
    fallback = tmp_path / 'fallback.json'
    fallback.write_text(json.dumps([{'fact_id': 'prior', 'company': '公司甲', 'account': 'REVENUE',
        'period': '2023Y', 'scope': '合并', 'value': '100', 'publish_date': '2025-03-01'}]), encoding='utf-8')
    draft = tmp_path / 'draft.md'
    draft.write_text('公司合并口径2023年营业收入100元。公司合并口径2024年营业收入120元。', encoding='utf-8')
    out = tmp_path / 'result'
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(primary),
        '--corpus-addition', str(additional), '--facts', str(fallback), '--company', '公司甲',
        '--report-date', '2025-03-31', '--out', str(out)])
    assert command_check(args) == 0
    result = json.loads((out / 'results.json').read_text(encoding='utf-8'))
    assert {row['period'] for row in result['facts']} == {'2023Y', '2024Y'}
    assert result['summary']['verified'] == 2
    assert replay_run(out)['status'] == 'passed'


def test_analysis_display_projection_uses_decimal_without_mutating_frozen_records():
    payload = {'summary': {}, 'claims': [], 'results': [], 'analysis': {'metrics': [
        {'metric': 'REVENUE', 'value': '120', 'status': 'verified'},
        {'metric': 'REVENUE_YOY', 'value': '0.123456789012345678901234567890', 'status': 'verified'},
        {'metric': 'CFO_QOQ', 'value': '0.25', 'status': 'verified'},
        {'metric': 'EPS', 'value': '1.2', 'status': 'verified'},
        {'metric': 'NP', 'value': None, 'status': 'abstained'}],
        'signals': [{'kind': 'non_recurring_share', 'company': '公司甲', 'period': '2024Y',
                     'scope': '合并', 'value': '0.25', 'threshold': '0.2', 'parameter_source': 'team_parameter',
                     'evidence': ['signal-proof']}],
        'events': [{'kind': 'accounting_policy_change', 'company': '公司甲', 'scope': '合并',
                    'source_text': '[外部网址](https://example.invalid)', 'evidence': ['event-proof']}]}}
    original = copy.deepcopy(payload)
    before = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    view = project_result_view(payload)
    texts = [row['display_value'] for row in view['analysis']['metrics']]
    assert texts == ['120 元', '12.345678901234567890123456789%（标准值 0.12345678901234567890123456789，无量纲比例）',
                     '25%（标准值 0.25，无量纲比例）', '1.2 元/股', '未计算']
    assert payload == original
    assert hashlib.sha256(json.dumps(payload).encode()).hexdigest() == before
    assert all('display_value' not in row for row in payload['analysis']['metrics'])
    signal = view['analysis']['signals'][0]
    assert '25%' in signal['display_text'] and '20%' in signal['display_text']
    assert '团队登记参数' in signal['display_text'] and 'signal\\-proof' in signal['display_text']
    event = view['analysis']['events'][0]
    assert '会计政策变更' in event['display_text'] and 'event\\-proof' in event['display_text']
    assert '\\[外部网址\\]\\(https\\:' in event['display_text']
    assert 'display_text' not in payload['analysis']['signals'][0]
    assert 'display_text' not in payload['analysis']['events'][0]


def test_http_analysis_display_projection_preserves_saved_json_bytes(local_web, tmp_path):
    state, request = local_web
    source = _frozen_source(state, tmp_path)
    path = source.out / 'results.json'
    frozen = json.loads(path.read_text(encoding='utf-8'))
    frozen['analysis'] = {'metrics': [{'metric': 'GROSS_MARGIN', 'status': 'verified', 'value': '0.25'}],
                         'signals': [{'kind': 'non_recurring_share', 'value': '0.2', 'threshold': '0.1',
                                      'parameter_source': 'team_parameter', 'evidence': ['signal-proof']}],
                         'events': [{'kind': 'accounting_estimate_change', 'evidence': ['event-proof']}]}
    raw = json.dumps(frozen, ensure_ascii=False).encode('utf-8')
    path.write_bytes(raw)
    view = request(f'/api/runs/{source.id}/results')
    assert view['analysis']['metrics'][0]['display_value'] == '25%（标准值 0.25，无量纲比例）'
    assert '团队登记参数' in view['analysis']['signals'][0]['display_text']
    assert '会计估计变更' in view['analysis']['events'][0]['display_text']
    assert path.read_bytes() == raw
