"""Freeze build inputs and record exact EXE, runtime and installed dependencies."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import stat
import sys
import tempfile
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    local = Path('\\\\?\\'+str(path.absolute())) if os.name == 'nt' and not str(path).startswith('\\\\?\\') else path
    with local.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _transport():
    # The script is also called directly, when Python starts with scripts/ on
    # sys.path. Import only this checkout's consumer, never an installed SDK.
    project = str(Path(__file__).resolve().parents[1])
    if project not in sys.path:
        sys.path.insert(0, project)
    from mingjing import harness_client
    return harness_client


def _carrier_root(root: Path) -> Path:
    return root / 'runtime/finance-carrier/staged'


def _payload_root(root: Path, manifest_sha256: str) -> Path:
    return root / 'runtime/finance-carrier/bundles' / manifest_sha256


def runtime_assets(root: Path) -> tuple[dict, list[Path]]:
    """Lock the actual finance-only closure, not an absent full SDK/Office EXE."""
    consumer = _transport()
    carrier = _carrier_root(root)
    try:
        manifest = consumer.verify_finance_carrier(carrier)
        snapshot = root / 'runtime/harness/source_snapshot.json'
        consumer._regular_path(snapshot, root / 'runtime')
        if sha256(snapshot) != manifest.get('source_snapshot_sha256'):
            raise ValueError('Source provenance differs from the finance carrier lock')
        provenance = json.loads(snapshot.read_text(encoding='utf-8'))
        if (provenance.get('source_sha256') != manifest.get('source_sha256')
                or provenance.get('upstream_version') != manifest.get('harness_version')
                or provenance.get('git_commit') != manifest.get('git_commit')):
            raise ValueError('Source provenance does not identify the deployed Harness snapshot')
        plugin_source = root / 'runtime/finance-plugin/index.mjs'
        if plugin_source.is_file() and sha256(plugin_source) != manifest['plugin_sha256']:
            raise ValueError('Finance plugin source differs from its deployed closure')
        paths = [carrier / 'carrier_manifest.json', snapshot,
                 *(carrier / entry['path'] for entry in manifest['files'])]
        paths += finance_payload_assets(root, required=False)
        return manifest, paths
    except (consumer.HarnessError, OSError) as exc:
        raise ValueError('Finance runtime asset differs from its frozen dependency lock') from exc


def validate_finance_payload(bundle: Path, expected_digest: str | None = None) -> list[Path]:
    """Validate distribution bytes and every member without extracting npm."""
    consumer = _transport()
    manifest, _ = consumer._read_finance_manifest(bundle)
    if expected_digest is not None and manifest['manifest_sha256'] != expected_digest:
        raise ValueError('Finance payload manifest differs from the source carrier')
    descriptor_path = bundle / 'payload.json'
    archive_path = bundle / 'finance-carrier.payload.zip'
    consumer._regular_path(descriptor_path, bundle)
    consumer._regular_path(archive_path, bundle)
    descriptor = json.loads(descriptor_path.read_text(encoding='utf-8'))
    if (descriptor.get('format_version') != 1 or descriptor.get('carrier_manifest_sha256') != manifest['manifest_sha256']
            or descriptor.get('payload') != archive_path.name or type(descriptor.get('bytes')) is not int
            or descriptor['bytes'] != archive_path.stat().st_size or descriptor.get('sha256') != sha256(archive_path)):
        raise ValueError('Finance payload differs from its distribution receipt')
    entries = {entry['path']: entry for entry in manifest['files']}
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) != len(entries) or {item.filename for item in infos} != set(entries):
            raise ValueError('Finance payload inventory differs from its carrier lock')
        for info in infos:
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16) or info.file_size != entries[info.filename]['bytes']:
                raise ValueError('Invalid or linked finance payload member')
            digest = hashlib.sha256()
            with archive.open(info) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            if digest.hexdigest() != entries[info.filename]['sha256']:
                raise ValueError('Finance payload member differs from its source lock')
    return [bundle / 'carrier_manifest.json', descriptor_path, archive_path]


def finance_payload_assets(root: Path, *, required: bool = True) -> list[Path]:
    consumer = _transport()
    manifest, _ = consumer._read_finance_manifest(_carrier_root(root))
    bundle = _payload_root(root, manifest['manifest_sha256'])
    if not bundle.exists() and not required:
        return []
    if not bundle.is_dir():
        raise ValueError('Finance payload is not prepared; run build_receipt.py --prepare at release build time')
    return validate_finance_payload(bundle, manifest['manifest_sha256'])


def prepare_finance_payload(root: Path) -> list[Path]:
    """Create one compressed carrier per frozen manifest, only for a build.

    Development startup consumes raw carrier files directly into its stable
    cache; it never calls this function or repeatedly zips/unzips this closure.
    """
    consumer = _transport()
    manifest, _ = runtime_assets(root)
    source = _carrier_root(root)
    base = root / 'runtime/finance-carrier/bundles'
    consumer._regular_path(base, root)
    base.mkdir(parents=True, exist_ok=True)
    bundle = _payload_root(root, manifest['manifest_sha256'])
    lock_path = base / (manifest['manifest_sha256']+'.lock')
    consumer._regular_path(lock_path, root)
    with consumer._RuntimeCacheLock(lock_path):
        if bundle.exists():
            return validate_finance_payload(bundle, manifest['manifest_sha256'])
        stage = Path(tempfile.mkdtemp(prefix='.'+manifest['manifest_sha256'][:16]+'-', dir=base))
        try:
            archive_path = stage / 'finance-carrier.payload.zip'
            with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for entry in manifest['files']:
                    original = source / entry['path']
                    consumer._regular_path(original, source)
                    info = zipfile.ZipInfo(entry['path'], date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    digest, count = hashlib.sha256(), 0
                    with consumer._io_path(original).open('rb') as stream, archive.open(info, 'w') as output:
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            count += len(block)
                            digest.update(block)
                            output.write(block)
                    if count != entry['bytes'] or digest.hexdigest() != entry['sha256']:
                        raise ValueError('Finance carrier changed during payload creation')
            (stage / 'carrier_manifest.json').write_bytes((source / 'carrier_manifest.json').read_bytes())
            descriptor = {'format_version': 1, 'carrier_manifest_sha256': manifest['manifest_sha256'],
                          'payload': archive_path.name, 'bytes': archive_path.stat().st_size, 'sha256': sha256(archive_path)}
            atomic_write(stage / 'payload.json', json.dumps(descriptor, indent=2)+'\n')
            validate_finance_payload(stage, manifest['manifest_sha256'])
            os.replace(stage, bundle)
            return validate_finance_payload(bundle, manifest['manifest_sha256'])
        finally:
            if stage.exists():
                if stage.parent != base or not stage.name.startswith('.'+manifest['manifest_sha256'][:16]+'-'):
                    raise ValueError('Unsafe finance payload staging cleanup')
                consumer._regular_path(stage, base)
                import shutil
                shutil.rmtree(consumer._io_path(stage))


def installed_dependencies(root: Path) -> dict:
    snapshot = json.loads((root / "requirements-build.json").read_text(encoding="utf-8"))
    if sha256(root / "requirements-build.lock") != snapshot["lock_sha256"]:
        raise ValueError("Python dependency lock differs from its manifest")
    for item in snapshot["packages"]:
        if metadata.version(item["name"]) != item["version"]:
            raise ValueError("Installed build dependency differs from the frozen metadata")
    return snapshot


def prepare_notices(root: Path, dependencies: dict, runtime: dict) -> None:
    license_records = []
    for origin in Path(sys.base_prefix).glob("*LICENSE*PYTHON*"):
        if origin.is_file():
            target = root / "docs/licenses/python/python" / origin.name
            atomic_write(target, origin.read_text(encoding="utf-8"))
            license_records.append({"component": "Python", "path": target.relative_to(root).as_posix(), "sha256": sha256(target)})
    for item in dependencies["packages"]:
        dist = metadata.distribution(item["name"])
        for file in dist.files or []:
            filename = Path(str(file)).name
            if not any(word in filename.upper() for word in ("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                continue
            origin = dist.locate_file(file)
            if not origin.is_file() or origin.stat().st_size > 2_000_000:
                continue
            try:
                text = origin.read_text(encoding="utf-8")
            except (UnicodeError, OSError):
                continue
            identifier = item["name"].lower().replace("_", "-")
            name = sha256(origin)[:12] + "-" + filename
            target = root / "docs" / "licenses" / "python" / identifier / name
            atomic_write(target, text)
            license_records.append({"component": item["name"], "path": target.relative_to(root).as_posix(), "sha256": sha256(target)})
    node_licenses = [entry for entry in runtime["files"] if any(
        word in PurePosixPath(entry["path"]).name.upper() for word in ("LICENSE", "LICENCE", "COPYING", "NOTICE"))]
    body = ["# Third-party notices / 第三方组件", "",
            f"Python {platform.python_version()} — Python Software Foundation License. https://docs.python.org/3/license.html", "",
            "明镜自有代码按根目录 LICENSE 的 AGPL-3.0-only 发布，详见 docs/LICENSE_GUIDE.md；第三方原许可保持不变。",
            "本程序嵌入 PyMuPDF/MuPDF、复用官方 Agent Loop 的金融 Harness carrier 及其锁定生产依赖。",
            "DeepSeek Harness 框架与模型供应商独立；接入 API 的服务条款由配置供应商规定。",
            "PyMuPDF/MuPDF 提供 AGPL v3 / Artifex 商业许可选择，原许可条件保持不变。",
            "PyInstaller 的 GPL 许可含 bootloader distribution exception；它属于构建工具。", "",
            "下表来自已安装构建/运行依赖闭包，不等于逐字节 EXE 组件审计。版本由实际 metadata 生成。", "",
            "| Python component | Version | Upstream license metadata |", "| --- | --- | --- |"]
    for item in dependencies["packages"]:
        license_text = " ".join(str(item["license"]).split()).replace("|", "\\|")
        if len(license_text) > 300:
            license_text = "Original license files are preserved under docs/licenses/python; see requirements-build.json metadata"
        body.append(f"| {item['name']} | {item['version']} | {license_text} |")
    body += ["", "已安装包附带的原始许可文件复制至 `docs/licenses/python/`；完整清单及 SHA-256 见 `docs/licenses/index.json`。", "",
             "官方 Harness、内嵌 Node 与金融插件依赖的 npm/native 闭包许可按上游文件保留在 `runtime/finance-carrier` 资产中；",
             "这些文件受同一 runtime manifest 哈希约束，构建/交付不会改写其许可。", "",
             f"Harness production manifest format: {runtime['format_version']}; declared upstream license files: {len(node_licenses)}.", "",
             "不打入运行历史、个人 Token、真实研报、约 8.9GB 外部语料或 runtime/tooling。演示 PDF 为本项目虚构生成。", "",
             "本文记录组件和原始许可来源，不声称已取得 Artifex 商业许可。"]
    atomic_write(root / "docs" / "THIRD_PARTY_NOTICES.md", "\n".join(body) + "\n")
    atomic_write(root / "docs" / "licenses" / "index.json", json.dumps({"python": license_records, "runtime": node_licenses}, ensure_ascii=False, indent=2) + "\n")


def build_inputs(root: Path, asset_paths: list[Path]) -> list[dict]:
    files = set(asset_paths)
    for relative in ("mingjing", "kernel", "build/mingjing_build", "legacy_tools", "web", "rulepack", "samples", "docs", "bench", "scripts", "tests", "frontend"):
        for path in (root / relative).rglob("*"):
            if path.is_file() and not any(part in {"__pycache__", ".git", ".pytest_cache", "node_modules", ".vite"} for part in path.relative_to(root).parts):
                files.add(path)
    for name in ("LICENSE", "MingJing.spec", "app_entry.py", "__init__.py", "pyproject.toml", "requirements-build.lock", "requirements-build.json", "scripts/build_receipt.py", "scripts/build_exe.ps1"):
        files.add(root / name)
    consumer = _transport()
    return [{"path": path.relative_to(root).as_posix(), "bytes": consumer._io_path(path).stat().st_size, "sha256": sha256(path)}
            for path in sorted(files)]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--exe", default="dist/MingJing.exe")
    parser.add_argument("--out", default="dist/MingJing-build.json")
    parser.add_argument("--input-lock", default="dist/build-source-lock.json")
    parser.add_argument("--mode", choices=("onefile", "onedir"), default="onefile")
    args = parser.parse_args()
    try:
        from .build_frontend import verify as verify_frontend
    except ImportError:
        from build_frontend import verify as verify_frontend
    verify_frontend(root)
    runtime, paths = runtime_assets(root)
    dependencies = installed_dependencies(root)
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    lock_path = root / args.input_lock
    if args.prepare:
        paths += prepare_finance_payload(root)
        prepare_notices(root, dependencies, runtime)
        inputs = build_inputs(root, paths)
        lock = {"format_version": 2, "runtime_lock_schema_version": 2, "product": "MingJing", "version": version, "mode": args.mode,
                "inputs": inputs, "source_sha256": canonical_hash(inputs),
                "runtime_manifest_sha256": runtime['manifest_sha256'],
                "runtime_distribution": "finance-carrier-compressed-payload",
                "python_dependency_lock_sha256": dependencies["lock_sha256"]}
        atomic_write(lock_path, json.dumps(lock, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": "build_inputs_frozen", "version": version, "files": len(inputs), "source_sha256": lock["source_sha256"]}))
        return 0
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get('format_version') != 2 or lock.get('runtime_lock_schema_version') != 2:
        raise SystemExit('Legacy build receipt remains read-only; create a new frozen build epoch')
    if lock["inputs"] != build_inputs(root, paths) or lock["version"] != version or lock["mode"] != args.mode:
        raise SystemExit("Build inputs changed during compilation; EXE receipt rejected")
    target = Path(args.exe)
    target = (target if target.is_absolute() else root / target).resolve(strict=True)
    receipt = {**lock, "executable": target.name, "sha256": sha256(target), "bytes": target.stat().st_size,
               "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
               "platform": platform.platform(), "dependencies": {item["name"]: item["version"] for item in dependencies["packages"]},
               "release_status": "candidate_requires_exact_binary_acceptance",
               "limitations": ["No clean Windows VM acceptance is implied", "No byte-identical rebuild or source git commit is fabricated"]}
    atomic_write(root / args.out, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "build_recorded", "version": version, "sha256": receipt["sha256"], "bytes": receipt["bytes"], "source_sha256": lock["source_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
