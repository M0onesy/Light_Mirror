"""Deploy a locked finance-only Harness carrier without modifying the upstream.

Copies compiled packages already present in the pinned SDK deployment and a
standalone Node executable. This is a functional development runtime, not an
application EXE build or a release acceptance receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
import uuid
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.plan_finance_runtime import create_plan, validate_candidate_inventory


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with _io(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _io(path: Path) -> Path:
    value = str(path.absolute())
    return Path('\\\\?\\' + value) if os.name == 'nt' and not value.startswith('\\\\?\\') else path


def _regular(path: Path) -> None:
    info = _io(path).lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError(f'Carrier resources cannot contain symbolic links or reparse points: {path}')


def copy_package(source: Path, destination: Path, overlay: dict | None = None) -> None:
    _io(destination).mkdir(parents=True, exist_ok=False)
    walk_root = _io(source)
    for current, folders, names in os.walk(walk_root, followlinks=False):
        parent = Path(current)
        _regular(parent)
        folders[:] = [name for name in folders if name not in {'node_modules', '.git', '.cache', '__pycache__'}]
        for name in names:
            path = parent / name
            # Declaration/debug maps are not executed. Original notices remain.
            if name.endswith(('.d.ts', '.d.cts', '.d.mts', '.map', '.tsbuildinfo')):
                continue
            _regular(path)
            target = _io(destination) / path.relative_to(walk_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    if overlay is not None:
        package = destination / 'package.json'
        data = json.loads(package.read_text(encoding='utf-8'))
        data['dependencies'] = overlay['dependencies']
        # The CLI's other profiles' configuration trees are not finance assets.
        if isinstance(data.get('dsh'), dict):
            data['dsh'].pop('configTrees', None)
        package.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _nearest_package(owner: Path, name: str, root: Path) -> Path | None:
    current = owner
    while current.is_relative_to(root):
        candidate = current / 'node_modules' / name
        if (candidate / 'package.json').is_file():
            return candidate
        if current == root:
            break
        current = current.parent
    return None


def stage_runtime(upstream: Path, destination: Path, node: Path, *, node_license: Path | None = None) -> dict:
    upstream, destination, node = upstream.resolve(), destination.absolute(), node.resolve(strict=True)
    if destination.exists():
        raise ValueError('The staged destination already exists; choose a new controlled directory')
    if destination.resolve().is_relative_to(upstream):
        raise ValueError('Runtime staging must not modify the pinned upstream checkout')
    plan = create_plan(upstream)
    if plan['candidate']['unresolved']:
        raise ValueError('The proposed finance dependency graph has unresolved packages')
    packages = plan['candidate']['packages']
    deployed = upstream / 'python/sdk-runtime/src/deepseek_harness_runtime/runtime/node/node_modules'
    key_for = lambda item: f"{item['name']}@{item['version']}:{(upstream / item['manifest']).resolve().relative_to(upstream).as_posix()}"
    records = {key_for(item): item for item in packages}
    dependencies = {key: {} for key in records}
    for edge in plan['candidate']['edges']:
        existing = dependencies[edge['from']].get(edge['requested_package'])
        if existing is not None and existing != edge['to']:
            raise ValueError('A package dependency maps to two incompatible deployed versions')
        dependencies[edge['from']][edge['requested_package']] = edge['to']
    source_paths = {}
    for key, item in records.items():
        source = (deployed / item['name'] if item['workspace'] else (upstream / item['manifest']).parent).resolve(strict=True)
        # The pinned checkout uses pnpm junctions for source manifests. Read
        # their approved real directory and materialize only ordinary files;
        # the independently deployed carrier must not retain any junction.
        if not source.is_relative_to(upstream):
            raise ValueError('A compiled package escaped the pinned checkout')
        data = json.loads((source / 'package.json').read_text(encoding='utf-8'))
        if data.get('name') != item['name'] or data.get('version') != item['version']:
            raise ValueError('Compiled SDK package differs from the proposed package identity')
        source_paths[key] = source
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.with_name(destination.name + '.stage-' + uuid.uuid4().hex[:12])
    stage.mkdir()
    primary = {}
    for key, item in records.items():
        if item['name'] not in primary or item['root']:
            primary[item['name']] = key
    installed, queue = {}, deque()
    def install(key: str, target: Path) -> None:
        item = records[key]
        copy_package(source_paths[key], target,
                     plan['dsh_packaging_overlay'] if item['name'] == '@deepseek-ai/dsh' else None)
        installed[str(target)] = key
        queue.append((key, target))
    for name, key in primary.items():
        install(key, stage / 'node_modules' / name)
    package_map = []
    while queue:
        key, owner = queue.popleft()
        mapping = {}
        for name, dependency in sorted(dependencies[key].items()):
            resolved = _nearest_package(owner, name, stage)
            if resolved is None or installed.get(str(resolved)) != dependency:
                resolved = owner / 'node_modules' / name
                if resolved.exists():
                    raise ValueError('Conflicting runtime package installation')
                install(dependency, resolved)
            mapping[name] = resolved.relative_to(stage).as_posix()
        package_map.append({**records[key], 'key': key, 'path': owner.relative_to(stage).as_posix(),
                            'compiled_manifest_sha256': sha(source_paths[key] / 'package.json'),
                            'dependencies': mapping})
    inventory = validate_candidate_inventory(plan, package_map)
    if inventory['status'] != 'passed':
        raise ValueError('The staged package inventory differs from the finance graph')
    shutil.copyfile(ROOT / 'runtime/finance-carrier/bootstrap.mjs', stage / 'bootstrap.mjs')
    (stage / 'finance-plugin').mkdir()
    for name in ('index.mjs', 'package.json'):
        shutil.copyfile(ROOT / 'runtime/finance-plugin' / name, stage / 'finance-plugin' / name)
    (stage / 'bin').mkdir()
    shutil.copyfile(node, stage / 'bin' / node.name)
    node_version = subprocess.check_output([str(node), '--version'], text=True).strip()
    (stage / 'licenses').mkdir()
    shutil.copyfile(upstream / 'LICENSE', stage / 'licenses' / 'DEEPSEEK_HARNESS_LICENSE')
    license_source = node_license or next((path for path in (node.parent / 'LICENSE', node.parent / 'LICENSE.txt') if path.is_file()), None)
    license_url = None
    if license_source:
        shutil.copyfile(license_source, stage / 'licenses' / 'NODE_LICENSE')
    else:
        license_url = f'https://raw.githubusercontent.com/nodejs/node/{node_version}/LICENSE'
        with urllib.request.urlopen(license_url, timeout=30) as response:
            license_bytes = response.read()
        if not license_bytes.startswith(b'Node.js is licensed'):
            raise ValueError('The original Node license was not verified')
        (stage / 'licenses' / 'NODE_LICENSE').write_bytes(license_bytes)
    files = []
    for path in sorted(_io(stage).rglob('*')):
        _regular(path)
        if path.is_file():
            files.append({'path': path.relative_to(_io(stage)).as_posix(), 'bytes': path.stat().st_size, 'sha256': sha(path)})
    manifest = {'format_version': 1, 'kind': 'mingjing-finance-carrier', 'status': 'staged_requires_runtime_acceptance',
                'platform': 'win32-x64', 'harness_version': plan['upstream_version'], 'git_commit': plan['git_commit'],
                'source_sha256': plan['source_sha256'], 'source_snapshot_sha256': plan['source_snapshot_sha256'],
                'bootstrap': 'bootstrap.mjs', 'node': 'bin/' + node.name, 'node_version': node_version,
                'plugin_sha256': sha(stage / 'finance-plugin/index.mjs'), 'node_license_source_url': license_url,
                'package_count': len(records), 'installed_package_count': len(package_map), 'package_map': package_map,
                'files': files, 'total_bytes': sum(entry['bytes'] for entry in files),
                'packaging_overlay': plan['dsh_packaging_overlay'], 'runtime_acceptance': 'not_executed',
                'clean_windows_acceptance': 'not_executed', 'mingjing_exe_built': False}
    (stage / 'carrier_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(stage, destination)
    return manifest


def sync_finance_plugin(destination: Path) -> dict:
    """Refresh only the development-owned plugin after an intentional edit.

    It does not recopy packages, build an EXE, or claim a prior receipt still
    applies to the newly locked plugin version.
    """
    from mingjing.harness_client import verify_finance_carrier
    destination = destination.resolve(strict=True)
    previous = verify_finance_carrier(destination)
    previous.pop('manifest_sha256', None)
    for name in ('index.mjs', 'package.json'):
        source = ROOT / 'runtime/finance-plugin' / name
        target = destination / 'finance-plugin' / name
        shutil.copyfile(source, target)
        entry = next(item for item in previous['files'] if item['path'] == 'finance-plugin/' + name)
        entry.update(bytes=target.stat().st_size, sha256=sha(target))
    previous.update(plugin_sha256=sha(destination / 'finance-plugin/index.mjs'),
                    total_bytes=sum(item['bytes'] for item in previous['files']),
                    status='staged_requires_runtime_acceptance', runtime_acceptance='not_executed')
    target = destination / 'carrier_manifest.json'
    temporary = destination / 'carrier_manifest.json.tmp'
    temporary.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, target)
    return previous


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, default=ROOT / 'vendor/harness')
    parser.add_argument('--out', type=Path, default=ROOT / 'runtime/finance-carrier/staged')
    parser.add_argument('--node', type=Path, default=Path(shutil.which('node') or 'node'))
    parser.add_argument('--node-license', type=Path)
    parser.add_argument('--sync-plugin', action='store_true', help='Refresh the owned plugin only; does not recopy dependency assets')
    args = parser.parse_args()
    manifest = (sync_finance_plugin(args.out) if args.sync_plugin else
                stage_runtime(args.upstream, args.out, args.node, node_license=args.node_license))
    print(json.dumps({'status': manifest['status'], 'packages': manifest['package_count'],
                      'installed_packages': manifest['installed_package_count'], 'files': len(manifest['files']),
                      'bytes': manifest['total_bytes'], 'node_version': manifest['node_version'], 'out': str(args.out),
                      'runtime_acceptance': 'not_executed', 'mingjing_exe_built': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
