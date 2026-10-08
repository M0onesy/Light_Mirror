"""User scope is a locked material, never a model-supplied financial value."""
import copy
import hashlib
import json
import urllib.error
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import server
from mingjing.audit import kernel_fingerprint
from mingjing.cli import build_parser, command_check
from mingjing.mentions import extract_mentions
from mingjing.scope import (apply_scope_clarification, create_scope_clarification,
                            scope_clarification_metadata, source_scope_dimensions,
                            validate_scope_declaration)
from test_server_analysis_product import local_web
from test_harness_web_ui import browser_page


def declaration(scope='合并'):
    resource = create_scope_clarification(scope, draft_sha256='a' * 64)
    return scope_clarification_metadata(resource, 'b' * 64)


@pytest.mark.parametrize('value', [None, {}, ['合并'], 1, 'consolidated', '未知', '合并口径'])
def test_scope_declaration_accepts_only_explicit_enums(value):
    with pytest.raises(ValueError, match='scope_declaration'):
        validate_scope_declaration(value)


def test_empty_declaration_does_not_create_material_or_use_parser_default():
    claim = extract_mentions('2023年营业收入为100元。', '甲公司', '2023Y')[0]
    assert claim.caliber_hint[0] == '合并'
    assert source_scope_dimensions(claim)['scope_header'] == ''
    assert create_scope_clarification('', draft_sha256='a' * 64) is None
    assert apply_scope_clarification([claim], None)[0] is claim
    assert 'module:scope' in kernel_fingerprint()


@pytest.mark.parametrize('text,scope', [
    ('2023年合并营业收入为100元。', '合并'),
    ('2023年母公司营业收入为100元。', '母公司'),
    ('2023年归属于母公司股东净利润为100元。', ''),
    ('2023年归属母公司净利润为100元。', '合并'),
    ('2023年不是母公司口径营业收入为100元。', ''),
    ('2023年未按合并口径营业收入为100元。', ''),
    ('2023年合并及母公司报表营业收入为100元。', ''),
])
def test_source_scope_evidence_remains_strict(text, scope):
    claim = extract_mentions(text, '甲公司', '2023Y')[0]
    assert source_scope_dimensions(claim)['scope_header'] == scope


def test_user_material_preserves_source_amount_occurrence_and_unknown_nr():
    claim = extract_mentions('2023年公司营业收入为100元。', '甲公司', '2023Y')[0]
    claim = replace(claim, caliber_hint=('合并', '未知'))
    before = copy.deepcopy(claim.to_dict())
    updated = apply_scope_clarification([claim], declaration('母公司'))[0]
    dimensions = source_scope_dimensions(updated)
    assert dimensions['scope_header'] == '母公司' and dimensions['provenance'] == 'user_supplied_scope'
    assert dimensions['scope_declaration_resource_id'].startswith('scope-clarification-')
    assert updated.caliber_hint == ('母公司', '未知')
    for field in ('claim_id', 'text', 'numeric_text', 'candidate_value', 'start', 'end', 'source_numeric_start'):
        assert getattr(updated, field) == before[field]
    assert claim.to_dict() == before


@pytest.mark.parametrize('text,scope', [
    ('2023年母公司营业收入为100元。', '合并'),
    ('2023年合并营业收入为100元。', '母公司'),
    ('2023年合并及母公司报表营业收入为100元。', '合并'),
    ('2023年母公司及合并报表营业收入为100元。', '母公司'),
    ('2023年未按合并口径营业收入为100元。', '合并'),
])
def test_user_scope_conflicts_do_not_override_explicit_source(text, scope):
    claim = extract_mentions(text, '甲公司', '2023Y')[0]
    before = copy.deepcopy(claim.to_dict())
    with pytest.raises(ValueError, match='冲突'):
        apply_scope_clarification([claim], declaration(scope))
    assert claim.to_dict() == before


def test_table_title_proof_wins_over_default_hint_and_declaration():
    claim = extract_mentions('2023年营业收入为100元。', '甲公司', '2023Y')[0]
    metadata = {'title': '利润表', 'period_source': 'source_header', 'period_hint': '2023Y',
                'account_hint': 'REVENUE', 'scope_hint': '合并', 'unit_hint': '元'}
    claim = replace(claim, table_context=metadata)
    assert source_scope_dimensions(claim)['scope_header'] == ''
    updated = apply_scope_clarification([claim], declaration('母公司'))[0]
    assert source_scope_dimensions(updated)['scope_header'] == '母公司'
    assert source_scope_dimensions(updated)['period_header'] == '2023Y'
    explicit = replace(claim, table_context=dict(metadata, title='合并与母公司利润表'))
    with pytest.raises(ValueError, match='冲突'):
        apply_scope_clarification([explicit], declaration('母公司'))


def test_cli_archives_clarification_and_manifest_without_model_or_value_override(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'cli-declared'
    args = build_parser().parse_args(['check', '--draft', str(root/'samples'/'demo_draft.md'),
        '--corpus', str(root/'samples'/'corpus'), '--report-date', '2025-03-31',
        '--out', str(output), '--mode', 'kernel', '--scope-declaration', '合并'])
    assert command_check(args) == 0
    data = json.loads((output/'results.json').read_text(encoding='utf-8'))
    resource = json.loads((output/'clarifications.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256((output/'clarifications.json').read_bytes()).hexdigest()
    manifest = json.loads((output/'run_manifest.json').read_text(encoding='utf-8'))
    assert data['scope_declaration'] == resource['scope'] == '合并'
    assert data['clarifications'][0]['sha256'] == digest
    assert data['clarifications'][0]['provenance'] == 'user_supplied_scope'
    assert any(row['sha256'] == digest and Path(row['path']).name == 'clarifications.json' for row in manifest['inputs'])
    assert all(c['table_context']['scope_declaration']['sha256'] == digest for c in data['claims'])
    from mingjing.replay import replay_run
    assert replay_run(output)['status'] == 'passed'
    (output/'clarifications.json').write_text(json.dumps(dict(resource, scope='母公司')), encoding='utf-8')
    assert replay_run(output)['status'] == 'failed'


def test_web_run_rerun_and_session_materials_use_explicit_scope_only(local_web, monkeypatch):
    state, request = local_web
    draft = state.runs_root.parent / 'scope-draft.md'
    draft.write_text('2023年营业收入为100元。', encoding='utf-8')
    corpus = state.runs_root.parent / 'scope-corpus'
    corpus.mkdir()
    monkeypatch.setattr(server.RunTask, 'start', lambda self: None)
    body = {'draft': str(draft), 'corpus': str(corpus), 'mode': 'kernel'}
    default = request('/api/run', body, 'POST')
    assert not default['request'].get('scope_declaration')
    explicit = request('/api/run', dict(body, scope_declaration='母公司'), 'POST')
    assert explicit['request']['scope_declaration'] == '母公司'
    with pytest.raises(urllib.error.HTTPError) as exc:
        request('/api/run', dict(body, scope_declaration={'scope': '合并', 'value': 100}), 'POST')
    assert exc.value.code == 400
    source = state.tasks[explicit['id']]
    source.status = 'completed'
    source.out.mkdir()
    saved = dict(body, scope_declaration='母公司')
    (source.out/'run_request.json').write_text(json.dumps(saved), encoding='utf-8')
    (source.out/'results.json').write_text(json.dumps({'draft': str(draft), 'claims': [], 'facts': [], 'results': []}), encoding='utf-8')
    (source.out/'run_manifest.json').write_text(json.dumps({'inputs': [
        {'path': str(draft), 'sha256': hashlib.sha256(draft.read_bytes()).hexdigest()}]}), encoding='utf-8')
    rerun = request(f'/api/runs/{source.id}/rerun', {'scope_declaration': '合并'}, 'POST')
    assert rerun['request']['scope_declaration'] == '合并'
    assert json.loads((source.out/'run_request.json').read_text(encoding='utf-8')) == saved
    # This HTTP parameter-routing fixture intentionally has no real frozen
    # files; source-integrity and material recovery are exercised separately.
    from mingjing import replay
    monkeypatch.setattr(replay, '_check_integrity', lambda *args: [])
    session = state.sessions.create(source.id)
    captured = []
    state.sessions.launch_run = lambda value: captured.append(value) or SimpleNamespace(id='new-scope-version', status='queued')
    changed = state.sessions.add_materials(session['session_id'], {'scope_declaration': '合并'})
    assert changed['new_version'] and changed['parent_run_id'] == source.id
    assert captured[0]['scope_declaration'] == '合并' and captured[0]['draft'] == str(draft)
    assert captured[0]['corpus'] == str(corpus)
    assert json.loads((source.out/'run_request.json').read_text(encoding='utf-8')) == saved
    next_draft = state.runs_root.parent / 'different-draft.md'
    next_draft.write_text('2024年营业收入为200元。', encoding='utf-8')
    changed = request(f'/api/runs/{source.id}/rerun', {'draft': str(next_draft)}, 'POST')
    assert changed['request']['scope_declaration'] == ''
    state.sessions.add_materials(session['session_id'], {'draft': str(next_draft)})
    assert captured[-1]['scope_declaration'] == ''


def test_json_markdown_and_browser_report_disclose_user_material_source(browser_page):
    from mingjing.report import html_report, markdown_report
    page, errors, external = browser_page
    metadata = declaration('母公司')
    payload = {'run_id': 'scope-report-fixture', 'draft_text': '2023年营业收入为100元。',
               'scope_declaration': '母公司', 'clarifications': [metadata], 'claims': [],
               'facts': [], 'results': [], 'summary': {'verified': 0, 'violated': 0, 'abstained': 0}}
    markdown = markdown_report(payload)
    assert '本轮口径：母公司' in markdown and '来源为用户补充声明' in markdown
    assert metadata['resource_id'] in markdown and metadata['sha256'] in markdown
    page.set_content(html_report(payload))
    header = page.locator('header').inner_text()
    assert '本轮口径：母公司' in header and '来源为用户补充声明' in header
    assert '不能据此声称原文明示该口径' in header
    assert metadata['resource_id'] in header and metadata['sha256'] in header
    assert not errors and not external
    plain = dict(payload, scope_declaration='', clarifications=[])
    page.set_content(html_report(plain))
    assert '用户补充声明' not in page.locator('header').inner_text()
