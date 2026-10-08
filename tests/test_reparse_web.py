"""Frozen real-PDF page supplements enter only a new local task version."""
import json
import threading
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import urllib.error

import pytest

from mingjing import server
from mingjing.cli import build_parser, command_check
from mingjing.coverage import partition_document
from mingjing.evidence import make_evidence_id
from mingjing.file_io import io_path
from mingjing.reparse import create_reparse_packet, load_reparse_packet, resolve_reparse_packet
from test_reparse_packets import source
from test_server_analysis_product import local_web


def frozen_parent(state, source, *, run_id='reparse-parent', status='ready', draft_text=None):
    resource = source['source_resources'][0]
    pdf = Path(resource['path'])
    _, _, blocks = partition_document(pdf)
    row = next(block for block in blocks if '净利润' in block['text'])
    baseline = [replace(source['baseline_facts'][0], evidence=(
        make_evidence_id(resource['sha256'], row['page'], row['text'].strip(), row['bbox']),))]
    if status == 'conflicts':
        baseline = [replace(baseline[0], value=Decimal('999'))]
    elif status == 'no_new_information':
        summary = create_reparse_packet(**source)
        new_facts = load_reparse_packet(resolve_reparse_packet(source['output_root'], summary['packet_id']),
            source_resources=source['source_resources'], baseline_facts=source['baseline_facts'],
            company=source['company'], as_of_date=source['as_of_date'],
            expected_source_generation=source['source_generation'])
        # Deliberately incomplete, human-structured test input. This is not a
        # reusable machine extraction cache or an independent financial gold.
        baseline.extend(replace(fact, source_type='structured', extractor_version='structured') for fact in new_facts)
    (pdf.parent / 'facts.json').write_text(json.dumps([fact.to_dict() for fact in baseline], ensure_ascii=False), encoding='utf-8')
    draft = pdf.parent / 'draft.md'
    draft.write_text(draft_text or '甲公司2024年合并营业收入100元。', encoding='utf-8')
    body = dict(draft=str(draft), corpus=str(pdf.parent), company='甲公司', period='2024Y',
        report_date='2025-03-31', task_type='check', mode='kernel')
    task = server.RunTask(run_id, body, state.runs_root, state.config)
    task.out.mkdir()
    (task.out / 'run_request.json').write_text(json.dumps(body), encoding='utf-8')
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(pdf.parent),
        '--company', '甲公司', '--period', '2024Y', '--report-date', '2025-03-31',
        '--out', str(task.out), '--run-id', run_id])
    assert command_check(args) == 0
    task.status = 'completed'
    task.thread = threading.Thread(target=lambda: None, daemon=True)
    task.thread.start()
    task.thread.join()
    state.tasks[task.id] = task
    return task


@pytest.mark.parametrize('frozen', [False, True], ids=['source-child', 'frozen-in-process'])
def test_pdf_page_packet_new_version_and_original_freeze(local_web, source, monkeypatch, frozen):
    state, request = local_web
    if frozen:
        monkeypatch.setattr(server.sys, 'frozen', True, raising=False)
    parent = frozen_parent(state, source)
    before = {path.relative_to(parent.out): path.read_bytes() for path in parent.out.rglob('*') if path.is_file()}
    data = json.loads((parent.out / 'results.json').read_text(encoding='utf-8'))
    assert data['summary']['abstained'] == 1
    identifier = data['facts'][0]['evidence'][0]
    capability = request(f'/api/evidence/{identifier}?run={parent.id}')['reparse']
    assert capability == {'available': True, 'report_id': source['block']['report_id'], 'page': 3}
    packet = request(f'/api/runs/{parent.id}/reparse', {'report_id': capability['report_id'],
        'page': 3, 'binding_id': data['claims'][0]['claim_id']}, 'POST')
    assert packet['status'] == 'ready' and packet['new_facts_count'] > 0
    assert packet['proof']['pages'] == [2, 3, 4] and packet['frozen_parent_unchanged']
    packets = request(f'/api/runs/{parent.id}/reparse-packets')['packets']
    assert [row['packet_id'] for row in packets] == [packet['packet_id']]
    preview = request(f'/api/runs/{parent.id}/reparse-packets/{packet["packet_id"]}')
    assert preview['readonly'] and not preview['applied_to_current_task']
    revenue = next(fact for fact in preview['facts'] if fact['account'] == 'REVENUE' and fact['period'] == '2024Y')
    assert revenue['value'] == '100.00' and revenue['display_value'] == '100 元'
    assert revenue['company'] == '甲公司' and revenue['scope'] == '合并'
    assert all('source_path' not in fact for fact in preview['facts'])
    evidence = request(f'/api/runs/{parent.id}/reparse-packets/{packet["packet_id"]}/evidence/{revenue["evidence"][0]}')
    assert evidence['reference_status'] == 'reparse_fact_preview' and evidence['readonly']
    assert evidence['page'] == 3 and evidence['document_sha256'] == source['source_resources'][0]['sha256']
    assert 'source_path' not in evidence and 'fact' not in evidence
    launched = request(f'/api/runs/{parent.id}/rerun', {'reparse_packet_ids': [packet['packet_id']]}, 'POST')
    task = state.get_task(launched['id'])
    task.thread.join(15)
    assert task.status == 'completed', task.error
    new_data = request(f'/api/runs/{task.id}/results')
    assert new_data['summary'] == {'verified': 1, 'violated': 0, 'abstained': 0}
    assert task.request['parent_run_id'] == parent.id
    assert task.request['reparse_packets'][0]['packet_id'] == packet['packet_id']
    path = Path(task.request['reparse_packets'][0]['path'])
    assert path.parent == parent.root / 'reparse' / parent.id and not path.is_relative_to(parent.out)
    assert request('/api/replay', {'run': str(task.out)}, 'POST')['status'] == 'passed'
    assert all((parent.out / path).read_bytes() == raw for path, raw in before.items())
    assert state.get_task(parent.id).status == 'completed'
    for overrides in ({}, {'report_date': '2025-04-30'}):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(f'/api/runs/{task.id}/rerun', overrides, 'POST')
        assert error.value.code == 400
        assert '补证材料' in error.value.read().decode('utf-8')
    assert len(state.tasks) == 2


@pytest.mark.parametrize('status', ['conflicts', 'no_new_information'])
def test_conflict_and_no_information_packets_remain_visible_but_cannot_be_applied(local_web, source, status):
    state, request = local_web
    parent = frozen_parent(state, source, status=status)
    packet = request(f'/api/runs/{parent.id}/reparse', {'report_id': source['block']['report_id'], 'page': 3}, 'POST')
    assert packet['status'] == status
    assert request(f'/api/runs/{parent.id}/reparse-packets')['packets'][0]['status'] == status
    with pytest.raises(urllib.error.HTTPError) as preview_error:
        request(f'/api/runs/{parent.id}/reparse-packets/{packet["packet_id"]}')
    assert preview_error.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as error:
        request(f'/api/runs/{parent.id}/rerun', {'reparse_packet_ids': [packet['packet_id']]}, 'POST')
    assert error.value.code == 400
    assert len(state.tasks) == 1


@pytest.mark.parametrize('change', [
    {'path': 'C:/private.pdf'}, {'candidate_value': '999999'}, {'formula': 'free'},
    {'page': True}, {'page': 0}, {'page': 999}, {'binding_id': 'another-claim'},
    {'report_id': 'report-' + 'f' * 20}, {'report_id': 'draft'},
])
def test_reparse_api_rejects_free_paths_values_formulas_and_out_of_scope_ids(local_web, source, change):
    state, request = local_web
    parent = frozen_parent(state, source)
    with pytest.raises(urllib.error.HTTPError) as error:
        request(f'/api/runs/{parent.id}/reparse', {'report_id': source['block']['report_id'], 'page': 3, **change}, 'POST')
    assert error.value.code == 400
    assert not (parent.root / 'reparse' / parent.id).exists()


@pytest.mark.parametrize('change', ['other_parent', 'changed_pdf', 'changed_material_parameter', 'forged_packet', 'public_packet_path'])
def test_packet_application_checks_parent_and_material_generation(local_web, source, change):
    state, request = local_web
    parent = frozen_parent(state, source)
    packet = request(f'/api/runs/{parent.id}/reparse', {'report_id': source['block']['report_id'], 'page': 3}, 'POST')
    body, target = {'reparse_packet_ids': [packet['packet_id']]}, parent.id
    if change == 'other_parent':
        other = frozen_parent(state, source, run_id='other-parent')
        target = other.id
    elif change == 'changed_pdf':
        Path(source['source_resources'][0]['path']).write_bytes(b'changed locked PDF')
    elif change == 'changed_material_parameter':
        body['report_date'] = '2025-04-30'
    elif change == 'forged_packet':
        path = parent.root / 'reparse' / parent.id / (packet['packet_id'] + '.json')
        payload = json.loads(io_path(path).read_text(encoding='utf-8'))
        payload['new_facts'][0]['value'] = '999999'
        io_path(path).write_text(json.dumps(payload), encoding='utf-8')
    else:
        target = ''
        body = dict(parent.request, reparse_packets=[{'path': str(parent.root / 'reparse' / parent.id)}])
    with pytest.raises(urllib.error.HTTPError) as error:
        request(f'/api/runs/{target}/rerun' if target else '/api/run', body, 'POST')
    assert error.value.code == 400
    assert not any(task.thread.is_alive() for task in state.tasks.values())


def test_packet_application_uses_shared_twelve_packet_budget(local_web, source):
    state, request = local_web
    parent = frozen_parent(state, source)
    with pytest.raises(urllib.error.HTTPError) as error:
        request(f'/api/runs/{parent.id}/rerun', {'reparse_packet_ids': [f'rp-{index:064x}' for index in range(13)]}, 'POST')
    assert error.value.code == 400
    assert '最多12项' in error.value.read().decode('utf-8')
    assert len(state.tasks) == 1


@pytest.mark.parametrize('change', ['changed_pdf', 'changed_parent', 'changed_packet', 'wrong_parent',
    'unknown_evidence', 'extra_path', 'extra_page', 'extra_candidate', 'unsupported_format'])
def test_packet_review_revalidates_locked_materials_and_limits_evidence(local_web, source, change):
    state, request = local_web
    parent = frozen_parent(state, source)
    packet = request(f'/api/runs/{parent.id}/reparse', {'report_id': source['block']['report_id'], 'page': 3}, 'POST')
    preview = request(f'/api/runs/{parent.id}/reparse-packets/{packet["packet_id"]}')
    identifier = preview['facts'][0]['evidence'][0]
    target = f'/api/runs/{parent.id}/reparse-packets/{packet["packet_id"]}'
    if change == 'changed_pdf':
        Path(source['source_resources'][0]['path']).write_bytes(b'changed locked PDF')
    elif change == 'changed_parent':
        (parent.out / 'results.json').write_text('{}', encoding='utf-8')
    elif change == 'changed_packet':
        path = parent.root / 'reparse' / parent.id / (packet['packet_id'] + '.json')
        payload = json.loads(io_path(path).read_text(encoding='utf-8'))
        payload['new_facts'][0]['value'] = '999999'
        io_path(path).write_text(json.dumps(payload), encoding='utf-8')
    elif change == 'wrong_parent':
        other = frozen_parent(state, source, run_id='another-frozen-parent')
        target = target.replace(parent.id, other.id)
    else:
        target += '/evidence/' + ('missing-evidence' if change == 'unknown_evidence' else identifier)
        target += {'extra_path': '?path=C:/private.pdf', 'extra_page': '?page=4',
            'extra_candidate': '?candidate_value=999999', 'unsupported_format': '?format=pdf'}.get(change, '')
    with pytest.raises(urllib.error.HTTPError) as error:
        request(target)
    assert error.value.code == 400
    assert not any(task.thread.is_alive() for task in state.tasks.values())
