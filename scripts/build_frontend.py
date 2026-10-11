"""Build the Harness UI and bind its served bytes to the actual source inputs.

This is an incremental frontend build, not an EXE or distribution build.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = 'build_receipt.json'


def inventory(root, paths):
    root = Path(root).resolve()
    records = []
    for path in sorted(set(paths)):
        info = path.lstat()
        if (not path.resolve(strict=True).is_relative_to(root) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400 or not stat.S_ISREG(info.st_mode)):
            raise ValueError('Frontend build inputs must be regular files within the project')
        for parent in path.parents:
            if parent == root:
                break
            attributes = parent.lstat()
            if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Frontend build inputs cannot traverse linked directories')
        records.append({'path': path.relative_to(root).as_posix(), 'bytes': info.st_size,
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return records


def input_files(root):
    front = Path(root)/'frontend'
    paths = [path for path in front.rglob('*') if path.is_file() and not any(
        part in {'node_modules', '.vite', '__pycache__'} for part in path.relative_to(front).parts)]
    return inventory(root, paths)


def output_files(root):
    web = Path(root)/'web'/'harness'
    return inventory(root, [path for path in web.rglob('*') if path.is_file() and path != web/RECEIPT])


def verify(root=ROOT):
    root = Path(root).resolve()
    receipt = json.loads((root/'web'/'harness'/RECEIPT).read_text(encoding='utf-8'))
    if receipt.get('kind') != 'mingjing-harness-ui-build.v1' or receipt.get('status') != 'built':
        raise ValueError('No successful Harness frontend build receipt')
    if receipt['inputs'] != input_files(root):
        raise ValueError('Harness frontend source changed after the served assets were built')
    if receipt['outputs'] != output_files(root) or not (root/'web'/'harness'/'index.html').is_file():
        raise ValueError('Harness frontend assets changed after their build receipt')
    return {'status': 'passed_source_to_assets', 'source_files': len(receipt['inputs']),
            'asset_files': len(receipt['outputs']), 'source_sha256': receipt['source_sha256'],
            'assets_sha256': receipt['assets_sha256']}


def smart_files(root):
    folder = Path(root)/'web'/'smart'
    return inventory(root, [p for p in folder.rglob('*') if p.is_file() and p.name != RECEIPT])


def verify_smart(root=ROOT):
    root = Path(root).resolve()
    receipt = json.loads((root/'web'/'smart'/RECEIPT).read_text(encoding='utf-8'))
    if receipt.get('kind') != 'mingjing-upload-ui-source.v1' or receipt.get('status') != 'checked':
        raise ValueError('Upload workbench has no checked source receipt')
    if receipt.get('files') != smart_files(root):
        raise ValueError('Upload workbench changed after its source checks')
    return {'status': 'passed_source_as_assets', 'files': len(receipt['files']), 'sha256': receipt['sha256']}


def record_smart(root=ROOT):
    """The upload UI serves original source files; validate JS before freezing."""
    root = Path(root).resolve()
    before = smart_files(root)
    node = root/'runtime'/'finance-carrier'/'staged'/'bin'/'node.exe'
    if not node.is_file():
        candidate = shutil.which('node')
        if not candidate:
            raise ValueError('A checked Node runtime is required to validate JavaScript')
        node = Path(candidate)
    for row in before:
        if row['path'].endswith('.js'):
            subprocess.run([str(node), '--check', str(root/row['path'])], check=True)
    if before != smart_files(root):
        raise ValueError('Upload UI changed during JavaScript validation')
    canonical = json.dumps(before, sort_keys=True, separators=(',', ':')).encode()
    receipt = {'kind': 'mingjing-upload-ui-source.v1', 'status': 'checked', 'files': before,
               'sha256': hashlib.sha256(canonical).hexdigest(), 'recorded_at': datetime.now(timezone.utc).isoformat()}
    (root/'web'/'smart'/RECEIPT).write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return verify_smart(root)


def build(root=ROOT):
    root = Path(root).resolve()
    front = root/'frontend'
    inputs = input_files(root)
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    node = shutil.which('node')
    if not npm or not node:
        raise RuntimeError('Frontend development build requires installed Node and npm')
    subprocess.run([npm, 'run', 'typecheck'], cwd=front, check=True)
    subprocess.run([npm, 'run', 'build'], cwd=front, check=True)
    if input_files(root) != inputs:
        raise ValueError('Frontend source changed during compilation; do not serve this build')
    outputs = output_files(root)
    canonical = lambda rows: hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    tools = {}
    for name in ('vite', 'typescript', 'react'):
        tools[name] = json.loads((front/'node_modules'/name/'package.json').read_text(encoding='utf-8'))['version']
    receipt = {'kind': 'mingjing-harness-ui-build.v1', 'status': 'built', 'inputs': inputs, 'outputs': outputs,
               'source_sha256': canonical(inputs), 'assets_sha256': canonical(outputs),
               'recorded_at': datetime.now(timezone.utc).isoformat(), 'tools': tools,
               'node': subprocess.check_output([node, '--version'], text=True).strip()}
    path = root/'web'/'harness'/RECEIPT
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(temporary, path)
    return verify(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--smart', action='store_true')
    args = parser.parse_args()
    action = verify_smart if args.verify else record_smart
    print(json.dumps(action() if args.smart else verify() if args.verify else build(), ensure_ascii=False))


if __name__ == '__main__':
    main()
