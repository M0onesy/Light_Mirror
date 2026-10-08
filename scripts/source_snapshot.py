"""Preserve original source bytes alongside hash locks, without release packaging.

Only nominated development sources and the frontend build input closure are
copied, together with the two bundled demo-corpus files. External reports,
runs, credentials, node_modules and native runtime resources are never included.
Large runtime/UI resources remain protected hash references.
Historical locks cannot recreate bytes they did not save; this tool never
rewrites or upgrades an old lock to pretend otherwise.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'mingjing-source-bytes.v1'
MANIFEST = 'source_snapshot.json'
EXTRA_CLOSURE = (
    'pyproject.toml', 'requirements-build.lock', 'requirements-build.json', 'MingJing.spec',
    'scripts/build_exe.ps1',
    'frontend/package.json', 'frontend/package-lock.json', 'frontend/tsconfig.json',
    'frontend/vite.config.ts', 'web/harness/build_receipt.json',
)
RUNTIME_REFERENCES = frozenset({
    'runtime/harness/manifest.json', 'runtime/finance-carrier/staged/carrier_manifest.json',
})
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024
DEMO_CORPUS_FILES = frozenset({'samples/corpus/demo_annual.pdf', 'samples/corpus/facts.json'})
_DENIED = frozenset({'node_modules', '__pycache__', '.git', '.pytest_cache', '.vite',
                     'secrets', 'credentials', 'corpus', 'reports', 'runs', 'logs', 'tmp', 'links'})


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _unlinked(path: Path) -> None:
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or getattr(path, 'is_junction', lambda: False)()):
        raise ValueError('Source snapshots do not traverse symlinks, junctions or reparse points')


def _absolute_unlinked(path: Path, *, exists=True) -> Path:
    """Validate lexical ancestors before resolving; resolution can hide links."""
    path = Path(os.path.abspath(path))
    for ancestor in [*reversed(path.parents), path]:
        if ancestor.exists() or ancestor.is_symlink():
            _unlinked(ancestor)
        elif exists:
            raise ValueError('Source snapshot path is missing')
    resolved = path.resolve(strict=exists)
    if resolved != path:
        raise ValueError('Source snapshot path is not canonical')
    return path


def _relative(raw: str) -> str:
    if (not isinstance(raw, str) or not raw or '\\' in raw or ':' in raw
            or '\x00' in raw or raw.startswith('/') or raw.endswith('/')):
        raise ValueError('Snapshot file paths must be canonical project-relative paths')
    path = PurePosixPath(raw)
    if path.as_posix() != raw or any(part in {'', '.', '..'} for part in path.parts):
        raise ValueError('Snapshot file paths cannot escape their root')
    # Only these fixed, size-bounded demo resources may traverse a corpus
    # component. A broad samples/corpus prefix exception would allow private
    # materials or newly introduced credentials to enter a source snapshot.
    if any((part.lower() in _DENIED and not (part == 'corpus' and raw in DEMO_CORPUS_FILES))
           or (path.parts[0] == 'samples' and PurePosixPath(part).stem.lower() in {'secrets', 'credentials'})
           or re.search(r'(^|[-_])d3($|[-_])', part, re.I)
           or part.startswith('.env') for part in path.parts):
        raise ValueError('Excluded data, dependencies or credentials cannot enter a source snapshot')
    return raw


def _source_allowed(raw: str) -> bool:
    path = PurePosixPath(raw)
    if raw in EXTRA_CLOSURE or raw == 'app_entry.py':
        return True
    parts = path.parts
    if parts[0] in {'mingjing', 'scripts', 'tests', 'rulepack'}:
        return path.suffix in {'.py', '.json', '.mjs', '.md', '.lock'}
    if parts[:2] == ('bench', 'v1'):
        return len(parts) == 3 and path.suffix in {'.py', '.json', '.mjs', '.md', '.lock'}
    if parts[:2] == ('runtime', 'finance-plugin'):
        return path.suffix in {'.py', '.json', '.mjs', '.md', '.lock'}
    if parts[0] == 'samples':
        return path.suffix in {'.md', '.txt', '.json', '.jsonl', '.pdf', '.csv', '.tsv', '.yaml', '.yml'}
    return parts[0] == 'frontend' and (path.name in {'LICENSE', 'NOTICE'} or path.suffix in {
        '.ts', '.tsx', '.js', '.jsx', '.mjs', '.css', '.html', '.json', '.svg', '.md', '.txt', '.lock'})


def _safe_file(root: Path, raw: str) -> Path:
    raw = _relative(raw)
    path = root.joinpath(*PurePosixPath(raw).parts)
    _absolute_unlinked(path)
    if not path.is_relative_to(root) or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Snapshot input must be a regular file in its approved root')
    return path


def _record(root: Path, raw: str, *, expected=None) -> dict:
    path = _safe_file(root, raw)
    record = {'path': raw, 'bytes': path.stat().st_size, 'sha256': _sha(path)}
    if expected is not None:
        if (not isinstance(expected, dict) or type(expected.get('bytes')) is not int
                or expected['bytes'] < 0 or expected.get('path') != raw
                or record != {key: expected.get(key) for key in record}):
            raise ValueError('Frozen file hash or size changed: ' + raw)
    return record


def _frontend_closure(root: Path) -> tuple[list[dict], list[dict]]:
    path = _safe_file(root, 'web/harness/build_receipt.json')
    receipt = json.loads(path.read_bytes())
    if receipt.get('kind') != 'mingjing-harness-ui-build.v1' or receipt.get('status') != 'built':
        raise ValueError('A successful frontend source-to-assets receipt is required')
    inputs, outputs = [], []
    for key, target, prefix in [('inputs', inputs, 'frontend/'), ('outputs', outputs, 'web/harness/')]:
        rows = receipt.get(key)
        if not isinstance(rows, list) or not rows:
            raise ValueError('Frontend closure inventory is absent')
        seen = set()
        for row in rows:
            raw = _relative(row.get('path', '') if isinstance(row, dict) else '')
            if raw in seen or not raw.startswith(prefix) or raw == 'web/harness/build_receipt.json':
                raise ValueError('Frontend closure contains a duplicate or foreign file')
            if key == 'inputs' and not _source_allowed(raw):
                raise ValueError('Frontend closure input is not a source file')
            seen.add(raw)
            target.append(_record(root, raw, expected=row))
        expected_digest = receipt.get('source_sha256' if key == 'inputs' else 'assets_sha256')
        if hashlib.sha256(_canonical(rows)).hexdigest() != expected_digest:
            raise ValueError('Frontend closure inventory digest changed')
    return inputs, outputs


def _copy_bytes(source: Path, target: Path) -> None:
    """Exclusive byte copy; no decoding/newline conversion or metadata rewrite."""
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def _staging_path(out: Path) -> Path:
    # Keep the deepest upstream UI input within Windows path limits without
    # repeating a potentially long human-readable final snapshot name.
    return out.with_name('.source-incomplete-' + uuid.uuid4().hex)


def create_snapshot(out: Path, *, lock: dict | None = None, root: Path = ROOT) -> dict:
    root = _absolute_unlinked(root)
    if not root.is_dir():
        raise ValueError('Source root must be a directory')
    out = Path(out)
    if not out.is_absolute():
        out = root/out
    out = _absolute_unlinked(out, exists=False)
    if not out.is_relative_to(root) or out == root:
        raise ValueError('Snapshot destination must remain inside the project root')
    if out.exists():
        raise FileExistsError('Source snapshot destination already exists; old snapshots are immutable')
    if lock is None:
        from scripts import development_receipt
        if development_receipt.ROOT != root:
            raise ValueError('Provide the nominated source lock for a nondefault root')
        lock = development_receipt.source_lock()
    if not isinstance(lock, dict) or not lock:
        raise ValueError('A nonempty development source-lock mapping is required')
    source_lock = {}
    copied, references = {}, {}
    for raw, expected_sha in lock.items():
        raw = _relative(raw)
        if not re.fullmatch(r'[0-9a-f]{64}', str(expected_sha)):
            raise ValueError('Source lock contains an invalid SHA-256')
        if raw not in RUNTIME_REFERENCES and not _source_allowed(raw):
            raise ValueError('Source lock requests an excluded or nonsource resource')
        record = _record(root, raw)
        if record['sha256'] != expected_sha:
            raise ValueError('Nominated source lock differs from original bytes: ' + raw)
        source_lock[raw] = expected_sha
        (references if raw in RUNTIME_REFERENCES else copied)[raw] = record
    for raw in EXTRA_CLOSURE:
        copied[raw] = _record(root, raw)
    inputs, assets = _frontend_closure(root)
    for row in inputs:
        copied[row['path']] = row
    for row in assets:
        references[row['path']] = row
    for raw in RUNTIME_REFERENCES:
        references[raw] = _record(root, raw)
    if any(row['bytes'] > MAX_FILE_BYTES for row in copied.values()) or sum(
            row['bytes'] for row in copied.values()) > MAX_SOURCE_BYTES:
        raise ValueError('Source snapshot exceeds the bounded source-only size limit')
    out.parent.mkdir(parents=True, exist_ok=True)
    _absolute_unlinked(out.parent)
    staging = _staging_path(out)
    staging.mkdir()
    # An exception leaves only a clearly incomplete directory, with no
    # published manifest. Never delete the nominated destination on failure.
    files = sorted(copied.values(), key=lambda row: row['path'])
    protected = sorted(references.values(), key=lambda row: row['path'])
    for record in files:
        original = _safe_file(root, record['path'])
        target = staging/'source'/record['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        _absolute_unlinked(target.parent)
        _copy_bytes(original, target)
        _record(staging/'source', record['path'], expected=record)
    # Validate both sides after all copies. Source edits during a snapshot
    # cannot silently mix generations, even if copy sizes stay the same.
    for record in [*files, *protected]:
        _record(root, record['path'], expected=record)
    manifest = {'schema_version': SCHEMA, 'status': 'complete',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_lock': source_lock, 'source_lock_sha256': hashlib.sha256(_canonical(source_lock)).hexdigest(),
        'files': files, 'files_sha256': hashlib.sha256(_canonical(files)).hexdigest(),
        'protected_references': protected,
        'references_sha256': hashlib.sha256(_canonical(protected)).hexdigest(),
        'copied_bytes': sum(row['bytes'] for row in files),
        'runtime_resources_copied': False, 'historical_bytes_reconstructed': False}
    temporary = staging/(MANIFEST + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8') + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, staging/MANIFEST)
        validation = verify_snapshot(staging, source_root=root)
        if out.exists():
            raise FileExistsError('Snapshot destination appeared during creation')
        staging.rename(out)
    except BaseException:
        # A failure during the final validation/publish must not leave a
        # complete marker even in a directory explicitly called incomplete.
        marker = staging/MANIFEST
        if marker.exists():
            _absolute_unlinked(marker)
            marker.unlink()
        raise
    return {**validation, 'snapshot': str(out)}


def verify_snapshot(snapshot: Path, *, source_root: Path | None = None) -> dict:
    snapshot = _absolute_unlinked(snapshot)
    manifest_path = _safe_file(snapshot, MANIFEST)
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get('schema_version') != SCHEMA or manifest.get('status') != 'complete':
        raise ValueError('Snapshot has no complete source-byte manifest')
    if manifest.get('runtime_resources_copied') is not False or manifest.get('historical_bytes_reconstructed') is not False:
        raise ValueError('Snapshot source-only provenance flags changed')
    lock = manifest.get('source_lock')
    if not isinstance(lock, dict) or not lock or hashlib.sha256(_canonical(lock)).hexdigest() != manifest.get('source_lock_sha256'):
        raise ValueError('Snapshot original source lock changed')
    seen, records, total = set(), {}, 0
    for field, digest_field in [('files', 'files_sha256'), ('protected_references', 'references_sha256')]:
        rows = manifest.get(field)
        if not isinstance(rows, list) or not rows or hashlib.sha256(_canonical(rows)).hexdigest() != manifest.get(digest_field):
            raise ValueError('Snapshot inventory changed')
        for row in rows:
            raw = _relative(row.get('path', '') if isinstance(row, dict) else '')
            if raw in seen:
                raise ValueError('Snapshot duplicate paths')
            seen.add(raw)
            if type(row.get('bytes')) is not int or row['bytes'] < 0 or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('sha256'))):
                raise ValueError('Snapshot file identity is invalid')
            if field == 'files':
                if not _source_allowed(raw) or row['bytes'] > MAX_FILE_BYTES:
                    raise ValueError('Snapshot includes an excluded resource')
                _record(snapshot/'source', raw, expected=row)
                records[raw] = row
                total += row['bytes']
            elif raw not in RUNTIME_REFERENCES and not raw.startswith('web/harness/'):
                raise ValueError('Snapshot protected reference is outside runtime/UI closure')
            if source_root is not None:
                _record(_absolute_unlinked(source_root), raw, expected=row)
    if total != manifest.get('copied_bytes') or total > MAX_SOURCE_BYTES:
        raise ValueError('Snapshot byte total changed')
    for raw, expected in lock.items():
        _relative(raw)
        entry = next((row for row in [*manifest['files'], *manifest['protected_references']] if row['path'] == raw), None)
        if entry is None or entry['sha256'] != expected:
            raise ValueError('Snapshot does not cover the nominated source lock')
    if not set(EXTRA_CLOSURE) <= records.keys() or not RUNTIME_REFERENCES <= seen:
        raise ValueError('Snapshot required build closure is incomplete')
    frontend = json.loads(_safe_file(snapshot/'source', 'web/harness/build_receipt.json').read_bytes())
    if frontend.get('kind') != 'mingjing-harness-ui-build.v1' or frontend.get('status') != 'built':
        raise ValueError('Snapshot frontend build provenance is invalid')
    references = {row['path']: row for row in manifest['protected_references']}
    for field, frozen, prefix, digest_key in [
        ('inputs', records, 'frontend/', 'source_sha256'),
        ('outputs', references, 'web/harness/', 'assets_sha256'),
    ]:
        rows = frontend.get(field)
        if not isinstance(rows, list) or not rows or hashlib.sha256(_canonical(rows)).hexdigest() != frontend.get(digest_key):
            raise ValueError('Snapshot frontend closure inventory changed')
        seen_frontend = set()
        for row in rows:
            raw = _relative(row.get('path', '') if isinstance(row, dict) else '')
            if raw in seen_frontend or not raw.startswith(prefix) or frozen.get(raw) != row:
                raise ValueError('Snapshot original frontend build closure is incomplete or changed')
            seen_frontend.add(raw)
    actual = set()
    for directory, dirs, names in os.walk(snapshot, followlinks=False):
        current = Path(directory)
        _unlinked(current)
        for name in dirs:
            _unlinked(current/name)
        for name in names:
            path = current/name
            _unlinked(path)
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError('Snapshot contains a nonregular file')
            actual.add(path.relative_to(snapshot).as_posix())
    if actual != {MANIFEST, *('source/' + raw for raw in records)}:
        raise ValueError('Snapshot contains missing or unrecorded files')
    return {'status': 'passed_source_bytes', 'snapshot': str(snapshot), 'manifest_sha256': _sha(manifest_path),
        'source_lock_sha256': manifest['source_lock_sha256'], 'files': len(records), 'bytes': total,
        'protected_references': len(manifest['protected_references']),
        'references_verified': source_root is not None, 'runtime_resources_copied': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('--out', type=Path, required=True)
    create.add_argument('--lock', type=Path, help='Existing development source-lock JSON; bytes must still match')
    check = sub.add_parser('verify')
    check.add_argument('--snapshot', type=Path, required=True)
    check.add_argument('--source-root', type=Path, help='Optionally verify live protected runtime/UI references')
    args = parser.parse_args()
    if args.command == 'create':
        lock = json.loads(_absolute_unlinked(args.lock).read_bytes()) if args.lock else None
        result = create_snapshot(args.out, lock=lock)
    else:
        result = verify_snapshot(args.snapshot, source_root=args.source_root)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
