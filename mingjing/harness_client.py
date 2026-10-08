"""Private stdio client for the official dsh profile launcher, without an Agent loop."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Any, Callable


class HarnessError(RuntimeError):
    """Runtime/transport failure with no credential-bearing diagnostic payload."""


def _io_path(path: Path) -> Path:
    """Use Windows extended paths for complete Office payloads and cache roots."""
    value = str(path.absolute())
    return Path("\\\\?\\" + value) if os.name == "nt" and not value.startswith("\\\\?\\") else path


def _regular_path(path: Path, root: Path) -> None:
    """Reject links/reparse points and paths outside an application-owned root."""
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HarnessError("Runtime resource escaped its allowed root") from exc
    current = path
    while current != root.parent:
        local = _io_path(current)
        if local.exists() or local.is_symlink():
            info = local.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise HarnessError("Linked runtime resources are not permitted")
        current = current.parent


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with _io_path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class _IntegrityState:
    """Mutable verification state must never become a semantic code constant."""

    def __init__(self):
        self.lock = threading.RLock()
        self.files: dict[tuple[str, str], dict[str, tuple[int, ...]]] = {}
        self.packages: set[tuple[str, str]] = set()
        self.windows_api = None


_integrity_state = _IntegrityState()


def _windows_change_time(path: Path) -> int | None:
    """Read NTFS metadata change time; Python's Windows ctime is birth time.

    If the filesystem/API cannot supply this stamp, the caller hashes content
    again. It must never treat birth time or a failed query as safe memo proof.
    """
    try:
        if _integrity_state.windows_api is None:
            import ctypes
            from ctypes import wintypes

            class FileBasicInfo(ctypes.Structure):
                _fields_ = [('CreationTime', ctypes.c_longlong), ('LastAccessTime', ctypes.c_longlong),
                            ('LastWriteTime', ctypes.c_longlong), ('ChangeTime', ctypes.c_longlong),
                            ('FileAttributes', wintypes.DWORD)]

            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            create = kernel.CreateFileW
            create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
            create.restype = wintypes.HANDLE
            query = kernel.GetFileInformationByHandleEx
            query.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
            query.restype = wintypes.BOOL
            close = kernel.CloseHandle
            close.argtypes = (wintypes.HANDLE,); close.restype = wintypes.BOOL
            _integrity_state.windows_api = (ctypes, FileBasicInfo, create, query, close)
        ctypes, info_type, create, query, close = _integrity_state.windows_api
        # FILE_READ_ATTRIBUTES; all share modes; OPEN_EXISTING;
        # FILE_FLAG_OPEN_REPARSE_POINT, so a link is not followed implicitly.
        handle = create(str(_io_path(path)), 0x80, 7, None, 3, 0x00200000, None)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return None
        try:
            information = info_type()
            if not query(handle, 0, ctypes.byref(information), ctypes.sizeof(information)):
                return None
            value = int(information.ChangeTime)
            return value if value > 0 else None
        finally:
            close(handle)
    except (OSError, AttributeError, TypeError, ValueError):
        return None


def _manifest_entries(entries: Any) -> set[str]:
    if not isinstance(entries, list) or not entries:
        raise HarnessError("Runtime manifest has no files")
    seen: set[str] = set()
    folded: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise HarnessError("Unsafe runtime manifest entry")
        relative = PurePosixPath(entry["path"])
        value = relative.as_posix()
        if (relative.is_absolute() or not relative.parts or value != entry["path"]
                or any(part in (".", "..") or ":" in part or "\\" in part for part in relative.parts)
                or value.casefold() in folded or type(entry.get("bytes")) is not int or entry["bytes"] < 0
                or not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in entry["sha256"])):
            raise HarnessError("Unsafe runtime manifest entry")
        seen.add(value)
        folded.add(value.casefold())
    return seen


def _verify_files(root: Path, entries: list[dict], manifest_name: str, raw: bytes) -> None:
    """Recheck the tree on every use; reuse content checks for unchanged files.

    The memo is process-local and includes Windows file identity/change stamps.
    Missing, added, linked or normally modified files fail before credentials.
    Nothing is trusted merely because a previous session validated its root.
    """
    root = root.absolute()
    expected = _manifest_entries(entries)
    key = (str(root), hashlib.sha256(raw).hexdigest())
    with _integrity_state.lock:
        try:
            _regular_path(root, root)
            observed = set()
            observed_info = {}
            for current_dir, folders, names in os.walk(_io_path(root), followlinks=False):
                parent = Path(current_dir)
                for name in folders + names:
                    path = parent / name
                    info = path.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                        raise HarnessError('Linked runtime resources are not permitted')
                    if stat.S_ISREG(info.st_mode):
                        relative = path.relative_to(_io_path(root)).as_posix()
                        observed.add(relative)
                        observed_info[relative] = info
            if observed != expected | {manifest_name}:
                raise HarnessError("Runtime file inventory differs from its dependency lock")
            if _io_path(root / manifest_name).read_bytes() != raw:
                raise HarnessError("Runtime manifest changed during validation")
            previous = _integrity_state.files.get(key, {})
            current = {}
            for entry in entries:
                path = root / entry["path"]
                info = observed_info[entry['path']]
                changed = _windows_change_time(path) if os.name == 'nt' else info.st_ctime_ns
                stamp = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino,
                         info.st_mode, getattr(info, "st_file_attributes", 0), changed)
                if info.st_size != entry["bytes"]:
                    raise HarnessError("Runtime resource size differs from its dependency lock")
                if (changed is None or previous.get(entry["path"]) != stamp) and _file_hash(path) != entry["sha256"]:
                    raise HarnessError("Runtime resource SHA-256 differs from its dependency lock")
                current[entry["path"]] = stamp
            if len(_integrity_state.files) >= 8 and key not in _integrity_state.files:
                _integrity_state.files.pop(next(iter(_integrity_state.files)))
            _integrity_state.files[key] = current
        except (OSError, HarnessError):
            _integrity_state.files.pop(key, None)
            raise


def verify_finance_carrier(root: Path) -> dict:
    """Validate the actual compiled JS/JSON/native closure and npm resolution."""
    root = Path(root).expanduser().absolute()
    manifest_path = root / "carrier_manifest.json"
    _regular_path(manifest_path, root)
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        if (manifest.get("format_version") != 1 or manifest.get("kind") != "mingjing-finance-carrier"
                or manifest.get("platform") != "win32-x64" or manifest.get("bootstrap") != "bootstrap.mjs"
                or manifest.get("node") != "bin/node.exe"):
            raise HarnessError("Unsupported finance carrier manifest")
        files = _manifest_entries(manifest.get("files"))
        required = {"bootstrap.mjs", "bin/node.exe", "finance-plugin/index.mjs", "finance-plugin/package.json"}
        if not required <= files:
            raise HarnessError("Finance carrier lacks its owned launcher or plugin")
        _verify_files(root, manifest["files"], "carrier_manifest.json", raw)
        cache_key = (str(root), hashlib.sha256(raw).hexdigest())
        # Once content has been checked again, unchanged JSON and the same
        # manifest imply the same npm resolution graph. Avoid rereading all
        # package manifests for every credential and each child session.
        with _integrity_state.lock:
            if cache_key in _integrity_state.packages:
                return {**manifest, "manifest_sha256": cache_key[1]}
        packages = manifest.get("package_map")
        if not isinstance(packages, list) or not packages:
            raise HarnessError("Finance carrier lacks the compiled package map")
        by_path = {}
        for package in packages:
            path = package.get("path")
            name = package.get("name")
            if not isinstance(path, str) or not isinstance(name, str) or path in by_path:
                raise HarnessError("Unsafe compiled package map")
            if not path.startswith("node_modules/") or not path.endswith("node_modules/" + name) or path + "/package.json" not in files:
                raise HarnessError("Compiled package path escaped its npm deployment")
            data = json.loads(_io_path(root / path / "package.json").read_bytes())
            if data.get("name") != name or data.get("version") != package.get("version"):
                raise HarnessError("Compiled package identity differs from its dependency lock")
            by_path[path] = package
        for path, package in by_path.items():
            mapping = package.get("dependencies")
            if not isinstance(mapping, dict):
                raise HarnessError("Compiled package dependency map is invalid")
            for name, target in mapping.items():
                if target not in by_path or by_path[target]["name"] != name:
                    raise HarnessError("Compiled package dependency points outside its lock")
                owner = root / path
                found = None
                while owner.is_relative_to(root):
                    candidate = owner / "node_modules" / name
                    if _io_path(candidate / "package.json").is_file():
                        found = candidate.relative_to(root).as_posix()
                        break
                    if owner == root:
                        break
                    owner = owner.parent
                if found != target:
                    raise HarnessError("Compiled package resolution differs from its dependency lock")
        declared = next(entry["sha256"] for entry in manifest["files"] if entry["path"] == "finance-plugin/index.mjs")
        if manifest.get("plugin_sha256") != declared:
            raise HarnessError("Finance plugin lock is inconsistent")
        with _integrity_state.lock:
            if len(_integrity_state.packages) >= 8:
                _integrity_state.packages.pop()
            _integrity_state.packages.add(cache_key)
        return {**manifest, "manifest_sha256": hashlib.sha256(raw).hexdigest()}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HarnessError("Finance carrier dependency lock is invalid or unavailable") from exc


def _read_finance_manifest(root: Path) -> tuple[dict, bytes]:
    """Read distribution metadata without copying or trusting executable files."""
    path = root / 'carrier_manifest.json'
    _regular_path(path, root)
    try:
        raw = _io_path(path).read_bytes()
        manifest = json.loads(raw)
        files = _manifest_entries(manifest.get('files'))
        if (type(manifest.get('format_version')) is not int or manifest['format_version'] != 1 or manifest.get('kind') != 'mingjing-finance-carrier'
                or manifest.get('platform') != 'win32-x64' or manifest.get('node') != 'bin/node.exe'
                or manifest.get('bootstrap') != 'bootstrap.mjs'
                or not {'bin/node.exe', 'bootstrap.mjs', 'finance-plugin/index.mjs', 'finance-plugin/package.json'} <= files
                or not isinstance(manifest.get('package_map'), list) or not manifest['package_map']
                or type(manifest.get('package_count')) is not int or manifest['package_count'] <= 0
                or type(manifest.get('installed_package_count')) is not int
                or manifest.get('installed_package_count') != len(manifest['package_map'])
                or type(manifest.get('total_bytes')) is not int
                or manifest.get('total_bytes') != sum(item['bytes'] for item in manifest['files'])):
            raise ValueError('Unsupported finance distribution')
        plugin = next(item['sha256'] for item in manifest['files'] if item['path'] == 'finance-plugin/index.mjs')
        if plugin != manifest.get('plugin_sha256'):
            raise ValueError('Inconsistent finance plugin identity')
        return {**manifest, 'manifest_sha256': hashlib.sha256(raw).hexdigest()}, raw
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HarnessError('Finance distribution manifest is invalid or unavailable') from exc


class _RuntimeCacheLock:
    """Use an OS lease to publish one complete cache generation at a time."""

    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        self.stream = self.path.open('a+b')
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b'0')
            self.stream.flush()
        if os.name == 'nt':
            import msvcrt
            deadline = time.monotonic() + 300
            while True:
                self.stream.seek(0)
                try:
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.stream.close()
                        raise HarnessError('Another process is preparing the finance runtime')
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        assert self.stream
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()


def _copy_checked_stream(stream, target: Path, entry: dict) -> None:
    _io_path(target.parent).mkdir(parents=True, exist_ok=True)
    count, digest = 0, hashlib.sha256()
    with _io_path(target).open('xb') as output:
        while block := stream.read(1024 * 1024):
            count += len(block)
            if count > entry['bytes']:
                raise HarnessError('Finance runtime resource exceeds its declared size')
            digest.update(block)
            output.write(block)
    if count != entry['bytes'] or digest.hexdigest() != entry['sha256']:
        raise HarnessError('Finance runtime resource differs from its dependency lock')


def _previous_verified_carrier(base: Path, manifest: dict) -> tuple[Path, dict] | None:
    """Reuse only immutable application cache bytes, never mutable source links."""
    expected = {entry['path']: (entry['sha256'], entry['bytes']) for entry in manifest['files']}
    candidates = []
    for root in base.iterdir():
        if len(root.name) != 64 or any(char not in '0123456789abcdef' for char in root.name) or not root.is_dir():
            continue
        try:
            _regular_path(root, base)
            previous, _ = _read_finance_manifest(root)
            if previous['manifest_sha256'] != root.name:
                continue
            shared = {entry['path']: entry for entry in previous['files']
                      if expected.get(entry['path']) == (entry['sha256'], entry['bytes'])}
            if shared:
                candidates.append((root, shared, sum(entry['bytes'] for entry in shared.values())))
        except (OSError, HarnessError):
            continue
    for root, shared, _ in sorted(candidates, key=lambda item: (item[2], len(item[1])), reverse=True):
        try:
            verify_finance_carrier(root)
            return root, shared
        except (OSError, HarnessError):
            # An unselected historical cache is not an execution dependency.
            # A selected current cache, above, always fails closed on damage.
            continue
    return None


def extract_finance_carrier(source: Path, cache_base: Path | None = None) -> Path:
    """Deploy once from a raw source carrier or a one-file embedded payload.

    Every later source/frozen start resolves the same immutable SHA generation.
    PyInstaller distribution paths never become session or replay identities.
    """
    from .config import app_root, validate_path
    source = validate_path(source, must_exist=True, kind='dir', follow_symlinks=False)
    manifest, raw = _read_finance_manifest(source)
    base = validate_path(cache_base or app_root() / 'runtime-cache' / 'finance', kind='dir', follow_symlinks=False)
    base.mkdir(parents=True, exist_ok=True)
    locks = base / '.locks'
    _regular_path(locks, base)
    locks.mkdir(exist_ok=True)
    lock_path = locks / (manifest['manifest_sha256'] + '.lock')
    _regular_path(lock_path, base)
    target = base / manifest['manifest_sha256']
    with _RuntimeCacheLock(lock_path):
        if target.exists():
            _regular_path(target, base)
            checked = verify_finance_carrier(target)
            if checked['manifest_sha256'] != manifest['manifest_sha256']:
                raise HarnessError('Cached finance runtime manifest differs from its source lock')
            return target
        stage = Path(tempfile.mkdtemp(prefix='.' + manifest['manifest_sha256'][:16] + '-', dir=base))
        try:
            archive_path = source / 'finance-carrier.payload.zip'
            if archive_path.is_file():
                descriptor_path = source / 'payload.json'
                _regular_path(archive_path, source)
                _regular_path(descriptor_path, source)
                descriptor = json.loads(descriptor_path.read_bytes())
                if (type(descriptor.get('format_version')) is not int or descriptor['format_version'] != 1
                        or descriptor.get('carrier_manifest_sha256') != manifest['manifest_sha256']
                        or type(descriptor.get('bytes')) is not int
                        or descriptor.get('payload') != archive_path.name or descriptor.get('bytes') != archive_path.stat().st_size
                        or descriptor.get('sha256') != _file_hash(archive_path)):
                    raise HarnessError('Embedded finance payload differs from its build lock')
                entries = {item['path']: item for item in manifest['files']}
                with zipfile.ZipFile(_io_path(archive_path)) as archive:
                    infos = archive.infolist()
                    if len(infos) != len(entries) or {item.filename for item in infos} != set(entries):
                        raise HarnessError('Embedded finance payload inventory differs from its lock')
                    for item in infos:
                        mode = item.external_attr >> 16
                        if item.is_dir() or stat.S_ISLNK(mode) or item.file_size != entries[item.filename]['bytes']:
                            raise HarnessError('Linked or invalid embedded finance resource')
                        with archive.open(item) as stream:
                            _copy_checked_stream(stream, stage / item.filename, entries[item.filename])
            else:
                previous = _previous_verified_carrier(base, manifest)
                for entry in manifest['files']:
                    output = stage / entry['path']
                    if previous is not None and entry['path'] in previous[1]:
                        _io_path(output.parent).mkdir(parents=True, exist_ok=True)
                        try:
                            os.link(_io_path(previous[0] / entry['path']), _io_path(output))
                            continue
                        except OSError:
                            # Filesystems without hardlinks use the same
                            # checked-copy path; no symlink fallback exists.
                            with _io_path(previous[0] / entry['path']).open('rb') as stream:
                                _copy_checked_stream(stream, output, entry)
                            continue
                    original = source / entry['path']
                    _regular_path(original, source)
                    with _io_path(original).open('rb') as stream:
                        _copy_checked_stream(stream, output, entry)
            (stage / 'carrier_manifest.json').write_bytes(raw)
            verify_finance_carrier(stage)
            os.replace(stage, target)
            return target
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            raise HarnessError('Finance runtime cache preparation failed') from exc
        finally:
            if stage.exists():
                if stage.parent != base or not stage.name.startswith('.' + manifest['manifest_sha256'][:16] + '-'):
                    raise HarnessError('Unsafe finance staging cleanup path')
                _regular_path(stage, base)
                shutil.rmtree(_io_path(stage))


def _native_cache_root(config: dict) -> Path:
    from .config import app_root, validate_path
    source = _finance_carrier(_source_resource_root(), config)
    if source is None:
        raise HarnessError('The locked finance carrier is missing')
    manifest, _ = _read_finance_manifest(source)
    root = validate_path(app_root() / 'runtime-cache' / 'native' / manifest['manifest_sha256'], kind='dir', follow_symlinks=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _verify_native_cache(root: Path, carrier: Path, *, verified_manifest: dict | None = None) -> None:
    """Permit only source-matching Windows native copies owned by MingJing."""
    if verified_manifest is None:
        manifest = verify_finance_carrier(carrier)
    else:
        # Internal callers pass the complete result of a tree validation in
        # this same guard, never a persisted/Boolean 'passed' marker. Recheck
        # the small manifest itself so its identity cannot change in between.
        manifest, _ = _read_finance_manifest(carrier)
        if not isinstance(verified_manifest, dict) or manifest != verified_manifest:
            raise HarnessError('Native cache guard received a different verified carrier manifest')
    expected = {}
    for entry in manifest['files']:
        if not entry['path'].endswith('.node'):
            continue
        owners = [item for item in manifest['package_map'] if entry['path'].startswith(item['path'] + '/')]
        if not owners:
            raise HarnessError('Native resource has no locked package owner')
        package = max(owners, key=lambda item: len(item['path']))
        name = package['name'].lstrip('@').replace('/', '-')
        expected[f"{name}/{package['version']}/win32-x64-msvc/{PurePosixPath(entry['path']).name}"] = entry['sha256']
    _regular_path(root, root)
    for current_dir, folders, names in os.walk(_io_path(root), followlinks=False):
        for name in folders + names:
            path = Path(current_dir) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise HarnessError('Linked native runtime cache is not permitted')
            if stat.S_ISREG(info.st_mode):
                relative = path.relative_to(_io_path(root)).as_posix()
                if relative not in expected or _file_hash(path) != expected[relative]:
                    raise HarnessError('Native runtime cache differs from its locked package source')


def _finance_carrier(source: Path, config: dict) -> Path | None:
    explicit = config.get("finance_carrier_root")
    if explicit:
        if not isinstance(explicit, str):
            raise HarnessError("Finance carrier directory must be a string")
        return Path(explicit).expanduser().absolute()
    candidate = source / "finance-carrier" / "staged"
    if (candidate / "carrier_manifest.json").is_file():
        return candidate
    bundled = source / 'finance-carrier'
    return bundled if (bundled / 'carrier_manifest.json').is_file() else None


def _source_resource_root() -> Path:
    return (Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
            if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]) / 'runtime'


def _configured_custom(config: dict) -> str | None:
    legacy = config.get('dsh_bin')
    if legacy:
        return str(legacy)
    nested = config.get('harness') or {}
    if (isinstance(nested, dict) and nested.get('runtime_mode', 'embedded') == 'external'
            and nested.get('enabled') is True and nested.get('executable')):
        return str(nested['executable'])
    return None


def _launch_identity(source: Path, config: dict, manifest: dict | None) -> dict:
    custom = _configured_custom(config)
    if custom:
        raw = Path(custom).expanduser().absolute()
        _regular_path(raw, raw.parent)
        executable = raw.resolve()
        if not executable.is_file():
            return {'kind': 'external_executable_missing', 'path': str(executable)}
        return {'kind': 'external_executable', 'path': str(executable), 'sha256': _file_hash(executable)}
    carrier = _finance_carrier(source, config)
    if carrier is not None:
        lock, _ = _read_finance_manifest(carrier)
        entries = {entry['path']: entry for entry in lock['files']}
        source_plugin = source / 'finance-plugin/index.mjs'
        if source_plugin.is_file() and _file_hash(source_plugin) != lock['plugin_sha256']:
            raise HarnessError('The deployed finance plugin differs from the current source plugin')
        return {'kind': 'finance_carrier', 'deployment': 'finance-carrier', 'location_neutral': True,
                'node_resource': lock['node'], 'node_sha256': entries[lock['node']]['sha256'],
                'entry_resource': lock['bootstrap'], 'entry_sha256': entries[lock['bootstrap']]['sha256'],
                'dependency_lock_sha256': lock['manifest_sha256'], 'package_count': lock['package_count'],
                'installed_package_count': lock['installed_package_count'], 'actual_bytes': lock['total_bytes']}
    return {'kind': 'missing_finance_carrier', 'location_neutral': True}


def _assert_transport_origin() -> None:
    """A historical nested package must never supply the trusted transport."""
    if __name__ != 'mingjing.harness_client' or __package__ != 'mingjing':
        raise HarnessError('Runtime transport comes from an unsupported application namespace')
    expected = (Path(getattr(sys, '_MEIPASS')) / 'mingjing' if getattr(sys, 'frozen', False)
                and hasattr(sys, '_MEIPASS') else Path(__file__).resolve().parent)
    for name in ('mingjing', 'mingjing.config', 'mingjing.audit'):
        module = sys.modules.get(name)
        origin = getattr(module, '__file__', None)
        if origin and Path(origin).resolve().parent != expected.resolve():
            raise HarnessError('Runtime transport imports a different application checkout')


def runtime_fingerprint(config: dict | None = None) -> dict[str, Any]:
    """Lock the exact source archive, native assets and private Python client.

    This consumer intentionally does not import a separately installed PyPI
    SDK. It launches the official profile entry from this pinned source archive
    and implements only NDJSON framing/lifecycle extensions, not an Agent loop.
    Reflection hashes Python code equally in source and frozen distributions.
    Reading these small metadata files never triggers the large asset extract.
    """
    from .audit import _module_code_data, _function_code_data, digest
    _assert_transport_origin()
    module = sys.modules[__name__]
    transport = _module_code_data(module)
    # The generic kernel serializer omits wrapped descriptors. This transport
    # includes them because its static resource resolution controls executable
    # and profile ownership.
    for class_name, cls in vars(module).items():
        if isinstance(cls, type) and cls.__module__ == __name__:
            for name, member in vars(cls).items():
                if isinstance(member, (staticmethod, classmethod)):
                    transport['classes'][class_name]['methods'][name] = _function_code_data(member.__func__)
    source = _source_resource_root()
    carrier = _finance_carrier(source, config or {})
    result = {'transport': 'mingjing-private-ndjson', 'protocol_version': 1, 'lock_schema_version': 2,
              'legacy_lock_policy': 'read_only_requires_original_runtime_or_new_task',
              'transport_code_sha256': digest(transport), 'official_python_sdk_installed': False,
              'sdk_reference_kind': 'same_source_archive', 'sdk_reference_version': '0.0.0.dev0'}
    if carrier is None:
        return {**result, 'assets_available': False, 'launch': _launch_identity(source, config or {}, None)}
    manifest, _ = _read_finance_manifest(carrier)
    source_file = source / 'harness/source_snapshot.json'
    _regular_path(source_file, source)
    source_bytes = source_file.read_bytes()
    original = json.loads(source_bytes)
    if hashlib.sha256(source_bytes).hexdigest() != manifest.get('source_snapshot_sha256'):
        raise HarnessError('The native source snapshot differs from its manifest')
    sdk_files = [entry for entry in original['files'] if entry['path'].startswith('python/sdk/')]
    return {**result, 'assets_available': True, 'launch': _launch_identity(source, config or {}, manifest),
            'harness_version': manifest['harness_version'],
            'manifest_sha256': manifest['manifest_sha256'],
            'plugin_sha256': manifest['plugin_sha256'], 'source_archive_sha256': manifest['source_sha256'],
            'source_snapshot_sha256': hashlib.sha256(source_bytes).hexdigest(),
            'upstream_commit': manifest.get('git_commit'), 'pnpm_lock_sha256': original['pnpm_lock_sha256'],
            'sdk_reference_files_sha256': digest(sdk_files), 'embedded_node_version': manifest['node_version'],
            'node_executable_sha256': next(entry['sha256'] for entry in manifest['files']
                                          if entry['path'] == manifest['node'])}


def extract_cached_assets(source: Path, cache_base: Path | None = None) -> Path:
    """Publish immutable verified runtime assets at a stable per-version path.

    Only the two explicitly declared production directories are copied; build
    toolchains and node_modules never enter this cache. A per-manifest OS file
    lock protects concurrent one-file EXE starts, and a same-volume directory
    rename publishes only a completely verified generation.
    """
    source = source.resolve()
    manifest_path = source / "harness" / "manifest.json"
    _regular_path(manifest_path, source)
    raw = manifest_path.read_bytes()
    try:
        manifest = json.loads(raw)
        if manifest.get("format_version") != 2:
            raise ValueError("Production runtime requires the complete v2 manifest")
        entries = manifest["files"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("Empty runtime manifest")
        seen: set[str] = set()
        for entry in entries:
            relative = PurePosixPath(entry["path"])
            if (relative.is_absolute() or not relative.parts or relative.parts[0] not in ("harness", "finance-plugin")
                    or any(part in (".", "..") or ":" in part or "\\" in part for part in relative.parts)
                    or relative.as_posix() != entry["path"] or entry["path"] in seen
                    or len(entry["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in entry["sha256"])
                    or not isinstance(entry["bytes"], int) or entry["bytes"] < 0):
                raise ValueError("Unsafe runtime manifest entry")
            seen.add(entry["path"])
        if "finance-plugin/index.mjs" not in seen or f"harness/{manifest['executable']}" not in seen:
            raise ValueError("Runtime manifest lacks its launcher or financial plugin")
    except (ValueError, KeyError, TypeError) as exc:
        raise HarnessError("Invalid production runtime manifest") from exc
    digest = hashlib.sha256(raw).hexdigest()
    from .config import app_root
    base = (cache_base or app_root() / "runtime-cache").absolute()
    _regular_path(base, base)
    base.mkdir(parents=True, exist_ok=True)
    lock_dir = base / ".locks"
    lock_dir.mkdir(exist_ok=True)
    lock_path = lock_dir / f"{digest}.lock"
    _regular_path(lock_path, base)
    target = base / digest

    def verify(root: Path) -> bool:
        try:
            _regular_path(root, base)
            _verify_files(root, entries, "harness/manifest.json", raw)
            return True
        except (OSError, HarnessError):
            return False

    with lock_path.open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        locked = False
        deadline = time.monotonic() + 300
        try:
            if os.name == "nt":
                import msvcrt
                while not locked:
                    lock.seek(0)
                    try:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise HarnessError("Another process is preparing the Harness runtime")
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                locked = True
            if target.exists():
                if verify(target):
                    return target
                raise HarnessError("Cached Harness assets failed their SHA-256 integrity check")
            stage = Path(tempfile.mkdtemp(prefix=f".{digest[:16]}-", dir=base))
            try:
                for entry in entries:
                    original = source / entry["path"]
                    _regular_path(original, source)
                    if not _io_path(original).is_file():
                        raise HarnessError("Bundled Harness resource is missing")
                    output = stage / entry["path"]
                    _io_path(output.parent).mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(_io_path(original), _io_path(output))
                    if _io_path(output).stat().st_size != entry["bytes"] or _file_hash(output) != entry["sha256"]:
                        raise HarnessError("Bundled Harness asset failed its SHA-256 integrity check")
                (stage / "harness" / "manifest.json").write_bytes(raw)
                os.replace(stage, target)
            finally:
                if stage.exists():
                    if stage.parent != base or not stage.name.startswith(f".{digest[:16]}-"):
                        raise HarnessError("Unsafe runtime staging cleanup path")
                    _regular_path(stage, base)
                    shutil.rmtree(_io_path(stage))
            return target
        finally:
            if locked:
                lock.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class HarnessRuntime:
    """Own one isolated native Harness process and dispatch its private requests.

    ``request_handler(method, params)`` answers tool, credential, and cache RPCs.
    ``event_handler(method, params)`` receives durable session notifications.
    The runtime has its own bounded stdio transport; all model/tool execution
    belongs to the unmodified upstream Node AgentLoop.
    """

    def __init__(self, home: Path, request_handler: Callable[[str, dict], Any],
                 event_handler: Callable[[str, dict], None] | None = None,
                 config: dict | None = None, expected_runtime_lock: dict | None = None):
        self.home = Path(home).expanduser().resolve()
        self.request_handler = request_handler
        self.event_handler = event_handler
        self.config = dict(config or {})
        self.expected_runtime_lock = expected_runtime_lock
        self.runtime_lock: dict | None = None
        self._process: subprocess.Popen | None = None
        self._pending: dict[str, tuple[threading.Event, dict]] = {}
        self._write_lock = threading.Lock()
        self._state = threading.Condition(threading.RLock())
        self._counter = 0
        self._notifications: dict[str, list[dict]] = {}
        self._statuses: dict[str, str] = {}
        self._stderr: deque[str] = deque(maxlen=48)
        self._workers = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mj-harness-rpc")
        self._closed = False
        self._transport_closed = False
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._assets_root: Path | None = None
        self._launch_verified_carrier: tuple[Path, dict] | None = None

    @staticmethod
    def _resource_root() -> Path:
        return _source_resource_root()

    def _assets(self) -> Path:
        if self._assets_root is None:
            carrier = _finance_carrier(_source_resource_root(), self.config)
            if carrier is None:
                raise HarnessError('The locked finance carrier is missing; no unpinned runtime fallback is permitted')
            self._assets_root = extract_finance_carrier(carrier)
        return self._assets_root

    def _launch(self) -> list[str]:
        self._launch_verified_carrier = None
        custom = _configured_custom(self.config)
        if custom:
            executable = Path(custom).expanduser().resolve()
            if not executable.is_file():
                raise HarnessError("Configured dsh runtime executable is missing")
            current = _launch_identity(_source_resource_root(), self.config, None)
            if self.runtime_lock and current != self.runtime_lock['launch']:
                raise HarnessError('The external native executable changed after its runtime lock was captured')
            return [str(executable)]
        root = self._assets()
        verified = verify_finance_carrier(root)
        identity = _launch_identity(_source_resource_root(), self.config, None)
        if (verified['manifest_sha256'] != identity['dependency_lock_sha256']
                or self.runtime_lock and identity != self.runtime_lock['launch']):
            raise HarnessError('The compiled finance carrier changed after its runtime lock was captured')
        self._launch_verified_carrier = (root, verified)
        return [str(root / identity['node_resource']), str(root / identity['entry_resource'])]

    def _prepare_profile(self) -> None:
        """Materialize trusted profile rows; neither prompt nor API key is YAML."""
        root = self._assets()
        plugin = root / "finance-plugin" / "index.mjs"
        if not plugin.is_file():
            raise HarnessError("Financial Harness plugin is missing")
        profile = self.home / "profiles" / "mingjing"
        profile.mkdir(parents=True, exist_ok=True)
        # Out-of-tree plugins must live under the profile parent so the official
        # profile module fallback preserves its installation's service classes.
        installed_plugin = profile / "mingjing-finance-plugin" / "index.mjs"
        installed_plugin.parent.mkdir(exist_ok=True)
        _regular_path(installed_plugin, self.home)
        raw_plugin = plugin.read_bytes()
        if not installed_plugin.exists() or installed_plugin.read_bytes() != raw_plugin:
            temporary = installed_plugin.with_suffix(".tmp")
            temporary.write_bytes(raw_plugin)
            os.replace(temporary, installed_plugin)
        manifest = {"name": "mingjing-harness-profile", "private": True, "type": "module",
                    "dsh": {"profile": {"bundles": []}}}
        (profile / "package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        rows = [
            {"id": "sdk-app-startup", "name": "@deepseek-ai/dsh-sdk-app", "config": {"profile": "mingjing"}},
            {"id": "timer", "name": "@deepseek-ai/cordis-plugin-timer"},
            {"id": "llm", "name": "@deepseek-ai/dsh-llm"},
            {"id": "session", "name": "@deepseek-ai/dsh-session"},
            {"id": "session-log-deepseek", "name": "@deepseek-ai/dsh-session-log-deepseek"},
            {"id": "session-projection", "name": "@deepseek-ai/dsh-session-projection"},
            {"id": "session-title", "name": "@deepseek-ai/dsh-session-title", "config": {"fallbackMaxWords": 5, "fallbackMaxBytes": 40, "maxTitleBytes": 80}},
            {"id": "system-prompt", "name": "@deepseek-ai/dsh-system-prompt", "config": {"includeHarnessIdentity": False, "includeRuntimeContext": False, "personaPrefix": ""}},
            {"id": "tools", "name": "@deepseek-ai/dsh-tools"},
            {"id": "agent", "name": "@deepseek-ai/dsh-agent"},
            {"id": "llm-retry", "name": "@deepseek-ai/dsh-llm-retry"},
            {"id": "jobs", "name": "@deepseek-ai/dsh-jobs-local"},
            {"id": "agent-loop", "name": "@deepseek-ai/dsh-agent-loop", "config": {"agents": []}},
            {"id": "sessions", "name": "@deepseek-ai/dsh-session-persistence-jsonl", "config": {"root": str(self.home / "sessions"), "compression": "none"}},
            {"id": "subagent", "name": "@deepseek-ai/dsh-subagent", "config": {"maxDepth": 1}},
            {"id": "subagent-spawn-in-process", "name": "@deepseek-ai/dsh-subagent-spawn-in-process"},
            {"id": "mj-finance-rpc", "name": installed_plugin.as_uri(), "inject": ["sdkAppStartup", "loader"]},
        ]
        # JSON is valid YAML. Data-only resources cannot execute !!js expressions.
        (profile / "cordis.patch.yml").write_text(json.dumps([{"insert": rows}], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # This home is application-owned: never inherit an unrelated user's patch.
        self.home.mkdir(parents=True, exist_ok=True)
        patch = self.home / "cordis.patch.yml"
        if patch.exists() and patch.read_text(encoding="utf-8").strip() not in ("", "[]"):
            raise HarnessError("Untrusted home-level Harness patch is not permitted")
        patch.write_text("[]\n", encoding="utf-8")

    def start(self) -> dict:
        if self._process is not None:
            return self.describe()
        if self._closed:
            raise HarnessError("Harness client is closed")
        nested = self.config.get('harness') or {}
        startup_timeout = self.config.get('initialize_timeout_s', nested.get('timeout_s', 60))
        if type(startup_timeout) not in (int, float) or not math.isfinite(startup_timeout) or startup_timeout <= 0:
            raise HarnessError('Harness startup timeout must be a positive finite number')
        # Reject an unavailable external runtime before materializing the
        # complete embedded resource closure. A cold health check must not
        # spend its timeout extracting Office sidecars for a process that
        # cannot be started. Existing executables still pass all lock checks.
        custom = _configured_custom(self.config)
        if custom:
            raw = Path(custom).expanduser().absolute()
            _regular_path(raw, raw.parent)
            if not raw.resolve().is_file():
                raise HarnessError("Configured dsh runtime executable is missing")
        self.runtime_lock = runtime_fingerprint(self.config)
        if self.expected_runtime_lock is not None and self.runtime_lock != self.expected_runtime_lock:
            raise HarnessError('The runtime transport, executable or plugin lock differs from the task snapshot')
        self._prepare_profile()
        command = [*self._launch(), "--profile", "mingjing"]
        allowed = ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PATH", "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
        env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
        env["DSH_HOME"] = str(self.home)
        env["DSH_NO_TELEMETRY"] = "1"
        if self.runtime_lock.get('assets_available'):
            native_cache = _native_cache_root(self.config)
            carrier = self._assets()
            checked = (self._launch_verified_carrier[1] if self._launch_verified_carrier is not None
                       and self._launch_verified_carrier[0] == carrier else verify_finance_carrier(carrier))
            _verify_native_cache(native_cache, carrier, verified_manifest=checked)
            env['NARB_NATIVE_CACHE_DIR'] = str(native_cache)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                         cwd=str(self.home), env=env, bufsize=1, creationflags=creationflags)
        self._reader_thread = threading.Thread(target=self._reader, daemon=True, name="mj-harness-reader")
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True, name="mj-harness-stderr")
        self._reader_thread.start()
        self._stderr_thread.start()
        try:
            self.request("initialize", {}, timeout_s=float(startup_timeout))
            return self.describe()
        except BaseException:
            self.close()
            raise

    def describe(self) -> dict:
        return self.request("mj/runtime.describe", {}, timeout_s=30)

    def _send(self, frame: dict) -> None:
        encoded = json.dumps(frame, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        if len(encoded.encode("utf-8")) > 16 * 1024 * 1024:
            raise HarnessError("Harness RPC frame exceeded 16 MiB")
        with self._write_lock:
            if not self._process or self._process.poll() is not None or not self._process.stdin:
                raise HarnessError("Harness subprocess is not available")
            self._process.stdin.write(encoded)
            self._process.stdin.flush()

    def request(self, method: str, params: dict, timeout_s: float = 120) -> dict:
        with self._state:
            self._counter += 1
            identifier = f"mj-{self._counter}"
            done, response = threading.Event(), {}
            self._pending[identifier] = (done, response)
        try:
            self._send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
            if not done.wait(timeout_s):
                raise HarnessError(f"Harness {method} timed out")
            if "error" in response:
                error = response["error"]
                raise HarnessError(str(error.get("message", "Harness RPC failed"))[:2000])
            result = response.get("result")
            if not isinstance(result, dict):
                raise HarnessError(f"Harness {method} returned an invalid object")
            return result
        finally:
            with self._state:
                self._pending.pop(identifier, None)

    def open_session(self, **spec: Any) -> dict:
        if self._process is None:
            self.start()
        return self.request("mj/session.open", spec)

    def resume(self, **spec: Any) -> dict:
        if self._process is None:
            self.start()
        return self.request("mj/session.resume", spec)

    def prompt(self, session_id: str, text: str, timeout_s: float = 600,
               cancel_event: threading.Event | None = None) -> dict:
        with self._state:
            begin = len(self._notifications.get(session_id, []))
        receipt = self.request("mj/session.prompt", {"session_id": session_id, "text": text})
        deadline = time.monotonic() + timeout_s
        active_seen = False
        while True:
            with self._state:
                interval = list(self._notifications.get(session_id, [])[begin:])
                if self._transport_closed or not self._process or self._process.poll() is not None:
                    raise HarnessError("Harness subprocess stopped during the task")
                active_seen = active_seen or any(frame["method"] == "session.status" and frame["params"].get("status") == "running" for frame in interval)
                if active_seen and self._statuses.get(session_id) == "idle":
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if cancel_event is not None and cancel_event.is_set():
                    timed_out = False
                    break
                self._state.wait(timeout=0.1)
        if not (active_seen and self._statuses.get(session_id) == "idle"):
            self.cancel(session_id)
            raise HarnessError("Harness task timed out" if timed_out else "Harness task cancelled")
        events = [frame["params"]["event"] for frame in interval if frame["method"] == "session.event"]
        final_response = ""
        finish_reason = None
        for event in events:
            data = event.get("data", {})
            if event.get("type") == "assistant/message":
                final_response = "\n".join(block.get("text", "") for block in data.get("message", {}).get("content", []) if block.get("type") == "text")
            if event.get("type") == "turn/end":
                finish_reason = data.get("reason", {}).get("kind")
        return {"session_id": session_id, "message_id": receipt["messageId"],
                "final_response": final_response, "finish_reason": finish_reason,
                "events": events, "notifications": interval, "native_loop": True}

    def cancel(self, session_id: str) -> dict:
        return self.request("mj/session.cancel", {"session_id": session_id}, timeout_s=30)

    def close_session(self, session_id: str) -> dict:
        return self.request("mj/session.close", {"session_id": session_id}, timeout_s=30)

    def _answer(self, frame: dict) -> None:
        method = frame.get("method")
        try:
            if method not in ("mj/tool.dispatch", "mj/credentials.resolve", "mj/model.cache.get", "mj/model.cache.put"):
                raise HarnessError("Runtime requested an unregistered private host method")
            if method == 'mj/credentials.resolve' and self.runtime_lock:
                if _finance_carrier(_source_resource_root(), self.config) is not None:
                    actual = _launch_identity(_source_resource_root(), self.config, None)
                    if actual != self.runtime_lock['launch']:
                        raise HarnessError('Finance carrier changed before credential resolution')
                    carrier = self._assets()
                    checked = verify_finance_carrier(carrier)
                    if checked['manifest_sha256'] != self.runtime_lock.get('manifest_sha256', actual.get('dependency_lock_sha256')):
                        raise HarnessError('Executed finance carrier differs from the task snapshot')
                    _verify_native_cache(_native_cache_root(self.config), carrier, verified_manifest=checked)
            value = self.request_handler(method, frame.get("params", {}))
            self._send({"jsonrpc": "2.0", "id": frame["id"], "result": value})
        except BaseException as exc:
            # Host errors may carry credentials; expose the error class only.
            try:
                self._send({"jsonrpc": "2.0", "id": frame["id"], "error": {"code": -32603, "message": f"Financial host rejected request ({type(exc).__name__})"}})
            except BaseException:
                pass

    def _reader(self) -> None:
        try:
            assert self._process and self._process.stdout
            for line in self._process.stdout:
                if len(line.encode("utf-8")) > 16 * 1024 * 1024:
                    raise HarnessError("Oversized runtime frame")
                frame = json.loads(line)
                if "id" in frame and "method" in frame:
                    self._workers.submit(self._answer, frame)
                elif "id" in frame:
                    with self._state:
                        pending = self._pending.get(str(frame["id"]))
                        if pending:
                            pending[1].update(frame)
                            pending[0].set()
                else:
                    method, params = frame.get("method", ""), frame.get("params", {})
                    session_id = params.get("sessionId")
                    with self._state:
                        if session_id:
                            self._notifications.setdefault(session_id, []).append(frame)
                            if method == "session.status":
                                self._statuses[session_id] = params.get("status", "")
                        self._state.notify_all()
                    if self.event_handler:
                        try:
                            self.event_handler(method, params)
                        except Exception:
                            pass
        except BaseException:
            pass
        finally:
            with self._state:
                self._transport_closed = True
                for done, response in self._pending.values():
                    response.update(error={"message": "Harness transport closed"})
                    done.set()
                self._state.notify_all()

    def _read_stderr(self) -> None:
        assert self._process and self._process.stderr
        for line in self._process.stderr:
            # Never export stderr into reports: providers may include raw response text.
            self._stderr.append(line.rstrip())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None:
            try:
                self.request("shutdown", {}, timeout_s=10)
                process.wait(timeout=10)
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass
            self._process = None
        self._workers.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> "HarnessRuntime":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
