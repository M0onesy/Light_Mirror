import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mingjing.relocation import RelocationError, RelocationMap, SCHEMA
from mingjing.relocation_replay import mapped_path_type
from mingjing.relocation_replay import replay_relocated
from mingjing.relocation_replay import _write_permitted
from test_replay import frozen_run


def environment(tmp_path):
    workspace = tmp_path/'workspace'
    source, target = workspace/'old'/'records', workspace/'Light_Mirror'/'evidence'/'records'
    target.mkdir(parents=True)
    (target/'document.txt').write_text('frozen financial source', encoding='utf8')
    payload = {'schema': SCHEMA, 'workspace_abs': str(workspace),
               'entries': [{'old': str(source), 'new': target.relative_to(workspace).as_posix(), 'kind': 'directory'}]}
    return workspace, source, target, payload


def test_moved_locked_file_keeps_logical_identity_and_reads_original_bytes(tmp_path):
    _, source, target, payload = environment(tmp_path)
    mapping = RelocationMap(payload)
    expected = hashlib.sha256((target/'document.txt').read_bytes()).hexdigest()
    assert mapping.resolve(source/'document.txt', must_exist=True, sha256=expected) == target/'document.txt'
    PathType = mapped_path_type(mapping)
    logical = PathType(target/'document.txt')
    assert str(logical) == str(source/'document.txt')
    assert str(logical.resolve(strict=True)) == str(source/'document.txt')
    assert logical.read_text() == (target/'document.txt').read_text()
    assert list(PathType(source).glob('*.txt')) == [logical]


def test_longest_prefix_selects_specific_relocation(tmp_path):
    workspace, source, target, payload = environment(tmp_path)
    special = workspace/'Light_Mirror'/'evidence'/'specific'
    special.mkdir()
    (special/'original.txt').write_text('specific')
    payload['entries'].append({'old': str(source/'document.txt'), 'new': 'Light_Mirror/evidence/specific/original.txt', 'kind': 'file'})
    mapping = RelocationMap(payload)
    assert mapping.resolve(source/'document.txt') == special/'original.txt'


@pytest.mark.parametrize('change', ['wrong_schema', 'relative_old', 'outside_old', 'parent_target',
                                   'outside_target', 'missing_target', 'duplicate_old', 'duplicate_target', 'wrong_kind'])
def test_unsafe_or_ambiguous_map_is_rejected(tmp_path, change):
    workspace, source, target, payload = environment(tmp_path)
    row = payload['entries'][0]
    if change == 'wrong_schema': payload['schema'] = 'other'
    elif change == 'relative_old': row['old'] = 'old/records'
    elif change == 'outside_old': row['old'] = str(tmp_path/'outside')
    elif change == 'parent_target': row['new'] = 'Light_Mirror/../outside'
    elif change == 'outside_target': row['new'] = 'outside'
    elif change == 'missing_target': row['new'] = 'Light_Mirror/missing'
    elif change == 'duplicate_old': payload['entries'].append(dict(row))
    elif change == 'duplicate_target': payload['entries'].append(dict(row, old=str(workspace/'different')))
    elif change == 'wrong_kind': row['kind'] = 'file'
    with pytest.raises(RelocationError): RelocationMap(payload)


def test_conflicting_original_is_not_silently_overridden(tmp_path):
    _, source, target, payload = environment(tmp_path)
    mapping = RelocationMap(payload)
    source.mkdir(parents=True)
    (source/'document.txt').write_text('edited conflicting source')
    with pytest.raises(RelocationError, match='different bytes'):
        mapping.resolve(source/'document.txt')


def test_frozen_hash_mismatch_is_rejected(tmp_path):
    _, source, _, payload = environment(tmp_path)
    mapping = RelocationMap(payload)
    with pytest.raises(RelocationError, match='hash differs'):
        mapping.resolve(source/'document.txt', sha256='0'*64)


def test_duplicate_json_keys_are_rejected(tmp_path):
    _, _, target, payload = environment(tmp_path)
    location = target/'map.json'
    location.write_text('{"schema":"x","schema":"y"}')
    with pytest.raises(RelocationError, match='Duplicate'):
        RelocationMap.load(location)


def test_map_read_does_not_edit_frozen_source(tmp_path):
    _, source, target, payload = environment(tmp_path)
    raw = (target/'document.txt').read_bytes()
    mapping = RelocationMap(payload)
    assert mapped_path_type(mapping)(source/'document.txt').read_bytes() == raw
    assert (target/'document.txt').read_bytes() == raw
    assert not source.exists()


def test_existing_directory_with_matching_names_and_different_bytes_is_rejected(tmp_path):
    _, source, _, payload = environment(tmp_path)
    source.mkdir(parents=True)
    (source/'document.txt').write_text('different bytes')
    with pytest.raises(RelocationError, match='different contents'):
        RelocationMap(payload).resolve(source)


@pytest.mark.parametrize('existing', [False, True])
def test_output_escape_or_previous_receipt_is_rejected_before_worker(tmp_path, existing):
    output = tmp_path/'previous' if existing else tmp_path/'evidence'/'relocation'/'..'/'..'/'outside'
    if existing: output.mkdir()
    with pytest.raises(RelocationError, match='new canonical'):
        replay_relocated(mode='kernel', run='unused', relocation_map='unused', out=output)


def test_audit_write_cannot_escape_output_with_parent_components(tmp_path):
    allowed = tmp_path/'output'
    allowed.mkdir()
    assert _write_permitted(allowed/'new.json', [allowed])
    assert not _write_permitted(allowed/'..'/'outside.json', [allowed])
    assert not _write_permitted(tmp_path/'outside.json', [allowed])


def test_kernel_replay_never_requires_a_harness_carrier(frozen_run, tmp_path):
    run, draft = frozen_run
    before = {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in tmp_path.rglob('*') if p.is_file()}
    workspace = tmp_path.parent
    target = workspace/'Light_Mirror'/'evidence'/'kernel-fixtures'/tmp_path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_path), target)
    location = workspace/'Light_Mirror'/'evidence'/'relocation'/('map-'+tmp_path.name+'.json')
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(json.dumps({'schema': SCHEMA, 'workspace_abs': str(workspace), 'entries': [
        {'old': str(tmp_path), 'new': target.relative_to(workspace).as_posix(), 'kind': 'directory'}
    ]}), encoding='utf8')
    out = location.parent/'tests'/tmp_path.name
    command = ('import sys; from mingjing import harness_client; '
               'from mingjing.relocation_replay import run_worker; '
               'harness_client._finance_carrier=lambda *args: (_ for _ in ()).throw(AssertionError("Carrier accessed")); '
               'r=run_worker(mode="kernel",run=sys.argv[1],relocation_map=sys.argv[2],out=sys.argv[3]); '
               'assert r["status"] == "passed", r')
    child = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', command,
                            str(target/'run'), str(location), str(out)],
                           capture_output=True, text=True, encoding='utf8', timeout=60)
    assert child.returncode == 0, child.stderr
    assert before == {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in target.rglob('*') if p.is_file()}


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction safety test')
def test_windows_junction_relocation_is_rejected(tmp_path):
    workspace, _, target, payload = environment(tmp_path)
    link = workspace/'Light_Mirror'/'linked'
    child = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
    assert child.returncode == 0, child.stderr
    try:
        payload['entries'][0]['new'] = link.relative_to(workspace).as_posix()
        with pytest.raises(RelocationError, match='junctions'):
            RelocationMap(payload)
    finally:
        os.rmdir(link)
