"""Active native PDF reads never replace the frozen financial truth."""
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from mingjing.cli import build_parser, command_check
from mingjing.file_io import io_path
from mingjing.reparse import (create_reparse_packet, freeze_staged_reparse_packets,
                            load_reparse_packet, resolve_reparse_packet)
from mingjing.reparse_inputs import load_parent_context
from test_reparse_packets import source


def staged(source, **changes):
    return create_reparse_packet(**dict(source, source_generation=None, staged=True, **changes))


def freeze_parent(source):
    out = source['output_root'].parent / 'frozen-parent'
    out.mkdir(parents=True)
    draft = Path(source['source_resources'][0]['path']).parent / 'draft.md'
    draft.write_text('甲公司2024年合并营业收入100元。', encoding='utf-8')
    facts = draft.parent / 'facts.json'
    facts.write_text(json.dumps([fact.to_dict() for fact in source['baseline_facts']], ensure_ascii=False), encoding='utf-8')
    staged_args = dict(source, output_root=out / 'staged-reparse', source_generation=None)
    proposal = create_reparse_packet(**staged_args, staged=True)
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(draft.parent),
        '--company', source['company'], '--period', '2024Y', '--report-date', source['as_of_date'],
        '--out', str(out), '--run-id', 'frozen-parent'])
    assert command_check(args) == 0
    return out, proposal


def test_staged_packet_is_stable_without_output_path_or_clock_in_public_tool_result(source):
    left = staged(source)
    right = staged(source, output_root=source['output_root'].parent / 'replay-stage')
    assert left == right
    assert left['status'] == 'staged' and left['source_generation'] == {}
    assert left['stage_id'] == 'stage-' + left['packet_sha256']
    assert 'source_path' not in json.dumps(left) and 'new_facts' not in left
    original = [fact.to_dict() for fact in source['baseline_facts']]
    packet = resolve_reparse_packet(source['output_root'], left['packet_id'])
    with pytest.raises(ValueError, match='staged packets require freezing'):
        load_reparse_packet(packet, source_resources=source['source_resources'],
            baseline_facts=source['baseline_facts'], company=source['company'], as_of_date=source['as_of_date'])
    assert [fact.to_dict() for fact in source['baseline_facts']] == original


def test_staging_conflicts_stay_conflicts_and_no_information_stays_visible(source):
    conflicting = [replace(source['baseline_facts'][0], value=Decimal('999'))]
    assert staged(source, baseline_facts=conflicting)['status'] == 'conflicts'
    packet = json.loads(io_path(resolve_reparse_packet(source['output_root'], staged(source)['packet_id'])).read_text(encoding='utf-8'))
    from mingjing.facts import fact_from_dict
    complete = [*source['baseline_facts'], *[fact_from_dict(row) for row in packet['new_facts']]]
    empty = staged(source, baseline_facts=complete)
    assert empty['status'] == 'no_new_information' and empty['new_facts_count'] == 0 and empty['staged']


@pytest.mark.parametrize('changes', [{'staged': 1}, {'source_generation': None}, {'staged': True}])
def test_generation_and_staging_authority_are_explicit(source, changes):
    with pytest.raises(ValueError, match='Staging requires|complete frozen-parent generation'):
        create_reparse_packet(**dict(source, **changes))
    assert not source['output_root'].exists()


def test_frozen_parent_promotes_reconstructed_staging_to_new_version_only(source):
    out, proposal = freeze_parent(source)
    before = {path: path.read_bytes() for path in out.rglob('*') if path.is_file()}
    exported = out.parent / 'reparse' / 'frozen-parent'
    summaries = freeze_staged_reparse_packets(out, exported)
    assert len(summaries) == 1 and summaries[0]['status'] == 'ready'
    promoted = summaries[0]
    assert promoted['stage_id'] == proposal['stage_id']
    assert promoted['packet_id'] != proposal['packet_id'] and promoted['parent_run_id'] == 'frozen-parent'
    assert promoted['proof'] == proposal['proof']
    parent = load_parent_context(out)
    loaded = load_reparse_packet(resolve_reparse_packet(exported, promoted['packet_id']),
        source_resources=parent['source_resources'], baseline_facts=parent['baseline_facts'],
        company=parent['company'], as_of_date=parent['as_of_date'],
        expected_source_generation=parent['source_generation'])
    assert any(fact.account == 'REVENUE' and fact.value == Decimal('100.00') for fact in loaded)
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert freeze_staged_reparse_packets(out, exported) == summaries
    audit = exported / 'audit' / promoted['packet_id'] / 'audit.jsonl'
    assert 'staged_reparse_frozen' in {json.loads(line)['type'] for line in io_path(audit).read_text(encoding='utf-8').splitlines()}


def test_staging_cannot_publish_into_or_modify_the_frozen_parent(source):
    out, _ = freeze_parent(source)
    with pytest.raises(ValueError, match='outside the parent task'):
        freeze_staged_reparse_packets(out, out / 'ready')
    assert not (out / 'ready').exists()


@pytest.mark.parametrize('tamper', ['staged_packet', 'extra_packet', 'source_pdf'])
def test_freeze_rejects_changed_or_unregistered_staging_and_materials(source, tamper):
    out, proposal = freeze_parent(source)
    packet = resolve_reparse_packet(out / 'staged-reparse', proposal['packet_id'])
    if tamper == 'staged_packet':
        io_path(packet).write_text('{}', encoding='utf-8')
    elif tamper == 'extra_packet':
        extra = out / 'staged-reparse' / ('rp-' + 'f'*64 + '.json')
        io_path(extra).write_bytes(io_path(packet).read_bytes())
    else:
        Path(source['source_resources'][0]['path']).write_bytes(b'changed source')
    exported = out.parent / 'rejected-export'
    with pytest.raises(ValueError, match='hash changed|not part of the frozen parent outputs'):
        freeze_staged_reparse_packets(out, exported)
    assert not exported.exists()


def test_reforged_staged_values_fail_original_page_reconstruction(source):
    out, proposal = freeze_parent(source)
    packet = resolve_reparse_packet(out / 'staged-reparse', proposal['packet_id'])
    data = json.loads(io_path(packet).read_text(encoding='utf-8'))
    data['new_facts'][0]['value'] = '999999'
    from mingjing.audit import digest
    data['packet_sha256'] = digest({key: value for key, value in data.items() if key not in {'packet_id', 'packet_sha256'}})
    data['packet_id'] = 'rp-' + data['packet_sha256']
    forged = packet.with_name(data['packet_id'] + '.json')
    io_path(forged).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    io_path(packet).unlink()
    # A locally rewritten manifest is still insufficient to invent PDF truth.
    from mingjing.evidence import sha256_file
    manifest_path = out / 'run_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    row = next(row for row in manifest['outputs'] if row['path'] == packet.relative_to(out).as_posix())
    row.update(path=forged.relative_to(out).as_posix(), sha256=sha256_file(forged), bytes=forged.stat().st_size)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError, match='source-page reconstruction'):
        freeze_staged_reparse_packets(out, out.parent / 'rejected-forged')
