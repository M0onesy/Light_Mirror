"""Verify staged source bytes and the locked vendor snapshot without installs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def long_path(path):
    return Path('\\\\?\\' + str(path)) if os.name == 'nt' and len(str(path)) >= 240 else path


def sha(path):
    digest = hashlib.sha256()
    with long_path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    records = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=root).split(b'\0')
    items = []
    process = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=root, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for row in records:
            if not row:
                continue
            metadata, raw_path = row.split(b'\t', 1)
            mode, oid, stage = metadata.split()
            if stage != b'0' or mode not in (b'100644', b'100755'):
                raise ValueError('Unmerged, linked or unsupported source entry')
            name = raw_path.decode('utf-8')
            path = root / name
            process.stdin.write(oid + b'\n')
            process.stdin.flush()
            header = process.stdout.readline().split()
            size = int(header[2])
            data = process.stdout.read(size)
            if process.stdout.read(1) != b'\n' or header[1] != b'blob' or len(data) != size:
                raise ValueError('Git blob read mismatch')
            digest = hashlib.sha256(data).hexdigest()
            if path.is_symlink() or not long_path(path).is_file() or sha(path) != digest:
                raise ValueError('Staged bytes differ from the worktree: ' + name)
            items.append({'path': name, 'git_blob': oid.decode('ascii'), 'bytes': size, 'sha256': digest})
    finally:
        process.stdin.close()
        process.wait(timeout=30)
    indexed = {row['path']: row for row in items}
    snapshot = root / 'runtime/harness/source_snapshot.json'
    vendor = json.loads(snapshot.read_text(encoding='utf-8'))
    for entry in vendor['files']:
        name = 'vendor/harness/' + entry['path']
        if (name not in indexed or indexed[name]['sha256'] != entry['sha256']
                or indexed[name]['bytes'] != entry['bytes']):
            raise ValueError('Locked Harness source missing or changed: ' + name)
    from scripts.build_frontend import verify, verify_smart
    verify(root)
    verify_smart(root)
    manifest = {'schema': 'mingjing.public-source-inventory.v1', 'status': 'passed',
                'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip(),
                'source_files': len(items), 'source_bytes': sum(row['bytes'] for row in items),
                'staged_bytes_equal': True, 'vendor_files': len(vendor['files']),
                'vendor_snapshot_sha256': sha(snapshot), 'frontend_receipts_verified': True,
                'files': items}
    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in manifest.items() if key != 'files'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
