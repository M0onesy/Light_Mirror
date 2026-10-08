"""Package only an exact, tested stage EXE; never promote formal release gates.

The developer-machine smoke/native receipts are scoped evidence. Human gold,
new holdout and independent Windows acceptance remain explicitly unexecuted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tomllib
import uuid
import zipfile

from scripts.create_core_review import DENIED_NAMES, _canonical, configured_secret_values, scan_bytes
from scripts.package_delivery import sha256, verify_current_build_inputs, verify_smoke_receipt

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = 'https://github.com/M0onesy/Light_Mirror'
DOCS = ('docs/USER_GUIDE.md', 'docs/DELIVERY_GUIDE.md', 'docs/EXE_DELIVERY.md',
        'docs/LICENSE_GUIDE.md', 'docs/THIRD_PARTY.md', 'docs/THIRD_PARTY_NOTICES.md')
SAMPLES = ('samples/demo_draft.md', 'samples/demo_draft_defined.md',
           'samples/corpus/facts.json', 'samples/corpus/demo_annual.pdf')
NOT_EXECUTED = ('human_gold', 'professional_rule_signoff', 'D3_time_holdout',
                'clean_windows_vm', 'full_report_financial_acceptance',
                'kimi_real_api', 'openai_real_api', 'formal_G0_G1_G4_G5_G6_G7')


def read_receipt(path: Path) -> dict:
    _canonical(path)
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Receipt must be a JSON object')
    return value


def verify_stage_receipts(exe: Path, build_path: Path, smoke_path: Path,
                          native_path: Path, version: str) -> tuple[dict, dict, dict]:
    for path in (exe, build_path, smoke_path, native_path):
        _canonical(path)
    digest = sha256(exe)
    build, native = read_receipt(build_path), read_receipt(native_path)
    if (build.get('sha256') != digest or native.get('exe_sha256') != digest
            or build.get('bytes') != exe.stat().st_size):
        raise ValueError('Build/native receipts must match the exact EXE hash and size')
    if (build.get('version') != version or native.get('version') != version
            or not build.get('source_sha256') or native.get('source_sha256') != build['source_sha256']
            or not build.get('runtime_manifest_sha256')
            or native.get('runtime_manifest_sha256') != build['runtime_manifest_sha256']):
        raise ValueError('Build/native version, source epoch and runtime lock must match')
    if build.get('mode') != 'onefile':
        raise ValueError('The stage has one EXE entry; onedir is not this artifact')
    if (native.get('schema') != 'mingjing-stage-native-exe.v1'
            or native.get('status') != 'passed_scoped_native_agent_and_exe'):
        raise ValueError('A passing scoped stage-native receipt is required')
    checks = native.get('checks', {})
    agent = checks.get('native_agent', {})
    if (agent.get('status') != 'passed' or agent.get('native_runtime') is not True
            or agent.get('tool_roundtrip') is not True):
        raise ValueError('Native Agent tool feedback loop was not verified')
    if (checks.get('real_api', {}).get('model', {}).get('status') != 'success'
            or checks.get('secret_scan') != 'passed'
            or checks.get('user_profile_unchanged') is not True
            or checks.get('normal_shutdown') != 'passed'
            or checks.get('capabilities', {}).get('pdf') is not True
            or checks.get('capabilities', {}).get('harness') is not True):
        raise ValueError('Scoped real API, capabilities, credentials or lifecycle evidence is missing')
    replays = checks.get('replays', [])
    for mode in ('kernel', 'agent'):
        rows = [row for row in replays if row.get('mode') == mode]
        if (len(rows) != 1 or rows[0].get('status') != 'passed'
                or rows[0].get('numeric_equal') is not True or rows[0].get('network_calls') != 0
                or (mode == 'agent' and rows[0].get('real_harness_loop') is not True)):
            raise ValueError('Scoped offline kernel and native Agent replay must both pass')
    smoke = verify_smoke_receipt(smoke_path, digest)
    return build, smoke, native


def scan_embedded_exe(exe: Path, root: Path, values: list[bytes]) -> dict:
    """Scan decompressed onefile/PYZ/carrier members, not only compressed EXE."""
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(exe))
    entries = {name.replace('\\', '/'): name for name in archive.toc}
    required = {'LICENSE', 'docs/THIRD_PARTY_NOTICES.md', 'docs/licenses/index.json',
                'runtime/harness/source_snapshot.json',
                'runtime/finance-carrier/finance-carrier.payload.zip'}
    if not required <= entries.keys():
        raise ValueError('EXE omits project/runtime provenance or licence resources')
    if archive.extract(entries['LICENSE']) != (root / 'LICENSE').read_bytes():
        raise ValueError('Embedded project licence differs from this source epoch')
    if (archive.extract(entries['runtime/harness/source_snapshot.json'])
            != (root / 'runtime/harness/source_snapshot.json').read_bytes()):
        raise ValueError('Embedded Harness source snapshot differs from current provenance')
    members = modules = payload_members = 0
    harness_license = (root / 'vendor/harness/LICENSE').read_bytes()
    harness_license_verified = False
    for name, original in entries.items():
        entry = archive.toc[original]
        if entry[-1] == 'z':
            pyz = archive.open_embedded_archive(original)
            for module in pyz.toc:
                data = pyz.extract(module, raw=True)
                if data is not None:
                    scan_bytes(data, values, 'EXE/PYZ/' + module)
                modules += 1
            continue
        data = archive.extract(original)
        if data is None:
            continue
        scan_bytes(data, values, 'EXE/' + name)
        members += 1
        if name == 'runtime/finance-carrier/finance-carrier.payload.zip':
            with zipfile.ZipFile(io.BytesIO(data)) as payload:
                if payload.testzip() is not None:
                    raise ValueError('Embedded carrier CRC verification failed')
                names = set(payload.namelist())
                if not {'licenses/DEEPSEEK_HARNESS_LICENSE', 'licenses/NODE_LICENSE'} <= names:
                    raise ValueError('Embedded carrier omits Harness/Node licence')
                if payload.read('licenses/DEEPSEEK_HARNESS_LICENSE') != harness_license:
                    raise ValueError('Embedded Harness licence differs from locked upstream')
                harness_license_verified = True
                for member in payload.infolist():
                    if not member.is_dir():
                        scan_bytes(payload.read(member), values, 'EXE/carrier/' + member.filename)
                        payload_members += 1
    if not harness_license_verified:
        raise ValueError('Harness licence was not verified in the exact EXE payload')
    return {'status': 'passed', 'archive_members': members, 'pyz_modules': modules,
            'carrier_members': payload_members, 'harness_license': 'passed',
            'project_license': 'passed'}


def stage_files(root: Path, exe: Path, build: Path, smoke: Path,
                native: Path) -> list[tuple[Path, str]]:
    files = [(exe, 'MingJing.exe'), (build, 'MingJing-build.json'),
             (smoke, 'acceptance/delivery_smoke.json'),
             (native, 'acceptance/stage_native_exe.json'),
             (root / 'LICENSE', 'LICENSE'),
             (root / 'runtime/harness/source_snapshot.json', 'source/Harness-source-snapshot.json')]
    files += [(root / name, name) for name in (*DOCS, *SAMPLES)]
    licence_root = root / 'docs/licenses'
    _canonical(licence_root)
    for current, directories, names in os.walk(licence_root, followlinks=False):
        for directory in directories:
            _canonical(Path(current) / directory)
        for name in names:
            path = Path(current) / name
            files.append((path, path.relative_to(root).as_posix()))
    names = set()
    for path, member in files:
        _canonical(path)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('Stage input must be a regular file')
        if path.name.lower() in DENIED_NAMES or path.name.lower().startswith('.env'):
            raise ValueError('Configuration/credential files cannot enter stage delivery')
        if path.suffix.lower() == '.pdf' and member != 'samples/corpus/demo_annual.pdf':
            raise ValueError('Only the fictional demonstration PDF can enter stage delivery')
        if member in names:
            raise ValueError('Duplicate stage archive member')
        names.add(member)
    return files


def stream_scan(path: Path, values: list[bytes]) -> None:
    # Header/private-key checks cover textual receipts/docs; exact known keys
    # also cover arbitrary binary resources and block boundaries.
    overlap = max([len(value) for value in values] + [80])
    previous = b''
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            data = previous + block
            scan_bytes(data, values, path.name)
            previous = data[-overlap:]


def generated_files(version: str, build: dict, native: dict,
                    snapshot: dict, source_commit: str | None) -> dict[str, bytes]:
    commit_text = source_commit or '尚未登记；以本包MingJing-build.json逐文件SHA-256核对对应源码'
    readme = f'''# 明镜 {version} 阶段候选

双击 `MingJing.exe` 使用。全部配置、材料路径、模型和金融任务均在本地Web完成；无需安装Python、Node或pnpm。关闭浏览器不退出，请使用Web退出按钮。

详细操作：[docs/USER_GUIDE.md](docs/USER_GUIDE.md)。协作与换机：[docs/DELIVERY_GUIDE.md](docs/DELIVERY_GUIDE.md)。来源与许可：[SOURCE.md](SOURCE.md)、[LICENSE](LICENSE)。外部财报语料不在本包，Token需由当前Windows用户重新配置。

这是 **stage_candidate，formal_release=false**。开发机受限PATH smoke、离线虚构样例及真实研报局部原生Agent走查只代表各自范围，不能当作全文金融质量或独立Windows环境验收。真人金标、专业规则签发、新D3、完整金融全文与Kimi/GPT真实API等均列为not_executed，详见MingJing-manifest.json。

唯一启动入口为EXE。samples是虚构演示；acceptance仅含对应EXE的阶段收据。核心30MB审阅包不代替完整对应源码，源码仓库见SOURCE.md。
'''
    source = f'''# 对应源码与资源

- 项目仓库：{SOURCE_URL}
- 当前应用版本：`{version}`。
- 应用Git commit：`{commit_text}`。
- 实际构建输入锁：`{build['source_sha256']}`，详见MingJing-build.json的inputs（逐文件SHA-256）。
- Harness快照版本：`{snapshot.get('upstream_version')}`。
- Harness源快照：`{snapshot.get('source_sha256')}`。
- 上游commit：`{snapshot.get('git_commit')}`；源归档无commit时保持null，不伪造。
- 原生载体manifest：`{build['runtime_manifest_sha256']}`。

完整源码应从仓库取得，与本包构建输入逐项核对；外部原始财报和个人密钥不公开。构建方法见仓库scripts/REBUILD_SOURCE.md。源码快照清单保存在source/Harness-source-snapshot.json。EXE含项目AGPLv3及Harness/Node/依赖原许可，第三方不因项目许可而改换许可证。

本次原生走查范围：{native.get('scope', '见acceptance/stage_native_exe.json')}。
'''
    return {'README.md': readme.encode('utf-8'), 'SOURCE.md': source.encode('utf-8')}


def create_stage(root: Path, out: Path, *, exe: Path, build_path: Path,
                 smoke_path: Path, native_path: Path,
                 source_commit: str | None = None) -> dict:
    root = _canonical(root)
    out = _canonical(out, exists=False)
    target_zip = out.with_name(out.name + '.zip')
    receipt_path = out.with_name(out.name + '-receipt.json')
    checksum_path = out.with_name(out.name + '.zip.sha256')
    for path in (out, target_zip, receipt_path, checksum_path):
        if path.exists():
            raise ValueError('Refusing to overwrite existing stage artifacts: ' + path.name)
    if source_commit is not None and re.fullmatch(r'[0-9a-fA-F]{40}', source_commit) is None:
        raise ValueError('Source commit must be a full actual Git commit')
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if '.dev' not in version and 'rc' not in version:
        raise ValueError('This command only creates explicitly prerelease stage candidates')
    build, smoke, native = verify_stage_receipts(exe, build_path, smoke_path, native_path, version)
    verify_current_build_inputs(root, build_path)
    sources = stage_files(root, exe, build_path, smoke_path, native_path)
    values = configured_secret_values(root)
    for path, _ in sources:
        stream_scan(path, values)
    embedded = scan_embedded_exe(exe, root, values)
    snapshot = read_receipt(root / 'runtime/harness/source_snapshot.json')
    generated = generated_files(version, build, native, snapshot, source_commit)
    rows = [{'path': member, 'bytes': path.stat().st_size, 'sha256': sha256(path)} for path, member in sources]
    if next(row['sha256'] for row in rows if row['path'] == 'MingJing.exe') != build['sha256']:
        raise ValueError('EXE changed after scoped acceptance was verified')
    for member, data in generated.items():
        scan_bytes(data, values, member)
        rows.append({'path': member, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {'schema': 'mingjing.stage-delivery.v1', 'product': 'MingJing', 'version': version,
                'created_at': datetime.now(timezone.utc).isoformat(), 'entrypoint': 'MingJing.exe',
                'delivery_kind': 'stage_candidate', 'formal_release': False,
                'sha256': build['sha256'], 'source_sha256': build['source_sha256'],
                'runtime_manifest_sha256': build['runtime_manifest_sha256'],
                'source_repository': SOURCE_URL, 'source_commit': source_commit,
                'acceptance_status': native['status'], 'acceptance_scope': native.get('scope'),
                'release_gates': {gate: 'not_certified_by_stage' for gate in ('G0', 'G1', 'G4', 'G5', 'G6', 'G7')},
                'not_executed': list(NOT_EXECUTED),
                'credential_scan': {'status': 'passed', 'references_checked': len(values),
                                    'method': 'allowlist_exact_configured_values_and_decompressed_EXE_PYZ_carrier_ZIP_members',
                                    'embedded': embedded},
                'files': sorted(rows, key=lambda row: row['path'])}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    generated['MingJing-manifest.json'] = manifest_bytes
    sums = [(row['sha256'], row['path']) for row in rows]
    sums.append((hashlib.sha256(manifest_bytes).hexdigest(), 'MingJing-manifest.json'))
    generated['SHA256SUMS.txt'] = ''.join(f'{digest}  {member}\n' for digest, member in sorted(sums, key=lambda row: row[1])).encode('utf-8')
    staging = out.with_name(out.name + '.stage-' + uuid.uuid4().hex)
    temporary_zip = target_zip.with_name(target_zip.name + '.tmp-' + uuid.uuid4().hex)
    staging.mkdir(parents=True)
    try:
        for path, member in sources:
            target = staging / member
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        for member, data in generated.items():
            scan_bytes(data, values, member)
            target = staging / member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for row in rows:
            path = staging / row['path']
            if path.stat().st_size != row['bytes'] or sha256(path) != row['sha256']:
                raise ValueError('Stage source changed while copying')
        # The input epoch must still be current after all copies and scans.
        verify_current_build_inputs(root, build_path)
        verify_stage_receipts(staging / 'MingJing.exe', staging / 'MingJing-build.json',
                              staging / 'acceptance/delivery_smoke.json',
                              staging / 'acceptance/stage_native_exe.json', version)
        with zipfile.ZipFile(temporary_zip, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(p for p in staging.rglob('*') if p.is_file()):
                archive.write(path, path.relative_to(staging).as_posix())
        with zipfile.ZipFile(temporary_zip) as archive:
            expected = {row['path']: row['sha256'] for row in rows}
            expected.update({member: hashlib.sha256(data).hexdigest() for member, data in generated.items()})
            if len(archive.namelist()) != len(expected) or set(archive.namelist()) != expected.keys():
                raise ValueError('Stage ZIP differs from the strict member allowlist')
            if archive.testzip() is not None:
                raise ValueError('Stage ZIP CRC verification failed')
            for member, digest in expected.items():
                data = archive.read(member)
                scan_bytes(data, values, 'ZIP/' + member)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError('Stage ZIP member hash differs')
        # Never replace a destination created by another invocation.
        if any(path.exists() for path in (out, target_zip, receipt_path, checksum_path)):
            raise ValueError('Stage output appeared concurrently; refusing overwrite')
        staging.rename(out)
        temporary_zip.rename(target_zip)
        archive_digest = sha256(target_zip)
        receipt = {'schema': 'mingjing.stage-package-receipt.v1', 'status': 'passed',
                   'version': version, 'delivery_kind': 'stage_candidate', 'formal_release': False,
                   'folder': str(out), 'archive': str(target_zip), 'archive_bytes': target_zip.stat().st_size,
                   'archive_sha256': archive_digest, 'exe_sha256': build['sha256'],
                   'source_sha256': build['source_sha256'], 'files': len(expected),
                   'credential_scan': manifest['credential_scan'], 'not_executed': list(NOT_EXECUTED)}
        with receipt_path.open('x', encoding='utf-8') as handle:
            handle.write(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        with checksum_path.open('x', encoding='utf-8') as handle:
            handle.write(archive_digest + '  ' + target_zip.name + '\n')
        return receipt
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        temporary_zip.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=ROOT / 'dist/MingJing.exe')
    parser.add_argument('--build-receipt', type=Path, default=ROOT / 'dist/MingJing-build.json')
    parser.add_argument('--smoke-receipt', type=Path, default=ROOT / 'runs/stage-exe-smoke/smoke.json')
    parser.add_argument('--native-receipt', type=Path, default=ROOT / 'runs/stage-native-exe/acceptance.json')
    parser.add_argument('--source-commit')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    out = args.out or ROOT.parents[1] / ('MingJing-stage-' + version)
    receipt = create_stage(ROOT, out, exe=args.exe.resolve(), build_path=args.build_receipt.resolve(),
                           smoke_path=args.smoke_receipt.resolve(), native_path=args.native_receipt.resolve(),
                           source_commit=args.source_commit)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
