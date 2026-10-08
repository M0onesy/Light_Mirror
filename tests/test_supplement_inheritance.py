"""Material versions retain and revalidate registered PDF supplements."""
import json
from pathlib import Path

import pytest

from mingjing.agent import write_json
from mingjing.cli import build_parser, command_check
from mingjing.evidence import sha256_file
from mingjing.reparse_inputs import apply_task_reparse_packets, load_parent_context
from mingjing.task_versions import material_version_request, plan_supplement_inheritance
from test_reparse_web import frozen_parent
from test_reparse_packets import source
from test_server_analysis_product import local_web


@pytest.fixture
def supplemented(local_web, source):
    state, request = local_web
    original = frozen_parent(state, source, draft_text='甲公司2024年营业收入100元。')
    packet = request(f'/api/runs/{original.id}/reparse', {'report_id': source['block']['report_id'], 'page': 3}, 'POST')
    launched = request(f'/api/runs/{original.id}/rerun', {'reparse_packet_ids': [packet['packet_id']]}, 'POST')
    child = state.get_task(launched['id'])
    child.thread.join(15)
    assert child.status == 'completed', child.error
    data = json.loads((child.out/'results.json').read_text(encoding='utf-8'))
    accepted = json.loads((child.out/'run_request.json').read_text(encoding='utf-8'))
    assert data['reparse_supplements']
    return state, child, data, accepted


def next_request(fixture):
    _, child, data, accepted = fixture
    return material_version_request(accepted, data, inherit_supplements=True)


def run_inherited(fixture, destination, *, additions=None, scope=''):
    _, child, _, _ = fixture
    request = next_request(fixture)
    request['scope_declaration'] = scope
    if additions:
        request['corpus_additions'] = [*request.get('corpus_additions', []), str(additions)]
    plan = plan_supplement_inheritance(child.out, request)
    destination.mkdir()
    write_json(destination/'run_request.json', dict(request, reparse_inheritance=plan))
    arguments = ['check', '--draft', request['draft'], '--corpus', request['corpus'], '--company', request['company'],
                 '--period', request['period'], '--report-date', request['report_date'], '--out', str(destination),
                 '--run-id', destination.name, '--mode', 'kernel', '--inherit-supplements-from', str(child.out)]
    if scope:
        arguments.extend(['--scope-declaration', scope])
    for addition in request.get('corpus_additions', []):
        arguments.extend(['--corpus-addition', addition])
    args = build_parser().parse_args(arguments)
    assert command_check(args) == 0
    return json.loads((destination/'results.json').read_text(encoding='utf-8'))


def test_same_material_and_scope_clarification_preserve_raw_page_proof(supplemented, tmp_path):
    _, child, old, _ = supplemented
    before = {path: path.read_bytes() for path in child.out.rglob('*') if path.is_file()}
    output = run_inherited(supplemented, tmp_path/'inherited-scope', scope='合并')
    assert output['summary']['verified'] == 1
    assert len(output['reparse_supplements']) == len(old['reparse_supplements'])
    proof = output['reparse_supplements'][0]
    assert proof['source_pages_revalidated'] and proof['inherited_from_run'] == child.id
    assert proof['source_generation'] == old['reparse_supplements'][0]['source_generation']
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_only_appended_material_keeps_old_supplement_and_new_baseline(supplemented, tmp_path):
    _, _, old, _ = supplemented
    addition = tmp_path/'additional.txt'
    addition.write_text('甲公司合并财务报表\n2022年营业收入50元。', encoding='utf-8')
    output = run_inherited(supplemented, tmp_path/'inherited-added', additions=addition, scope='合并')
    assert output['summary']['verified'] == 1
    assert output['reparse_supplements'][0]['packet_id'] == old['reparse_supplements'][0]['packet_id']
    assert output['reparse_supplements'][0]['source_pages_revalidated']
    assert str(addition) in output['corpus_additions']


def test_grandchild_inherits_original_packet_and_revalidates_original_pages(supplemented, tmp_path):
    state, child, original, _ = supplemented
    first_path = tmp_path/'inherited-child'
    first = run_inherited(supplemented, first_path, scope='合并')
    accepted = json.loads((first_path/'run_request.json').read_text(encoding='utf-8'))
    next_fixture = (state, type('FrozenVersion', (), {'out': first_path, 'id': first['run_id']})(), first, accepted)
    second = run_inherited(next_fixture, tmp_path/'inherited-grandchild', scope='合并')
    proof = second['reparse_supplements'][0]
    assert second['summary']['verified'] == 1
    assert len(second['reparse_supplements']) == 1
    assert proof['packet_id'] == original['reparse_supplements'][0]['packet_id']
    assert proof['source_generation'] == original['reparse_supplements'][0]['source_generation']
    assert proof['inherited_from_run'] == first['run_id']
    assert proof['source_pages_revalidated']
    assert load_parent_context(child.out)['source_generation']['run_id'] == child.id


@pytest.mark.parametrize('change', ['company', 'cutoff', 'draft', 'corpus', 'rules'])
def test_invalidated_dimensions_are_refused_before_source_proof_disappears(supplemented, tmp_path, change, monkeypatch):
    _, child, _, _ = supplemented
    request = next_request(supplemented)
    before = {path: path.read_bytes() for path in child.out.rglob('*') if path.is_file()}
    if change == 'company':
        request['company'] = '另一家公司'
    elif change == 'cutoff':
        request['report_date'] = '2025-04-30'
    elif change == 'draft':
        draft = tmp_path/'other.md'
        draft.write_text('Changed draft', encoding='utf-8')
        request['draft'] = str(draft)
    elif change == 'corpus':
        request['corpus'] = str(tmp_path)
    else:
        from mingjing import rules
        root = tmp_path/'other-rules'
        root.mkdir()
        write_json(root/'sources.json', {'version_hash': 'changed-rulepack'})
        monkeypatch.setattr(rules, 'rulepack_root', lambda: root)
    with pytest.raises(ValueError, match='补证材料不能继承'):
        plan_supplement_inheritance(child.out, request)
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_inherited_packet_conflicts_with_new_baseline_are_explicit(supplemented):
    from dataclasses import replace
    from decimal import Decimal
    _, child, _, _ = supplemented
    request = next_request(supplemented)
    request['reparse_inheritance'] = plan_supplement_inheritance(child.out, request)
    baseline_parent = json.loads((child.out/'run_request.json').read_text(encoding='utf-8'))['reparse_parent_run']
    context = load_parent_context(baseline_parent)
    supplemented_context = load_parent_context(child.out)
    revenue = next(fact for fact in supplemented_context['baseline_facts'] if fact.account == 'REVENUE' and fact.period == '2024Y')
    conflicting = [*context['baseline_facts'], replace(revenue, value=Decimal('999'))]
    with pytest.raises(ValueError, match='new material baseline'):
        apply_task_reparse_packets(request, conflicting)
