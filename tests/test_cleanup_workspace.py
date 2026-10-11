"""Safety checks for cleanup across a shared worktree and frozen task graph."""
from pathlib import Path
import json
import os
import sys

import pytest

from scripts import cleanup_workspace as cleanup


def workspace(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / 'workspace'
    project = root / 'mainProj' / 'mingjing-next'
    project.mkdir(parents=True)
    return root, project


def test_all_historical_run_children_and_external_locked_inputs_are_protected(tmp_path, monkeypatch):
    root, project = workspace(tmp_path)
    run = project / 'runs' / 'cancelled' / 'messages' / 'message-1'
    run.mkdir(parents=True)
    external = root / 'external-build-cache' / 'supplement.pdf'
    external.parent.mkdir()
    external.write_bytes(b'actual supplement')
    lock = run / 'message_lock.json'
    lock.write_text(json.dumps({'parent': {'resource_path': str(external)}}), encoding='utf-8')
    monkeypatch.setattr(cleanup, 'process_paths', lambda _: set())
    protected, frozen = cleanup.dependencies(root)
    assert cleanup.overlaps(run / 'orphan-cache', protected)
    assert cleanup.overlaps(external.parent, protected)
    assert any(row['path'] == str(lock) for row in frozen)


def test_shared_git_common_directory_survives_cleanup(tmp_path):
    root, project = workspace(tmp_path)
    common = root / 'mainProj' / 'mingjing' / '.git'
    worktree = common / 'worktrees' / 'mingjing-next'
    worktree.mkdir(parents=True)
    (project / '.git').write_text('gitdir: ' + str(worktree), encoding='utf-8')
    (worktree / 'commondir').write_text('../..', encoding='utf-8')
    protected = cleanup.git_roots(project)
    assert common.resolve() in protected
    assert cleanup.overlaps(common.parent, protected)


def test_relative_lock_path_is_normalized_before_protection(tmp_path):
    root, project = workspace(tmp_path)
    base = project / 'runs' / 'task'
    target = project / 'build' / 'payload.exe'
    paths = list(cleanup.registered_paths({'path': '../../build/payload.exe'}, base, root))
    assert paths == [target]
    assert cleanup.overlaps(target.parent, set(paths))


def test_candidates_do_not_enter_run_runtime_vendor_or_dependency_trees(tmp_path):
    root, project = workspace(tmp_path)
    for name in ('runs/task', 'runtime/carrier', 'vendor/harness', 'frontend/node_modules'):
        (project / name / '__pycache__').mkdir(parents=True)
    expected = project / 'mingjing' / '__pycache__'
    expected.mkdir(parents=True)
    (expected / 'entry.pyc').write_bytes(b'rebuildable')
    result = cleanup.candidates(root)
    assert result == [(expected, 'rebuildable_python_or_test_cache')]


def test_linked_tree_and_escape_cannot_be_inspected_for_deletion(tmp_path):
    root, project = workspace(tmp_path)
    external = tmp_path / 'external'
    external.mkdir()
    with pytest.raises(ValueError, match='outside'):
        cleanup.plain_path(external, root)
    link = project / 'build'
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip('Creating Windows symlinks requires local privilege')
    with pytest.raises(ValueError, match='Linked'):
        cleanup.plain_path(link, root)


def test_archive_requires_retained_hash_receipt(tmp_path):
    root, _ = workspace(tmp_path)
    archive = root / 'mainProj' / 'MingJing-1.0.0rc1-Windows-x64.zip'
    archive.write_bytes(b'old package')
    assert cleanup.archive_evidence(archive) == []
    receipt = archive.parent / 'MingJing-delivery.json'
    receipt.write_text(json.dumps({'archive_sha256': cleanup.file_hash(archive)}), encoding='utf-8')
    assert cleanup.archive_evidence(archive)[0]['path'] == str(receipt)


def test_apply_rechecks_new_locked_reference_before_removing_cache(tmp_path, monkeypatch):
    root, project = workspace(tmp_path)
    cache = project / 'mingjing' / '__pycache__'
    cache.mkdir(parents=True)
    artifact = cache / 'entry.pyc'
    artifact.write_bytes(b'old cache')
    calls = []

    def discover(_):
        calls.append(True)
        return ({artifact} if len(calls) > 1 else set()), []

    monkeypatch.setattr(cleanup, 'dependencies', discover)
    out = root / 'receipt'
    monkeypatch.setattr(sys, 'argv', ['cleanup', '--workspace', str(root), '--out', str(out), '--apply'])
    assert cleanup.main() == 0
    receipt = json.loads((out / 'cleanup_receipt.json').read_text(encoding='utf-8'))
    assert artifact.exists()
    assert receipt['removed_bytes'] == 0
    assert receipt['entries'][0]['decision'] == 'keep_new_dependency_or_running_process'


def test_apply_removes_only_unprotected_rebuildable_files_and_verifies_hashes(tmp_path, monkeypatch):
    root, project = workspace(tmp_path)
    cache = project / 'mingjing' / '__pycache__'
    cache.mkdir(parents=True)
    (cache / 'entry.pyc').write_bytes(b'cache')
    source = cache.parent / 'entry.py'
    source.write_text('important source', encoding='utf-8')
    run = project / 'runs' / 'failure' / '__pycache__'
    run.mkdir(parents=True)
    evidence = run / 'entry.pyc'
    evidence.write_bytes(b'historical failure')
    monkeypatch.setattr(cleanup, 'process_paths', lambda _: set())
    out = root / 'receipt'
    monkeypatch.setattr(sys, 'argv', ['cleanup', '--workspace', str(root), '--out', str(out), '--apply'])
    assert cleanup.main() == 0
    receipt = json.loads((out / 'cleanup_receipt.json').read_text(encoding='utf-8'))
    assert not cache.exists()
    assert source.read_text(encoding='utf-8') == 'important source'
    assert evidence.exists()
    assert receipt['removed_bytes'] == 5
    assert receipt['protected_hash_verification'] == 'passed'


def test_windows_extended_length_path_conversion(tmp_path):
    native = cleanup.native_path(tmp_path)
    assert native.startswith('\\\\?\\') if os.name == 'nt' else native == str(tmp_path.absolute())


def test_consolidated_data_evidence_artifacts_and_archives_are_never_candidates(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    project = root / 'Light_Mirror'
    for relative in ('data/annual', 'evidence/dev3/failed', 'runtime/carrier',
                     'vendor/harness', 'artifacts/program'):
        (project / relative / '__pycache__').mkdir(parents=True)
    archived = root / '存档/历史版本/old/__pycache__'
    archived.mkdir(parents=True)
    source_cache = project / 'mingjing/__pycache__'
    source_cache.mkdir(parents=True)
    monkeypatch.setattr(cleanup, 'process_paths', lambda _: set())
    protected, _ = cleanup.dependencies(root)
    assert all(cleanup.overlaps(project / name, protected)
               for name in ('data', 'evidence', 'runtime', 'vendor', 'artifacts'))
    assert cleanup.overlaps(archived, protected)
    assert cleanup.candidates(root) == [(source_cache, 'rebuildable_python_or_test_cache')]


def test_consolidated_default_workspace_is_the_immediate_parent(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    project = root / 'Light_Mirror'
    project.mkdir(parents=True)
    observed = []
    monkeypatch.setattr(cleanup, 'ROOT', project)
    monkeypatch.setattr(cleanup, 'dependencies', lambda workspace: (observed.append(workspace) or set(), []))
    monkeypatch.setattr(cleanup, 'candidates', lambda _: [])
    monkeypatch.setattr(sys, 'argv', ['cleanup'])
    assert cleanup.main() == 0
    assert observed == [root]
    assert (project / 'evidence/cleanup-preview/cleanup_receipt.json').is_file()
