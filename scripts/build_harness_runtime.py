"""Build the pinned source archive through the official dsh Windows SEA route."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import stat
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/harness"
PNPM = ROOT / "runtime/tooling/node_modules/pnpm/bin/pnpm.cjs"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def windows_path(path: Path) -> Path:
    value = str(path.absolute())
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        return Path('\\\\?\\' + value)
    return path


def write_manifest(receipt: dict, node: str) -> dict:
    root = windows_path(ROOT / 'runtime')
    payload = []
    for directory in ('harness', 'finance-plugin'):
        for path in sorted((root / directory).rglob('*')):
            if not path.is_file() or (directory == 'harness' and path.name == 'manifest.json'):
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise RuntimeError('Production runtime assets cannot be links or reparse points')
            payload.append({'path': path.relative_to(root).as_posix(), 'sha256': sha(path), 'bytes': info.st_size})
    result = {'format_version': 2, 'harness_version': receipt['upstream_version'], 'git_commit': None,
              'source_sha256': receipt['source_sha256'], 'plugin_sha256': sha(root / 'finance-plugin/index.mjs'),
              'node_version': (root / 'harness/licenses/node/version.txt').read_text().strip(),
              'build_node_version': subprocess.check_output([node, '--version'], text=True).strip(),
              'pnpm_version': '11.7.0', 'executable': 'deepseek-harness-sdk-runtime-win-x64.exe', 'files': payload}
    (root / 'harness/manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def collect_licenses() -> None:
    """Keep original notices for the exact deployed closure, not a guess list."""
    sys.path.insert(0, str(ROOT))
    from mingjing.harness_client import HarnessRuntime
    runtime = HarnessRuntime(ROOT / 'runtime/build-logs/license-probe-home', lambda *_: {})
    try:
        node_version = runtime.start()['node_version']
    finally:
        runtime.close()
    destination = windows_path(ROOT / 'runtime/harness/licenses')
    destination.mkdir(exist_ok=True)
    shutil.copy2(UPSTREAM / 'LICENSE', destination / 'DEEPSEEK_HARNESS_LICENSE')
    node_dir = destination / 'node'
    node_dir.mkdir(exist_ok=True)
    url = f'https://raw.githubusercontent.com/nodejs/node/{node_version}/LICENSE'
    with urllib.request.urlopen(url, timeout=40) as response:
        license_text = response.read()
    if not license_text.startswith(b'Node.js is licensed'):
        raise RuntimeError('The exact embedded Node original LICENSE could not be verified')
    (node_dir / 'LICENSE').write_bytes(license_text)
    (node_dir / 'version.txt').write_text(node_version + '\n', encoding='utf-8')
    (node_dir / 'source.json').write_text(json.dumps({'url': url, 'version': node_version, 'sha256': hashlib.sha256(license_text).hexdigest()}, indent=2) + '\n', encoding='utf-8')
    for package in ('@yao-pkg/pkg', '@yao-pkg/pkg-fetch'):
        installed = UPSTREAM / 'node_modules' / package
        if installed.is_dir():
            out = destination / 'build-tools' / package
            out.mkdir(parents=True, exist_ok=True)
            for path in installed.iterdir():
                if path.is_file() and (path.name.lower().startswith(('license', 'copying', 'notice')) or path.name == 'package.json'):
                    shutil.copy2(path, out / path.name)
    deployed = windows_path(UPSTREAM / 'python/sdk-runtime/src/deepseek_harness_runtime/runtime/node/node_modules')
    inventory = []
    notices = []
    for path in sorted(deployed.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(deployed)
        lower = path.name.lower()
        if lower.startswith(('license', 'copying', 'notice', 'copyright')) or path.name == 'package.json':
            out = destination / 'npm' / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out)
            notices.append({'path': out.relative_to(destination).as_posix(), 'sha256': sha(out), 'bytes': out.stat().st_size})
            if path.name == 'package.json':
                data = json.loads(path.read_text(encoding='utf-8'))
                if data.get('name') and data.get('version'):
                    inventory.append({'path': relative.as_posix(), 'name': data['name'], 'version': data['version'], 'license': data.get('license'), 'package_sha256': sha(path)})
    (destination / 'package_inventory.json').write_text(json.dumps({'node_version': node_version, 'packages': inventory, 'original_notice_files': notices}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def snapshot() -> dict:
    excluded = {"node_modules", ".git", "lib", "dist", "dist-exe", ".dsh-build", "__pycache__", ".cache", "build", "target", ".venv"}
    files = []
    command = ["rg", "--files", "--hidden", "--no-ignore"]
    for name in excluded:
        command.extend(["-g", f"!**/{name}/**"])
    paths = subprocess.check_output(command, cwd=UPSTREAM, text=True, encoding="utf-8").splitlines()
    for relative in paths:
        path = UPSTREAM / relative
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or path.name.endswith((".tsbuildinfo", ".pyc")):
            continue
        files.append({"path": path.relative_to(UPSTREAM).as_posix(), "sha256": sha(path), "bytes": info.st_size})
        if len(files) % 1000 == 0:
            print(f"Pinned source files: {len(files)}", flush=True)
    files.sort(key=lambda entry: entry["path"])
    encoded = json.dumps(files, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {"kind": "source_archive_snapshot", "upstream_version": json.loads((UPSTREAM / "package.json").read_text(encoding="utf-8"))["version"],
            "git_commit": None, "source_sha256": hashlib.sha256(encoded).hexdigest(),
            "pnpm_lock_sha256": sha(UPSTREAM / "pnpm-lock.yaml"),
            "local_packaging_changes": ["scripts/client-build-environment.ts: explicit source archive metadata, no fabricated commit"],
            "files": files}


def main() -> None:
    global UPSTREAM
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', type=Path, default=UPSTREAM)
    parser.add_argument('--legacy-sea', action='store_true', help='Build the historical full SDK SEA; not the MingJing finance carrier')
    parser.add_argument('--node', type=Path, default=Path(shutil.which('node') or 'node'))
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--snapshot-only", action="store_true")
    parser.add_argument("--reuse-snapshot", action="store_true")
    parser.add_argument("--copy-only", action="store_true")
    parser.add_argument("--manifest-only", action="store_true")
    parser.add_argument("--licenses-only", action="store_true")
    args = parser.parse_args()
    UPSTREAM = args.upstream.resolve(strict=True)
    sys.path.insert(0, str(ROOT))
    from scripts.upstream_source import verify_upstream, locked_source
    verify_upstream(UPSTREAM)
    if not args.legacy_sea and not args.snapshot_only:
        from scripts.stage_finance_runtime import stage_runtime
        if not (UPSTREAM / 'python/sdk-runtime/src/deepseek_harness_runtime/runtime/node/node_modules').is_dir():
            raise RuntimeError('Build the pinned upstream SDK Node carrier first (see scripts/REBUILD_SOURCE.md); no SEA is built by default')
        manifest = stage_runtime(UPSTREAM, ROOT / 'runtime/finance-carrier/staged', args.node)
        print(json.dumps({'status': manifest['status'], 'bytes': manifest['total_bytes'], 'runtime_acceptance': 'not_executed'}, ensure_ascii=False))
        return
    root = ROOT / "runtime/harness"
    root.mkdir(parents=True, exist_ok=True)
    snapshot_file = root / "source_snapshot.json"
    receipt = locked_source()
    if args.snapshot_only:
        print(json.dumps({key: value for key, value in receipt.items() if key != "files"}, ensure_ascii=False))
        return
    node = shutil.which("node")
    if not node or not PNPM.is_file():
        raise RuntimeError("Install Node 24 and the pinned pnpm 11.7.0 toolchain before building")
    if args.licenses_only:
        collect_licenses()
        result = write_manifest(receipt, node)
        print(json.dumps({key: value for key, value in result.items() if key != 'files'}, ensure_ascii=False))
        return
    if args.manifest_only:
        result = write_manifest(receipt, node)
        print(json.dumps({key: value for key, value in result.items() if key != 'files'}, ensure_ascii=False))
        return
    env = dict(os.environ)
    env["DSH_SOURCE_ARCHIVE_SHA256"] = receipt["source_sha256"]
    env["npm_execpath"] = str(PNPM)
    env["PATH"] = str(PNPM.parents[2] / ".bin") + os.pathsep + env.get("PATH", "")
    command = [node, str(PNPM), "exec", "tsx", "scripts/build-exe-for-python-sdk.ts", "--targets=node24-win-x64"]
    if args.skip_build:
        command.append("--skip-build")
    logs = ROOT / "runtime/build-logs"
    logs.mkdir(exist_ok=True)
    if not args.copy_only:
        with (logs / "build.log").open("w", encoding="utf-8") as stream:
            process = subprocess.run(command, cwd=UPSTREAM, env=env, stdout=stream, stderr=subprocess.STDOUT)
        if process.returncode:
            print((logs / "build.log").read_text(encoding="utf-8", errors="replace")[-16000:])
            raise RuntimeError(f"Official Harness build failed ({process.returncode}); inspect runtime/build-logs/build.log")
    runtime = windows_path(UPSTREAM / "dist-exe")
    stem = "deepseek-harness-sdk-runtime-win-x64"
    for name in (stem + ".exe", stem + "-rg.exe"):
        shutil.copy2(runtime / name, windows_path(root / name))
    office = runtime / (stem + "-office")
    shutil.copytree(office, windows_path(root / office.name), dirs_exist_ok=True)
    collect_licenses()
    result = write_manifest(receipt, node)
    print(json.dumps({key: value for key, value in result.items() if key != "files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
