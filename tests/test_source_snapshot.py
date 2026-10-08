"""Source bytes survive future edits; unsafe/incomplete snapshots never pass."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts import source_snapshot as snapshot


def write(root, raw, data=b'fixture\r\n\x00\xff'):
    path = root/raw
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def record(root, raw):
    path = root/raw
    return {'path': raw, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def fixture(root):
    for raw in snapshot.EXTRA_CLOSURE:
        if raw != 'web/harness/build_receipt.json':
            write(root, raw, (raw + '\r\n').encode())
    write(root, 'mingjing/example.py', b'# UTF-8 \xe4\xb8\xad\xe6\x96\x87\r\nvalue = 1\r\n')
    write(root, 'tests/example.json', b'{"fixture": "only"}\r\n')
    write(root, 'frontend/vendor/harness/ui.tsx', b'export const fixture = "upstream";\r\n')
    write(root, 'frontend/vendor/harness/LICENSE', b'upstream-license-original-bytes\r\n')
    write(root, 'frontend/src/App.tsx', b'export const app = "finance";\r\n')
    write(root, 'web/harness/index.html', b'<html>fixture</html>\r\n')
    write(root, 'web/harness/assets/a.js', b'asset-bytes\n')
    for raw in snapshot.RUNTIME_REFERENCES:
        write(root, raw, b'{"runtime": "protected hash reference"}\n')
    inputs = [record(root, raw) for raw in sorted([
        'frontend/package.json', 'frontend/package-lock.json', 'frontend/tsconfig.json',
        'frontend/vite.config.ts', 'frontend/vendor/harness/ui.tsx', 'frontend/vendor/harness/LICENSE', 'frontend/src/App.tsx'])]
    outputs = [record(root, raw) for raw in sorted(['web/harness/index.html', 'web/harness/assets/a.js'])]
    write(root, 'web/harness/build_receipt.json', json.dumps({
        'kind': 'mingjing-harness-ui-build.v1', 'status': 'built', 'inputs': inputs, 'outputs': outputs,
        'source_sha256': hashlib.sha256(snapshot._canonical(inputs)).hexdigest(),
        'assets_sha256': hashlib.sha256(snapshot._canonical(outputs)).hexdigest()}).encode())
    paths = ['mingjing/example.py', 'tests/example.json', *snapshot.RUNTIME_REFERENCES,
             'web/harness/build_receipt.json']
    return {raw: record(root, raw)['sha256'] for raw in paths}


def test_original_bytes_and_build_closure_are_preserved_without_materials_or_native_runtime(tmp_path):
    lock = fixture(tmp_path)
    write(tmp_path, 'runs/old/results.json', b'private-report')
    write(tmp_path, 'corpus/original.pdf', b'private-material')
    write(tmp_path, 'frontend/node_modules/huge.js', b'not-copied')
    write(tmp_path, 'runtime/finance-carrier/staged/node.exe', b'not-copied')
    out = tmp_path/'runs/source-bytes-new'
    result = snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    assert result['status'] == 'passed_source_bytes' and result['references_verified']
    manifest = json.loads((out/snapshot.MANIFEST).read_bytes())
    assert manifest['source_lock'] == lock
    assert manifest['runtime_resources_copied'] is False and manifest['historical_bytes_reconstructed'] is False
    for row in manifest['files']:
        assert (out/'source'/row['path']).read_bytes() == (tmp_path/row['path']).read_bytes()
    assert (out/'source/frontend/vendor/harness/ui.tsx').is_file()
    assert (out/'source/scripts/build_exe.ps1').read_bytes() == (tmp_path/'scripts/build_exe.ps1').read_bytes()
    assert not (out/'source/runtime/finance-carrier/staged/node.exe').exists()
    assert not (out/'source/web/harness/index.html').exists()
    assert not (out/'source/runs').exists() and not (out/'source/corpus').exists()
    (tmp_path/'mingjing/example.py').write_bytes(b'new-generation\r\n')
    assert snapshot.verify_snapshot(out)['status'] == 'passed_source_bytes'
    with pytest.raises(ValueError, match='hash or size changed'):
        snapshot.verify_snapshot(out, source_root=tmp_path)


def test_only_fixed_bundled_demo_corpus_is_preserved(tmp_path):
    lock = fixture(tmp_path)
    for raw in sorted(snapshot.DEMO_CORPUS_FILES):
        write(tmp_path, raw, (raw + '\r\noriginal demo bytes').encode())
        lock[raw] = record(tmp_path, raw)['sha256']
    out = tmp_path/'runs/demo-source-bytes'
    snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    for raw in snapshot.DEMO_CORPUS_FILES:
        assert (out/'source'/raw).read_bytes() == (tmp_path/raw).read_bytes()
    assert snapshot.verify_snapshot(out, source_root=tmp_path)['status'] == 'passed_source_bytes'


def test_complete_real_development_source_lock_round_trips_without_filtering(tmp_path_factory):
    """Exercise the actual inventory, not a prospective whitelist intersection."""
    from scripts import development_receipt
    # Deep upstream UI paths require a short temporary root on Windows even
    # when the production project snapshot remains under its path limit.
    tmp_path = tmp_path_factory.mktemp('sl')
    actual_root = development_receipt.ROOT
    lock = development_receipt.source_lock()
    assert snapshot.DEMO_CORPUS_FILES <= lock.keys()
    build = json.loads((actual_root/'web/harness/build_receipt.json').read_bytes())
    paths = set(lock) | set(snapshot.EXTRA_CLOSURE) | set(snapshot.RUNTIME_REFERENCES)
    paths.update(row['path'] for field in ('inputs', 'outputs') for row in build[field])
    # Keep every nominated path. Omitting unsupported paths here would mask
    # exactly the real-lock integration failure this regression protects.
    for raw in sorted(paths):
        write(tmp_path, raw, (actual_root/raw).read_bytes())
    out = tmp_path/'runs/real-lock-source-bytes'
    result = snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    manifest = json.loads((out/snapshot.MANIFEST).read_bytes())
    assert manifest['source_lock'] == lock
    assert {row['path'] for row in manifest['files']} >= set(lock) - snapshot.RUNTIME_REFERENCES
    assert result['references_verified'] is True
    assert snapshot.verify_snapshot(out, source_root=tmp_path)['status'] == 'passed_source_bytes'


def test_real_root_staging_and_final_paths_fit_windows_limit():
    from scripts import development_receipt
    root = development_receipt.ROOT
    build = json.loads((root/'web/harness/build_receipt.json').read_bytes())
    paths = set(development_receipt.source_lock()) | set(snapshot.EXTRA_CLOSURE)
    paths.update(row['path'] for row in build['inputs'])
    out = root/'runs/source-bytes-stable-11'
    staging = snapshot._staging_path(out)
    assert staging.parent == out.parent and staging.name.startswith('.source-incomplete-')
    assert out.name not in staging.name
    # Test the real product root and complete input closure, not just the
    # shortened test fixture. The terminating NUL occupies the 260th slot.
    for base in (staging, out):
        assert max(len(str(base/'source'/raw)) for raw in paths) <= 259


@pytest.mark.parametrize('raw', [
    '../outside.py', 'mingjing/../example.py', '/outside.py', 'C:/outside.py',
    'mingjing\\example.py', 'mingjing//example.py', 'runs/old/results.json',
    'frontend/node_modules/secret.json', 'frontend/.env', 'bench/v1/D3/data.json',
    'runtime/finance-carrier/staged/node.exe', 'credentials/token.json', 'corpus/report.pdf',
    'samples/corpus/private.pdf', 'samples/corpus/credentials/token.json',
    'samples/other/corpus/demo_annual.pdf', 'samples/corpus/logs/debug.json',
    'samples/corpus/links/facts.json', 'samples/corpus/D3/facts.json',
    'samples/runs/results.json', 'samples/links/example.json',
    'samples/corpus/secrets.json', 'samples/corpus/credentials.json', 'samples/corpus/.env',
    'samples/secrets.json', 'samples/credentials.json',
    'scripts/unapproved-build.ps1',
])
def test_forbidden_lock_path_never_copies_or_publishes(tmp_path, raw):
    lock = fixture(tmp_path)
    lock[raw] = 'a' * 64
    out = tmp_path/'runs/rejected'
    with pytest.raises(ValueError):
        snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    assert not out.exists()
    assert not list(tmp_path.rglob(snapshot.MANIFEST))


def test_stale_original_lock_cannot_be_upgraded_to_current_bytes(tmp_path):
    lock = fixture(tmp_path)
    (tmp_path/'mingjing/example.py').write_bytes(b'changed')
    with pytest.raises(ValueError, match='Nominated source lock differs'):
        snapshot.create_snapshot(tmp_path/'runs/new', lock=lock, root=tmp_path)
    assert not (tmp_path/'runs/new').exists()


@pytest.mark.parametrize('raw', ['frontend/vendor/harness/ui.tsx', 'web/harness/assets/a.js'])
def test_stale_frontend_source_or_asset_closure_fails_before_publish(tmp_path, raw):
    lock = fixture(tmp_path)
    (tmp_path/raw).write_bytes(b'changed-closure')
    with pytest.raises(ValueError, match='hash or size changed'):
        snapshot.create_snapshot(tmp_path/'runs/new', lock=lock, root=tmp_path)
    assert not (tmp_path/'runs/new').exists()


def test_copy_failure_leaves_incomplete_directory_without_manifest_or_complete_destination(tmp_path, monkeypatch):
    lock = fixture(tmp_path)
    def fail(source, target):
        target.write_bytes(b'partial')
        raise OSError('fixture-copy-failure')
    monkeypatch.setattr(snapshot, '_copy_bytes', fail)
    out = tmp_path/'runs/new'
    with pytest.raises(OSError, match='fixture-copy-failure'):
        snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    assert not out.exists()
    partial = list(out.parent.glob('.source-incomplete-*'))
    assert len(partial) == 1 and not (partial[0]/snapshot.MANIFEST).exists()
    with pytest.raises((ValueError, FileNotFoundError)):
        snapshot.verify_snapshot(partial[0])


def test_source_edit_during_copy_cannot_publish_a_mixed_generation(tmp_path, monkeypatch):
    lock = fixture(tmp_path)
    actual = snapshot._copy_bytes
    def mutate(source, target):
        actual(source, target)
        if source.name == 'example.py':
            source.write_bytes(b'changed-inflight')
    monkeypatch.setattr(snapshot, '_copy_bytes', mutate)
    out = tmp_path/'runs/new'
    with pytest.raises(ValueError, match='hash or size changed'):
        snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    assert not out.exists() and not list(out.parent.rglob(snapshot.MANIFEST))


def test_final_validation_failure_withdraws_staged_complete_marker(tmp_path, monkeypatch):
    lock = fixture(tmp_path)
    def fail(*args, **kwargs):
        raise ValueError('fixture-final-validation-failure')
    monkeypatch.setattr(snapshot, 'verify_snapshot', fail)
    out = tmp_path/'runs/new'
    with pytest.raises(ValueError, match='fixture-final-validation-failure'):
        snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    assert not out.exists() and not list(out.parent.rglob(snapshot.MANIFEST))


def test_inventory_rehash_cannot_erase_an_original_frontend_input(tmp_path):
    lock = fixture(tmp_path)
    out = tmp_path/'runs/new'
    snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    manifest = json.loads((out/snapshot.MANIFEST).read_bytes())
    raw = 'frontend/vendor/harness/ui.tsx'
    entry = next(row for row in manifest['files'] if row['path'] == raw)
    manifest['files'].remove(entry)
    manifest['copied_bytes'] -= entry['bytes']
    manifest['files_sha256'] = hashlib.sha256(snapshot._canonical(manifest['files'])).hexdigest()
    (out/'source'/raw).unlink()
    (out/snapshot.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='frontend build closure'):
        snapshot.verify_snapshot(out)


def test_same_size_copy_corruption_is_detected(tmp_path, monkeypatch):
    lock = fixture(tmp_path)
    def corrupt(source, target):
        data = source.read_bytes()
        target.write_bytes(b'X' * len(data))
    monkeypatch.setattr(snapshot, '_copy_bytes', corrupt)
    out = tmp_path/'runs/new'
    with pytest.raises(ValueError, match='hash or size changed'):
        snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    assert not out.exists() and not list(out.parent.rglob(snapshot.MANIFEST))


@pytest.mark.parametrize('tampering', ['source_bytes', 'extra_file', 'missing_file', 'lock', 'inventory', 'total'])
def test_tampered_snapshot_is_not_complete_evidence(tmp_path, tampering):
    lock = fixture(tmp_path)
    out = tmp_path/'runs/new'
    snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    manifest = json.loads((out/snapshot.MANIFEST).read_bytes())
    if tampering == 'source_bytes':
        (out/'source/mingjing/example.py').write_bytes(b'tampered')
    elif tampering == 'extra_file':
        write(out, 'source/mingjing/secret.json', b'not-in-inventory')
    elif tampering == 'missing_file':
        (out/'source/mingjing/example.py').unlink()
    elif tampering == 'lock':
        manifest['source_lock']['mingjing/example.py'] = 'a' * 64
    elif tampering == 'inventory':
        manifest['files'][0]['sha256'] = 'a' * 64
    else:
        manifest['copied_bytes'] += 1
    (out/snapshot.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises((ValueError, FileNotFoundError)):
        snapshot.verify_snapshot(out)


def test_destination_does_not_overwrite_an_existing_historical_snapshot(tmp_path):
    lock = fixture(tmp_path)
    out = tmp_path/'runs/new'
    snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    old = {path.relative_to(out).as_posix(): path.read_bytes() for path in out.rglob('*') if path.is_file()}
    with pytest.raises(FileExistsError):
        snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    assert old == {path.relative_to(out).as_posix(): path.read_bytes() for path in out.rglob('*') if path.is_file()}


def test_live_protected_runtime_reference_tampering_is_reported_without_copying_runtime(tmp_path):
    lock = fixture(tmp_path)
    out = tmp_path/'runs/new'
    snapshot.create_snapshot(out, lock=lock, root=tmp_path)
    raw = sorted(snapshot.RUNTIME_REFERENCES)[0]
    (tmp_path/raw).write_bytes(b'tampered-native-manifest')
    assert snapshot.verify_snapshot(out)['references_verified'] is False
    with pytest.raises(ValueError, match='hash or size changed'):
        snapshot.verify_snapshot(out, source_root=tmp_path)


def test_linked_source_or_destination_is_refused_without_following_it(tmp_path):
    lock = fixture(tmp_path)
    original = tmp_path/'mingjing/example.py'
    data = original.read_bytes()
    outside = tmp_path.parent/(tmp_path.name + '-outside.py')
    outside.write_bytes(data)
    original.unlink()
    try:
        original.symlink_to(outside)
    except OSError:
        # Windows without symlink privilege: exercise a genuine directory
        # junction instead of calling the link guard a passed skipped test.
        import os
        import subprocess
        if os.name != 'nt':
            raise
        original.write_bytes(data)
        linked = tmp_path/'runs'
        other = tmp_path.parent/(tmp_path.name + '-outside-dir')
        other.mkdir()
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(linked), str(other)], capture_output=True)
        assert result.returncode == 0
        with pytest.raises(ValueError, match='junctions or reparse'):
            snapshot.create_snapshot(linked/'new', lock=lock, root=tmp_path)
        assert list(other.iterdir()) == []
        linked.rmdir()
        other.rmdir()
    else:
        with pytest.raises(ValueError, match='symlinks'):
            snapshot.create_snapshot(tmp_path/'runs/new', lock=lock, root=tmp_path)
        original.unlink()
    assert outside.read_bytes() == data
    outside.unlink()
