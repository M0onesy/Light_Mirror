"""Export a clean, credential-free source tree without rewriting local history."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from scripts.create_core_review import select_files, configured_secret_values, scan_bytes, _canonical, BASELINES
from scripts.upstream_source import ROOT, DEFAULT_UPSTREAM, verify_upstream, locked_source, io_path


def scan_document(data: bytes, secrets: list[bytes], name: str) -> None:
    scan_bytes(data, secrets, name)
    if name.lower().endswith('.pdf'):
        # Generated PDF text can be compressed and therefore invisible to a
        # container-byte scan. PyMuPDF is already a required build dependency.
        import fitz
        try:
            with fitz.open(stream=data, filetype='pdf') as document:
                if document.needs_pass:
                    raise ValueError('Encrypted PDF cannot enter the public export: ' + name)
                for index, page in enumerate(document):
                    scan_bytes(page.get_text().encode('utf-8'), secrets, name + ':page-' + str(index + 1))
                for value in document.metadata.values():
                    if isinstance(value, str):
                        scan_bytes(value.encode('utf-8'), secrets, name + ':metadata')
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('Public PDF could not be decoded for credential scanning: ' + name) from exc


def project_documents(project_root: Path = ROOT) -> dict[str, Path]:
    """Select exact requirement baselines and the current review PDF only."""
    requirements = _canonical(project_root.parent)
    selected = {}
    for name in BASELINES:
        path = _canonical(requirements / name)
        if not path.is_file():
            raise ValueError('Project requirement baseline must be a regular file')
        selected['docs/project/' + name] = path
    for name in ('金融人工智能竞赛要求.txt', '明镜MingJing_项目计划书_初赛评审版.pdf'):
        path = requirements / name
        if path.exists() or path.is_symlink():
            path = _canonical(path)
            if not path.is_file():
                raise ValueError('Project submission resource must be a regular file')
            selected['docs/project/' + name] = path
    return selected


def export_repository(destination: Path) -> dict:
    destination = _canonical(destination, exists=False)
    workspace = ROOT.parents[1]
    if destination.parent != workspace or destination.exists():
        raise ValueError('Public export must be a new, separate directory at the workspace root')
    source_receipt = verify_upstream()
    secrets = configured_secret_values(ROOT)
    selected = {p.relative_to(ROOT).as_posix(): p for p in select_files(ROOT)}
    for item in locked_source()['files']:
        selected['vendor/harness/' + item['path']] = DEFAULT_UPSTREAM / item['path']
    selected.update(project_documents(ROOT))
    rows = []
    destination.mkdir()
    # Each public member is explicitly selected, including immutable upstream
    # bytes. No inherited worktree metadata, binaries or private run caches.
    for member, source in sorted(selected.items()):
        data = io_path(source).read_bytes()
        if len(data) >= 100 * 1024 * 1024:
            raise ValueError('GitHub file-size limit exceeded: ' + member)
        scan_document(data, secrets, member)
        out = destination / member
        io_path(out.parent).mkdir(parents=True, exist_ok=True)
        io_path(out).write_bytes(data)
        rows.append({'path': member, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    receipt = {'schema': 'mingjing-public-source.v1', 'status': 'exported_and_scanned',
               'created_at': datetime.now(timezone.utc).isoformat(), 'upstream': source_receipt,
               'history_policy': 'new_public_history_local_shared_history_retained',
               'credential_scan': {'configured_refs_checked': len(secrets), 'status': 'passed'},
               'files': rows, 'total_bytes': sum(row['bytes'] for row in rows),
               'max_file_bytes': max(row['bytes'] for row in rows)}
    (destination / 'source-export-manifest.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Preserve line endings of every hashed file after clone, regardless of
    # the contributor's core.autocrlf; third-party notices remain byte exact.
    with (destination / '.gitattributes').open('a', encoding='utf-8') as stream:
        stream.write('\n# Source-export manifests use exact byte hashes.\n* -text\n')
    # The export manifest describes inputs, not the modified attributes policy.
    entry = next(row for row in rows if row['path'] == '.gitattributes')
    data = (destination / '.gitattributes').read_bytes()
    entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    receipt['total_bytes'] = sum(row['bytes'] for row in rows)
    (destination / 'source-export-manifest.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {key: receipt[key] for key in ('status', 'total_bytes', 'max_file_bytes', 'credential_scan')}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT.parents[1] / 'Light_Mirror')
    args = parser.parse_args()
    print(json.dumps(export_repository(args.out), ensure_ascii=False))


if __name__ == '__main__':
    main()
