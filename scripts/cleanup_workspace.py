"""Safely remove unreferenced build artifacts; never prune materials or runs.

The default is an inventory. Apply re-discovers dependencies and process owners
immediately before each deletion and retains per-file hashes in the receipt.
Windows traversal uses extended-length paths and never follows reparse points.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
REPARSE_POINT = 0x400
LOCK_NAMES = {
    'run_manifest.json', 'agent_lock.json', 'resource_lock.json', 'source_lock.json',
    'runtime_lock.json', 'message_lock.json', 'session_lock.json', 'checkpoint.json',
    'source_snapshot.json', 'code_lock.json', 'knowledge_lock.json',
}
SCAN_EXCLUDES = {'node_modules', '.git', 'runs', 'runtime', 'vendor', 'baseline',
                 'evidence', 'bench', 'frontend', 'web', 'samples'}


def native_path(path: Path) -> str:
    """Permit long Windows paths without putting native prefixes in receipts."""
    value = str(path.absolute())
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return value


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(native_path(path), 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & REPARSE_POINT)


def tree(root: Path, *, prune: set[str] | None = None):
    """Yield lexical paths, rejecting unreadable or linked subtrees."""
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(native_path(directory)) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            path = directory / entry.name
            info = entry.stat(follow_symlinks=False)
            if is_link(info):
                # Never inspect or follow an unknown target. Its directory must
                # not become a removal candidate.
                if prune is None:
                    raise ValueError(f'Linked tree member: {path}')
                continue
            if prune and entry.is_dir(follow_symlinks=False) and entry.name in prune:
                continue
            yield path
            if entry.is_dir(follow_symlinks=False):
                stack.append(path)


def plain_path(path: Path, workspace: Path) -> Path:
    raw = Path(os.path.abspath(path))
    if not raw.is_relative_to(workspace):
        raise ValueError('Deletion candidate is outside the authorized workspace')
    for part in (raw, *raw.parents):
        if part == workspace.parent:
            break
        if is_link(os.lstat(native_path(part))):
            raise ValueError('Linked cleanup targets are forbidden')
    resolved = raw.resolve(strict=True)
    if not resolved.is_relative_to(workspace):
        raise ValueError('Resolved cleanup target escaped the workspace')
    return resolved


def overlaps(target: Path, references: set[Path]) -> bool:
    if target in references or any(parent in references for parent in target.parents):
        return True
    prefix = os.path.normcase(str(target)) + os.sep
    return any(os.path.normcase(str(ref)).startswith(prefix) for ref in references)


def compact_paths(references: set[Path]) -> set[Path]:
    """Collapse children of whole protected trees before any overlap checks."""
    return {ref for ref in references if not any(parent in references for parent in ref.parents)}


def process_paths(workspace: Path) -> set[Path]:
    if os.name != 'nt':
        return set()
    probe = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
        '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); '
        '@(Get-CimInstance Win32_Process | Select-Object ExecutablePath,CommandLine) | ConvertTo-Json -Compress'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    if probe.returncode:
        raise RuntimeError('Could not verify active process ownership')
    try:
        rows = json.loads(probe.stdout or '[]')
    except ValueError as exc:
        raise RuntimeError('Could not parse active process ownership') from exc
    if isinstance(rows, dict):
        rows = [rows]
    protected = set()
    for row in rows:
        if row.get('ExecutablePath'):
            protected.add(Path(row['ExecutablePath']).absolute())
        # Extract path tokens only. Do not save command lines, which may contain
        # credentials. A script/resource opened by python/node remains protected.
        for token in re.findall(r'"([^"]+)"|(?<!\S)([A-Za-z]:\\[^\s]+)', row.get('CommandLine') or ''):
            value = token[0] or token[1]
            try:
                path = Path(value).absolute()
                if path.is_relative_to(workspace) and path.exists():
                    protected.add(path)
            except (OSError, ValueError):
                continue
    return protected


def git_roots(project: Path) -> set[Path]:
    result = set()
    marker = project / '.git'
    if marker.is_dir():
        result.add(marker.resolve())
    elif marker.is_file():
        result.add(marker.absolute())
        text = marker.read_text(encoding='utf-8').strip()
        if not text.startswith('gitdir: '):
            raise ValueError('Malformed Git worktree pointer')
        directory = Path(text[8:])
        if not directory.is_absolute():
            directory = marker.parent / directory
        directory = directory.resolve(strict=True)
        result.add(directory)
        common = directory / 'commondir'
        if common.is_file():
            value = Path(common.read_text(encoding='utf-8').strip())
            result.add((value if value.is_absolute() else directory / value).resolve(strict=True))
    return result


def registered_paths(value, base: Path, workspace: Path, protected_trees: tuple[str, ...] = ()):
    """Protect concrete paths in frozen locks, including nested session locks."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key == 'path' or key.endswith('_path')):
                raw = item if os.path.isabs(item) else os.path.join(str(base), item)
                normalized = os.path.normcase(os.path.abspath(raw))
                if any(normalized == root or normalized.startswith(root + os.sep) for root in protected_trees):
                    continue
                candidate = Path(os.path.abspath(raw))
                if candidate.is_relative_to(workspace):
                    yield candidate
            else:
                yield from registered_paths(item, base, workspace, protected_trees)
    elif isinstance(value, list):
        for item in value:
            yield from registered_paths(item, base, workspace, protected_trees)


def dependencies(workspace: Path) -> tuple[set[Path], list[dict]]:
    main = workspace / 'mainProj'
    projects = (main / 'mingjing', main / 'mingjing-next')
    protected = {main / '年报和半年报', main / '存放会计准则', main / 'agent架构参考',
                 main / 'runs', main / '项目书前期探索计划', workspace / '报名材料', workspace / '论文参考'}
    frozen = []
    for project in projects:
        if project.is_dir():
            protected.update(git_roots(project))
            for name in ('runs', 'runtime', 'vendor', 'baseline', 'evidence', 'samples', 'node_modules'):
                protected.add(project / name)
    protected_trees = tuple(os.path.normcase(str(path.absolute())) for path in protected)
    for project in projects:
        if not project.is_dir():
            continue
        # Whole run trees deliberately protect parent/child tasks, supplement
        # bundles, messages and old incompatible locks without rewriting them.
        roots = (project / 'runs', project / 'runtime')
        for root in roots:
            if not root.is_dir():
                continue
            try:
                paths = tree(root, prune={'node_modules', 'source-bytes', 'cache', '.git'})
                for path in paths:
                    if path.name not in LOCK_NAMES or not path.is_file():
                        continue
                    row = {'path': str(path.absolute()), 'sha256': file_hash(path)}
                    frozen.append(row)
                    try:
                        data = json.loads(path.read_text(encoding='utf-8'))
                        protected.update(registered_paths(data, path.parent, workspace, protected_trees))
                    except (OSError, ValueError, TypeError):
                        protected.add(path.parent.absolute())
            except (OSError, ValueError):
                protected.add(root)
        for name in ('mingjing', 'kernel', 'scripts', 'tests', 'docs', 'rulepack', 'bench', 'frontend', 'web'):
            root = project / name
            if not root.is_dir():
                continue
            # Source files are protected individually so rebuildable pycache
            # children can still be removed.
            try:
                protected.update(path.absolute() for path in tree(root, prune={'__pycache__', '.pytest_cache', 'node_modules'}) if path.is_file())
            except (OSError, ValueError):
                protected.add(root)
    for name in ('明镜MingJing_项目计划书.docx', '明镜_计划书打磨与开发任务手册.md'):
        path = main / name
        protected.add(path)
        if path.is_file():
            frozen.append({'path': str(path), 'sha256': file_hash(path)})
    protected.update(process_paths(workspace))
    return compact_paths({path.absolute() for path in protected}), frozen


def candidates(workspace: Path) -> list[tuple[Path, str]]:
    main = workspace / 'mainProj'
    old = main / 'mingjing'
    result = []
    for pattern in ('MingJing-1.0.0rc1-source-runtime*.zip', 'MingJing-1.0.0rc1-Windows-x64*.zip',
                    'mingjing-20261006-*.zip', 'MingJing-delivery-20261006-*.zip'):
        result.extend((path, 'obsolete_or_duplicate_delivery_archive') for path in main.glob(pattern))
    for name in ('build', 'build-acceptance', 'build-test', 'dist', 'dist-acceptance', 'dist-test'):
        path = old / name
        if path.is_dir():
            result.append((path, 'rebuildable_historical_build_output'))
    for path in (workspace / '.pytest_cache', main / '.pytest_cache'):
        if path.is_dir():
            result.append((path, 'rebuildable_test_cache'))
    for project in (old, main / 'mingjing-next'):
        if not project.is_dir():
            continue
        for name in ('temp-annual.txt', 'temp-prospectus.txt'):
            path = project / name
            if path.is_file():
                result.append((path, 'unreferenced_local_pdf_text_extraction'))
        for path in tree(project, prune=SCAN_EXCLUDES):
            if path.name in ('__pycache__', '.pytest_cache') and path.is_dir():
                result.append((path, 'rebuildable_python_or_test_cache'))
    seen = set()
    deduplicated = []
    for path, reason in sorted(result, key=lambda row: (len(row[0].parts), str(row[0]))):
        if any(path.is_relative_to(parent) for parent in seen):
            continue
        seen.add(path)
        deduplicated.append((path, reason))
    return deduplicated


def inventory(target: Path, workspace: Path) -> list[dict]:
    paths = [target] if target.is_file() else list(tree(target))
    for path in paths:
        plain_path(path, workspace)
    return [{'path': str(path), 'bytes': os.stat(native_path(path)).st_size, 'sha256': file_hash(path),
             'file_id': [os.stat(native_path(path)).st_dev, os.stat(native_path(path)).st_ino],
             'links': os.stat(native_path(path)).st_nlink}
            for path in sorted(paths) if path.is_file()]


def archive_evidence(target: Path) -> list[dict]:
    """Old archives need an existing retained delivery/hash receipt."""
    digest = file_hash(target)
    records = []
    for path in sorted(target.parent.glob('MingJing*.json')) + sorted(target.parent.glob('MingJing*.sha256')):
        if digest in path.read_text(encoding='utf-8'):
            records.append({'path': str(path), 'sha256': file_hash(path)})
    return records


def write_receipt(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=ROOT.parents[1])
    parser.add_argument('--out', type=Path, default=ROOT / 'runs' / 'workspace-cleanup-stage')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    protected, frozen = dependencies(workspace)
    receipt = {'schema': 'mingjing.cleanup.v2', 'workspace': str(workspace), 'started_at': time.time(),
               'apply': args.apply, 'status': 'inventoried', 'entries': [], 'protected_manifests': frozen,
               'protected_paths': sorted(str(path) for path in protected),
               'preserved_run_trees': [str(workspace / 'mainProj' / project / 'runs') for project in ('mingjing', 'mingjing-next')],
               'size_basis': 'logical_file_bytes; physical_reclaim_excludes_observed_hardlinked_files'}
    out = args.out.resolve() / 'cleanup_receipt.json'
    for path, reason in candidates(workspace):
        entry = {'path': str(path.absolute()), 'reason': reason, 'decision': 'keep', 'files': []}
        receipt['entries'].append(entry)
        try:
            target = plain_path(path, workspace)
            if overlaps(target, protected):
                entry['reason'] = 'protected_by_frozen_dependency_source_or_running_process'
                continue
            if target.suffix.lower() == '.zip':
                entry['archive_evidence'] = archive_evidence(target)
                if not entry['archive_evidence']:
                    entry['reason'] = 'archive_has_no_retained_hash_receipt'
                    continue
            entry['files'] = inventory(target, workspace)
            entry['bytes'] = sum(item['bytes'] for item in entry['files'])
            entry['decision'] = 'delete'
        except (OSError, ValueError) as exc:
            entry['reason'] = 'inspection_failed_' + type(exc).__name__
    receipt['planned_bytes'] = sum(e.get('bytes', 0) for e in receipt['entries'] if e['decision'] == 'delete')
    write_receipt(out, receipt)
    removed = 0
    reclaimable = 0
    if args.apply:
        for entry in receipt['entries']:
            if entry['decision'] != 'delete':
                continue
            try:
                target = plain_path(Path(entry['path']), workspace)
                current_protected, _ = dependencies(workspace)
                if overlaps(target, current_protected):
                    entry['decision'] = 'keep_new_dependency_or_running_process'
                    continue
                if inventory(target, workspace) != entry['files']:
                    entry['decision'] = 'keep_changed'
                    continue
                for record in entry.get('archive_evidence', []):
                    if file_hash(Path(record['path'])) != record['sha256']:
                        raise ValueError('Archive receipt changed before cleanup')
                if target.is_dir():
                    shutil.rmtree(native_path(target))
                else:
                    os.unlink(native_path(target))
                removed += entry['bytes']
                reclaimable += sum(item['bytes'] for item in entry['files'] if item['links'] == 1)
                entry['decision'] = 'deleted'
            except (OSError, ValueError) as exc:
                entry['decision'] = 'keep_delete_failed_' + type(exc).__name__
            write_receipt(out, receipt)
        for manifest in frozen:
            if file_hash(Path(manifest['path'])) != manifest['sha256']:
                raise RuntimeError('Protected frozen file changed during cleanup')
        receipt['protected_hash_verification'] = 'passed'
        receipt['status'] = 'completed'
    receipt['removed_bytes'] = removed
    receipt['estimated_unlinked_bytes'] = reclaimable
    receipt['finished_at'] = time.time()
    write_receipt(out, receipt)
    print(json.dumps({'status': receipt['status'], 'planned_bytes': receipt['planned_bytes'],
                      'removed_bytes': removed, 'estimated_unlinked_bytes': reclaimable, 'receipt': str(out)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
