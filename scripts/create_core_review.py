"""Create a bounded, credential-free core review folder and ZIP.

This is a source review artifact, not an independently runnable native Agent.
The allowlist deliberately omits full upstream Harness sources, carrier
binaries, user profiles, raw real reports, run histories and dependency trees.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tomllib
import uuid
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LIMIT_BYTES = 30_000_000  # Decimal MB: below both 30 MB and 30 MiB.
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 100 * 1024 * 1024
DIRECTORIES = ('mingjing', 'kernel', 'legacy_tools', 'rulepack', 'scripts',
               'tests', 'docs', 'frontend', 'web', 'runtime/finance-plugin')
SUFFIXES = {'.py', '.json', '.jsonl', '.csv', '.tsv', '.md', '.txt', '.rst',
            '.mjs', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.svg',
            '.toml', '.yaml', '.yml', '.lock', '.ps1', '.woff', '.woff2', '.ttf'}
DENIED_PARTS = {'.git', '.pytest_cache', '__pycache__', 'node_modules',
                'runs', 'reports', 'runtime-cache', 'baseline', '.baseline',
                'dist', 'build', 'tmp', 'logs', '.codex', 'credentials', 'secrets'}
DENIED_NAMES = {'config.json', 'secrets.json', 'credentials.json', 'token.json',
                'api_key.txt', 'apikey.txt', 'private.key', 'id_rsa', 'id_ed25519'}
ROOT_FILES = ('README.md', 'LICENSE', '.gitignore', '.gitattributes',
              'pyproject.toml', 'pytest.ini', 'requirements-build.lock',
              'requirements-build.json', 'MingJing.spec', 'app_entry.py', '__init__.py')
EXPLICIT = ('samples/demo_draft.md', 'samples/demo_draft_defined.md',
            'samples/corpus/facts.json', 'samples/corpus/demo_annual.pdf',
            'runtime/harness/source_snapshot.json', 'runtime/harness/manifest.json',
            'runtime/finance-carrier/bootstrap.mjs',
            'bench/README.md', 'bench/__init__.py', 'bench/evaluate.py',
            'bench/stats.py', 'bench/acceptance_cases.json', 'bench/samples_demo.jsonl')
BASELINES = ('明镜MingJing_项目计划书.docx', '明镜_计划书打磨与开发任务手册.md')
PRIVATE_MARKERS = (b'-----BEGIN PRIVATE KEY-----', b'-----BEGIN RSA PRIVATE KEY-----',
                   b'-----BEGIN EC PRIVATE KEY-----', b'-----BEGIN OPENSSH PRIVATE KEY-----')


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _unlinked(path: Path) -> None:
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or getattr(path, 'is_junction', lambda: False)()):
        raise ValueError('Review artifacts cannot traverse symlinks, junctions or reparse points')


def _canonical(path: Path, *, exists: bool = True) -> Path:
    path = Path(os.path.abspath(path))
    for parent in [*reversed(path.parents), path]:
        if parent.exists() or parent.is_symlink():
            _unlinked(parent)
        elif exists:
            raise ValueError('Required review source is missing')
    if path.resolve(strict=exists) != path:
        raise ValueError('Review paths must be canonical')
    return path


def _allowed(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part.lower() in DENIED_PARTS for part in relative.parts):
        return False
    if path.name.lower() in DENIED_NAMES or path.name.lower().startswith('.env'):
        return False
    return path.suffix.lower() in SUFFIXES or path.name.upper() in {'LICENSE', 'NOTICE', 'COPYING'}


def review_sources(root: Path, requirements_dir: Path, *, include_baselines: bool = True) -> list[tuple[Path, str]]:
    """Use approved directories plus exact demo/baseline resources only."""
    root, requirements_dir = _canonical(root), _canonical(requirements_dir)
    sources: dict[str, Path] = {}

    def add(path: Path, member: str, *, required: bool = True) -> None:
        if not path.exists() and not path.is_symlink():
            if required:
                raise ValueError('Required review resource is missing: ' + member)
            return
        _canonical(path)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('Review resource must be a regular file: ' + member)
        if member in sources:
            raise ValueError('Duplicate review archive member: ' + member)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('Review source exceeds the per-file size limit: ' + member)
        sources[member] = path

    for name in ROOT_FILES:
        add(root / name, 'PROJECT_README.md' if name == 'README.md' else name,
            required=name in {'README.md', 'LICENSE', 'pyproject.toml'})
    for directory in DIRECTORIES:
        folder = root / directory
        if not folder.exists():
            continue
        _canonical(folder)
        # os.walk does not follow links. Validate any allowed subtree before
        # visiting it rather than resolving a link and losing its identity.
        for current, dirs, files in os.walk(folder, followlinks=False):
            kept = []
            for name in sorted(dirs):
                child = Path(current) / name
                if name.lower() in DENIED_PARTS:
                    continue
                _unlinked(child)
                kept.append(name)
            dirs[:] = kept
            for name in sorted(files):
                path = Path(current) / name
                if _allowed(path, root):
                    add(path, path.relative_to(root).as_posix())
    # Only top-level v1 evaluator/schema code enters; annotation candidates,
    # blind identity forms, real holdout data and their history stay external.
    bench_v1 = root / 'bench/v1'
    if bench_v1.exists():
        _canonical(bench_v1)
        for path in sorted(bench_v1.iterdir()):
            if path.is_file() and _allowed(path, root):
                add(path, path.relative_to(root).as_posix())
    for name in EXPLICIT:
        add(root / name, name, required=name in {
            'samples/demo_draft_defined.md', 'samples/corpus/facts.json',
            'samples/corpus/demo_annual.pdf', 'runtime/harness/source_snapshot.json'})
    if include_baselines:
        for name in BASELINES:
            add(requirements_dir / name, 'requirements/' + name)
    if sum(path.stat().st_size for path in sources.values()) > MAX_SOURCE_BYTES:
        raise ValueError('Review source exceeds the total uncompressed limit')
    return sorted((path, name) for name, path in sources.items())


def select_files(root: Path = ROOT) -> list[Path]:
    """Reusable project-source allowlist for a clean GitHub export.

    Baselines outside the project and full upstream Harness sources are not
    selected here; their callers add those explicitly after validation.
    """
    return [path for path, _ in review_sources(root, root, include_baselines=False)]


def configured_secret_values(root: Path) -> list[bytes]:
    # Read-only DPAPI access. Missing/decryption errors remain failures instead
    # of turning an unexecuted configured-key scan into a passing receipt.
    from scripts.package_delivery import configured_secret_values as load_values
    return load_values(root)


def scan_bytes(data: bytes, values: list[bytes], member: str) -> None:
    if any(value and value in data for value in values):
        raise ValueError('Configured plaintext credential detected in: ' + member)
    # Test/source code may legitimately mention PEM header strings. Actual
    # private key blocks also contain line breaks and encoded key material.
    for marker in PRIVATE_MARKERS:
        if marker + b'\n' in data or marker + b'\r\n' in data:
            raise ValueError('Private key material detected in: ' + member)
    if member.lower().endswith('.docx'):
        # Word content is ZIP-compressed: scanning the container bytes alone
        # would miss a token inside document.xml, comments or custom XML.
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as document:
                entries = document.infolist()
                if (sum(item.file_size for item in entries) > 50 * 1024 * 1024
                        or any(item.file_size > MAX_FILE_BYTES for item in entries)):
                    raise ValueError('DOCX decompressed size exceeds the scan limit: ' + member)
                if 'word/document.xml' not in document.namelist():
                    raise ValueError('Review baseline is not a valid Word document: ' + member)
                for entry in entries:
                    if not entry.is_dir():
                        content = document.read(entry)
                        scan_bytes(content, values, member + '::' + entry.filename)
                        if entry.filename.lower().endswith('.xml'):
                            try:
                                text = ''.join(ET.fromstring(content).itertext()).encode('utf-8')
                            except ET.ParseError as exc:
                                raise ValueError('Invalid XML in Word review document: ' + member) from exc
                            # Word may split one visible token into multiple
                            # XML text runs or escape its characters.
                            scan_bytes(text, values, member + '::decoded-xml')
        except zipfile.BadZipFile as exc:
            raise ValueError('Review baseline is not a valid Word document: ' + member) from exc


def _readme(version: str) -> str:
    return f'''# 明镜核心审阅包（{version}）

本包供评审、指导和协作修改核心代码使用。它不包含完整 Harness 源码、原生载体或开发依赖，不能单独启动完整原生 Agent；运行程序请使用对应阶段的 MingJing.exe，完整开发请克隆 https://github.com/M0onesy/Light_Mirror 。

- 项目介绍：[PROJECT_README.md](PROJECT_README.md)
- 详细使用：[docs/USER_GUIDE.md](docs/USER_GUIDE.md)
- 三种交付：[docs/DELIVERY_GUIDE.md](docs/DELIVERY_GUIDE.md)
- 需求基线：requirements/ 下的计划书和内部开发任务手册
- Python 金融内核与宿主：mingjing/ 与 kernel/；规则包：rulepack/
- 原生金融插件：runtime/finance-plugin/；界面：frontend/src/
- 上游来源和运行时仅保留清单引用，完整二进制资产不在本包内。

审阅者建议先读计划书，再对照手册尾部问题与解决指导。修改后把补丁或 Git 分支交给项目负责人合并；不要覆盖历史运行锁。离线内核可在 Python 3.12 环境安装锁定依赖后用 PROJECT_README 中的 CLI 示例运行；仅自带fixture的内核测试可独立执行，原生 Agent、上游来源、载体及外部证据闭包测试需要完整工程。

core-review-manifest.json 登记每个文件的大小、SHA-256 和审阅包边界。ZIP 的外部回执记录压缩大小及 ZIP SHA-256。真实材料、运行历史、个人配置、密钥、node_modules 和 .git 均排除。
'''


def create_core_review(root: Path, out: Path, *, requirements_dir: Path | None = None,
                       secret_values: list[bytes] | None = None,
                       max_archive_bytes: int = LIMIT_BYTES) -> dict:
    root = _canonical(root)
    requirements_dir = _canonical(requirements_dir or root.parent)
    workspace = _canonical(root.parents[1])
    out = _canonical(out, exists=False)
    if (out == workspace or not out.is_relative_to(workspace)
            or out.is_relative_to(root) or root.is_relative_to(out)):
        raise ValueError('Core review destination must be a separate folder inside the authorized workspace')
    if type(max_archive_bytes) is not int or not 0 < max_archive_bytes <= LIMIT_BYTES:
        raise ValueError('Archive limit must not exceed 30,000,000 bytes')
    target_zip = out.with_name(out.name + '.zip')
    receipt_path = out.with_name(out.name + '-receipt.json')
    for path in (out, target_zip, receipt_path):
        _canonical(path, exists=False)
        if path.exists():
            raise FileExistsError('Refusing to overwrite an existing review artifact: ' + path.name)
    sources = review_sources(root, requirements_dir)
    values = secret_values if secret_values is not None else configured_secret_values(root)
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    rows = []
    for source, member in sources:
        data = source.read_bytes()
        scan_bytes(data, values, member)
        rows.append({'path': member, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {
        'schema': 'mingjing-core-review.v1', 'artifact_kind': 'core_source_review',
        'version': version, 'created_at': datetime.now(timezone.utc).isoformat(),
        'independently_runnable_native_agent': False, 'max_archive_bytes': max_archive_bytes,
        'source_bytes': sum(row['bytes'] for row in rows), 'files': rows,
        'excluded': ['full_upstream_harness', 'native_carrier', 'node_modules', 'real_reports',
                     'runs', 'user_profiles', 'credentials', 'git_history', 'holdout_annotations'],
        'credential_scan': {'status': 'passed', 'method': 'strict_allowlist_exact_configured_values_private_key_blocks',
                            'references_checked': len(values)},
        'license': 'AGPL-3.0-only for project-owned code; retained upstream licenses apply to third-party files',
    }
    stage = out.with_name('.core-review-incomplete-' + uuid.uuid4().hex)
    zip_stage = target_zip.with_name('.core-review-incomplete-' + uuid.uuid4().hex + '.zip')
    out.parent.mkdir(parents=True, exist_ok=True)
    stage.mkdir()
    try:
        for (source, member), row in zip(sources, rows):
            _canonical(source)
            destination = stage.joinpath(*Path(member).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open('rb') as incoming, destination.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if destination.stat().st_size != row['bytes'] or _sha(destination) != row['sha256']:
                raise ValueError('Review input changed while copying: ' + member)
        readme = _readme(str(version)).encode('utf-8')
        scan_bytes(readme, values, 'README.md')
        (stage / 'README.md').write_bytes(readme)
        manifest['files'].append({'path': 'README.md', 'bytes': len(readme), 'sha256': hashlib.sha256(readme).hexdigest()})
        manifest['source_bytes'] += len(readme)
        (stage / 'core-review-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        with zipfile.ZipFile(zip_stage, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    archive.write(path, out.name + '/' + path.relative_to(stage).as_posix())
        if zip_stage.stat().st_size > max_archive_bytes:
            raise ValueError('Compressed core review archive exceeds its 30 MB limit')
        with zipfile.ZipFile(zip_stage) as archive:
            expected = {out.name + '/' + row['path']: row for row in manifest['files']}
            if set(archive.namelist()) != set(expected) | {out.name + '/core-review-manifest.json'}:
                raise ValueError('Review ZIP contains unregistered members')
            for member, row in expected.items():
                data = archive.read(member)
                scan_bytes(data, values, member)
                if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                    raise ValueError('Review ZIP hash verification failed: ' + member)
            if json.loads(archive.read(out.name + '/core-review-manifest.json')) != manifest:
                raise ValueError('Review ZIP manifest verification failed')
        # Recheck original bytes after ZIP verification; every shipped member
        # belongs to the same inspected source epoch.
        for (source, member), row in zip(sources, rows):
            _canonical(source)
            if source.stat().st_size != row['bytes'] or _sha(source) != row['sha256']:
                raise ValueError('Review source changed during packaging: ' + member)
        receipt = {'schema': 'mingjing-core-review-receipt.v1', 'status': 'passed',
                   'version': version, 'folder': out.name, 'archive': target_zip.name,
                   'archive_bytes': zip_stage.stat().st_size, 'archive_sha256': _sha(zip_stage),
                   'manifest_sha256': _sha(stage / 'core-review-manifest.json'),
                   'file_count': len(manifest['files']), 'source_bytes': manifest['source_bytes'],
                   'under_30_mb': True, 'credential_scan': manifest['credential_scan']}
        for destination in (out, target_zip, receipt_path):
            _canonical(destination, exists=False)
            if destination.exists():
                raise FileExistsError('A review destination appeared during packaging')
        os.rename(stage, out)
        # Same-volume hardlink publication is exclusive, unlike replace.
        os.link(zip_stage, target_zip)
        zip_stage.unlink()
        with receipt_path.open('x', encoding='utf-8') as handle:
            handle.write(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        return receipt
    finally:
        # Never delete a computed directory without checking its final root
        # and refusing reparse points. Final review artifacts are immutable.
        if stage.exists():
            checked = _canonical(stage)
            if checked.parent != out.parent or not checked.name.startswith('.core-review-incomplete-'):
                raise ValueError('Unsafe staging cleanup path')
            shutil.rmtree(checked)
        zip_stage.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT.parents[1] / 'MingJing-core-review')
    parser.add_argument('--requirements-dir', type=Path, default=ROOT.parent)
    args = parser.parse_args(argv)
    receipt = create_core_review(ROOT, args.out, requirements_dir=args.requirements_dir)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
