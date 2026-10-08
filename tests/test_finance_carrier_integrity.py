"""Actual deployed-code locks, credential ordering and shared validation."""
import hashlib
import json
import os
import shutil
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mingjing import harness_client
from mingjing.harness_client import HarnessError, HarnessRuntime, extract_cached_assets, verify_finance_carrier


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def make_carrier(root):
    content = {
        'bootstrap.mjs': b"import 'root';\n",
        'bin/node.exe': b'MZ-controlled-test-fixture',
        'finance-plugin/index.mjs': b'export default {};\n',
        'finance-plugin/package.json': b'{"name":"finance-plugin","type":"module"}',
        'node_modules/root/package.json': b'{"name":"root","version":"1.0.0","dependencies":{"leaf":"1.0.0"}}',
        'node_modules/root/index.js': b"import 'leaf';\n",
        'node_modules/leaf/package.json': b'{"name":"leaf","version":"1.0.0"}',
        'node_modules/leaf/index.js': b'export default {};\n',
        'node_modules/leaf/native.node': b'controlled-native-test-fixture',
    }
    for name, raw in content.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    manifest = {'format_version': 1, 'kind': 'mingjing-finance-carrier', 'platform': 'win32-x64',
                'bootstrap': 'bootstrap.mjs', 'node': 'bin/node.exe', 'plugin_sha256': sha(content['finance-plugin/index.mjs']),
                'package_count': 2, 'installed_package_count': 2, 'total_bytes': sum(map(len, content.values())),
                'package_map': [{'name': 'root', 'version': '1.0.0', 'path': 'node_modules/root', 'dependencies': {'leaf': 'node_modules/leaf'}},
                                {'name': 'leaf', 'version': '1.0.0', 'path': 'node_modules/leaf', 'dependencies': {}}],
                'files': [{'path': name, 'bytes': len(raw), 'sha256': sha(raw)} for name, raw in content.items()]}
    (root / 'carrier_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest


def test_unchanged_closure_reuses_content_hashes_but_rechecks_tree(tmp_path, monkeypatch):
    root = tmp_path / 'carrier'
    manifest = make_carrier(root)
    calls = []
    original = harness_client._file_hash
    monkeypatch.setattr(harness_client, '_file_hash', lambda path: calls.append(path) or original(path))
    first = verify_finance_carrier(root)
    assert len(calls) == len(manifest['files'])
    calls.clear()
    assert verify_finance_carrier(root) == first
    assert calls == []
    (root / 'node_modules/leaf/extra.js').write_text('extra', encoding='utf-8')
    with pytest.raises(HarnessError, match='inventory'):
        verify_finance_carrier(root)


@pytest.mark.parametrize('resource', ['node_modules/root/index.js', 'node_modules/leaf/package.json', 'node_modules/leaf/native.node'])
def test_same_size_write_with_restored_mtime_cannot_bypass_content_lock(tmp_path, resource):
    root = tmp_path/'carrier'; make_carrier(root)
    verify_finance_carrier(root)
    path = root/resource
    before = path.stat()
    change_before = harness_client._windows_change_time(path) if os.name == 'nt' else before.st_ctime_ns
    raw = path.read_bytes()
    modified = raw.replace(b'1.0.0', b'9.0.0') if resource.endswith('.json') else b'X'+raw[1:]
    assert len(modified) == len(raw) and modified != raw
    path.write_bytes(modified)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
    if os.name == 'nt':
        assert before.st_ctime_ns == after.st_ctime_ns  # birth time stayed equal in the actual Windows counterexample
        change_after = harness_client._windows_change_time(path)
        if change_before is not None and change_after is not None:
            assert change_before != change_after
    with pytest.raises(HarnessError, match='SHA-256'):
        verify_finance_carrier(root)


def test_unavailable_windows_change_stamp_forces_content_rehash(tmp_path, monkeypatch):
    root = tmp_path/'carrier'; manifest = make_carrier(root)
    monkeypatch.setattr(harness_client, '_windows_change_time', lambda _:None)
    if os.name != 'nt':
        pytest.skip('Windows missing-change-time fallback is platform specific')
    calls = []
    original = harness_client._file_hash
    monkeypatch.setattr(harness_client, '_file_hash', lambda path:calls.append(path) or original(path))
    verify_finance_carrier(root)
    calls.clear()
    verify_finance_carrier(root)
    assert len(calls) == len(manifest['files'])
    path = root/'node_modules/leaf/index.js'; stamp = path.stat(); raw = path.read_bytes()
    path.write_bytes(b'X'+raw[1:]); os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(HarnessError, match='SHA-256'):
        verify_finance_carrier(root)


@pytest.mark.parametrize('resource', ['node_modules/root/index.js', 'node_modules/root/package.json', 'node_modules/leaf/native.node'])
def test_changed_compiled_js_json_and_native_fail_before_credentials(tmp_path, monkeypatch, resource):
    root = tmp_path / 'carrier'
    make_carrier(root)
    source = tmp_path / 'runtime'
    (source/'finance-plugin').mkdir(parents=True)
    (source/'finance-plugin/index.mjs').write_bytes((root/'finance-plugin/index.mjs').read_bytes())
    monkeypatch.setattr(harness_client, '_source_resource_root', lambda: source)
    monkeypatch.setenv('MINGJING_HOME', str(tmp_path/'app'))
    config = {'finance_carrier_root': str(root)}
    identity = harness_client._launch_identity(source, config, None)
    handled, frames = [], []
    runtime = HarnessRuntime(tmp_path/'home', lambda *args: handled.append(args) or {'api_key': 'must-not-be-resolved'}, config=config)
    runtime.runtime_lock = {'launch': identity}
    monkeypatch.setattr(runtime, '_send', frames.append)
    executed = runtime._assets()
    original = (executed/resource).read_bytes()
    (executed/resource).write_bytes(b'X'+original[1:])
    try:
        runtime._answer({'jsonrpc': '2.0', 'id': 'credential-call', 'method': 'mj/credentials.resolve', 'params': {}})
    finally:
        runtime.close()
    assert handled == []
    assert frames[0]['error']['message'] == 'Financial host rejected request (HarnessError)'
    assert 'must-not-be-resolved' not in json.dumps(frames)


@pytest.mark.parametrize('mutation', ['missing', 'map_target', 'package_identity', 'traversal'])
def test_missing_and_forged_package_resolution_are_rejected(tmp_path, mutation):
    root = tmp_path/'carrier'
    manifest = make_carrier(root)
    if mutation == 'missing':
        (root/'node_modules/leaf/index.js').unlink()
    elif mutation == 'map_target':
        manifest['package_map'][0]['dependencies']['leaf'] = 'node_modules/root'
    elif mutation == 'package_identity':
        manifest['package_map'][1]['version'] = '9.9.9'
    else:
        manifest['files'][0]['path'] = '../escaped.js'
    (root/'carrier_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(HarnessError):
        verify_finance_carrier(root)


def test_linked_runtime_files_fail_even_after_prior_validation(tmp_path):
    root = tmp_path/'carrier'
    make_carrier(root)
    verify_finance_carrier(root)
    target = root/'node_modules/leaf/index.js'
    external = tmp_path/'external.js'
    external.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(external)
    except OSError:
        pytest.skip('Current Windows user lacks symbolic-link privileges')
    with pytest.raises(HarnessError, match='Linked'):
        verify_finance_carrier(root)


def test_full_asset_cache_uses_mingjing_home_and_shared_content_validation(tmp_path, monkeypatch):
    app = tmp_path/'isolated-app'
    monkeypatch.setenv('MINGJING_HOME', str(app))
    source = tmp_path/'production'
    resources = {'harness/test.exe': b'MZfixture', 'finance-plugin/index.mjs': b'export default {};'}
    for path, raw in resources.items():
        target = source/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    manifest = {'format_version': 2, 'executable': 'test.exe', 'files': [
        {'path': path, 'bytes': len(raw), 'sha256': sha(raw)} for path, raw in resources.items()]}
    (source/'harness/manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    cache = extract_cached_assets(source)
    assert cache.parent == app/'runtime-cache'
    assert extract_cached_assets(source) == cache
    calls = []
    original = harness_client._file_hash
    monkeypatch.setattr(harness_client, '_file_hash', lambda path: calls.append(path) or original(path))
    assert extract_cached_assets(source) == cache
    assert calls == []
    (cache/'harness/test.exe').write_bytes(b'MZchanged')
    with pytest.raises(HarnessError, match='integrity'):
        extract_cached_assets(source)


def test_web_harness_timeout_controls_actual_initialize_request(tmp_path, monkeypatch):
    from types import SimpleNamespace
    runtime = HarnessRuntime(tmp_path/'home', lambda *_: {}, config={'harness': {'timeout_s': 7}})
    captured = []
    monkeypatch.setattr(harness_client, 'runtime_fingerprint', lambda *_: {'fixture': True})
    monkeypatch.setattr(runtime, '_prepare_profile', lambda: None)
    monkeypatch.setattr(runtime, '_launch', lambda: ['controlled-fixture'])
    monkeypatch.setattr(harness_client.subprocess, 'Popen', lambda *_args, **_kwargs: SimpleNamespace(
        stdout=[], stderr=[], stdin=None, poll=lambda: 0, wait=lambda **_: 0))
    monkeypatch.setattr(runtime, '_reader', lambda: None)
    monkeypatch.setattr(runtime, '_read_stderr', lambda: None)
    monkeypatch.setattr(runtime, 'request', lambda method, params, timeout_s=120: captured.append((method, timeout_s)) or {})
    try:
        runtime.start()
        assert captured[0] == ('initialize', 7.0)
    finally:
        runtime.close()


@pytest.mark.parametrize('timeout', [True, '30', 0, -1, float('inf'), float('nan')])
def test_bad_harness_startup_timeout_fails_before_any_launch(tmp_path, timeout):
    runtime = HarnessRuntime(tmp_path/'home', lambda *_: {}, config={'harness': {'timeout_s': timeout}})
    try:
        with pytest.raises(HarnessError, match='startup timeout'):
            runtime.start()
        assert runtime._process is None
    finally:
        runtime.close()


def make_bundle(source, bundle, mutate=None):
    bundle.mkdir()
    raw = (source/'carrier_manifest.json').read_bytes()
    manifest = json.loads(raw)
    (bundle/'carrier_manifest.json').write_bytes(raw)
    archive_path = bundle/'finance-carrier.payload.zip'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        for entry in manifest['files']:
            info = zipfile.ZipInfo(entry['path'])
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, (source/entry['path']).read_bytes())
        if mutate:
            mutate(archive)
    descriptor = {'format_version': 1, 'carrier_manifest_sha256': sha(raw), 'payload': archive_path.name,
                  'bytes': archive_path.stat().st_size, 'sha256': sha(archive_path.read_bytes())}
    (bundle/'payload.json').write_text(json.dumps(descriptor), encoding='utf-8')
    return descriptor


def test_source_bundle_and_changed_distribution_location_share_one_cache(tmp_path, monkeypatch):
    source = tmp_path/'source'
    make_carrier(source)
    bundle = tmp_path/'MEIPASS-1'
    make_bundle(source, bundle)
    other = tmp_path/'MEIPASS-2'
    shutil.copytree(bundle, other)
    base = tmp_path/'cache'
    first = harness_client.extract_finance_carrier(source, base)
    assert first.name == sha((source/'carrier_manifest.json').read_bytes())
    monkeypatch.setattr(harness_client, '_copy_checked_stream', lambda *_: pytest.fail('A cache hit must not recopy runtime files'))
    assert harness_client.extract_finance_carrier(bundle, base) == first
    assert harness_client.extract_finance_carrier(other, base) == first
    assert len([path for path in base.iterdir() if path.is_dir() and not path.name.startswith('.')]) == 1


def test_concurrent_starts_publish_only_one_complete_cache(tmp_path, monkeypatch):
    source = tmp_path/'source'
    manifest = make_carrier(source)
    original = harness_client._copy_checked_stream
    copies = []
    monkeypatch.setattr(harness_client, '_copy_checked_stream', lambda stream, target, entry: copies.append(entry['path']) or original(stream, target, entry))
    with ThreadPoolExecutor(max_workers=3) as pool:
        roots = list(pool.map(lambda _: harness_client.extract_finance_carrier(source, tmp_path/'cache'), range(3)))
    assert roots[0] == roots[1] == roots[2]
    assert len(copies) == len(manifest['files'])
    assert not any(path.name.startswith('.'+roots[0].name[:16]+'-') for path in roots[0].parent.iterdir())


@pytest.mark.parametrize('hardlinks_available', [True, False])
def test_new_generation_reuses_only_verified_cache_and_never_links_mutable_source(tmp_path, monkeypatch, hardlinks_available):
    source = tmp_path/'source'
    manifest = make_carrier(source)
    base = tmp_path/'cache'
    first = harness_client.extract_finance_carrier(source, base)
    original_plugin = (first/'finance-plugin/index.mjs').read_bytes()
    updated = b'export default {version:2};\n'
    (source/'finance-plugin/index.mjs').write_bytes(updated)
    for item in manifest['files']:
        if item['path'] == 'finance-plugin/index.mjs':
            item.update(bytes=len(updated), sha256=sha(updated))
    manifest.update(plugin_sha256=sha(updated), total_bytes=sum(item['bytes'] for item in manifest['files']))
    (source/'carrier_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    original_link = os.link
    linked = []
    def link(old, new):
        linked.append((Path(old), Path(new)))
        if not hardlinks_available:
            raise OSError('Fixture filesystem lacks hardlink support')
        return original_link(old, new)
    monkeypatch.setattr(harness_client.os, 'link', link)
    second = harness_client.extract_finance_carrier(source, base)
    assert first != second and len(linked) == len(manifest['files'])-1
    assert all(old.is_relative_to(harness_client._io_path(first)) for old, _ in linked)
    assert (first/'finance-plugin/index.mjs').read_bytes() == original_plugin
    assert (second/'finance-plugin/index.mjs').read_bytes() == updated
    mutable = source/'node_modules/leaf/index.js'
    mutable.write_text('modified after deployment', encoding='utf-8')
    assert (first/'node_modules/leaf/index.js').read_bytes() == (second/'node_modules/leaf/index.js').read_bytes() == b'export default {};\n'
    verify_finance_carrier(first)
    verify_finance_carrier(second)


@pytest.mark.parametrize('kind', ['extra', 'duplicate', 'traversal', 'symlink', 'missing', 'payload_hash', 'member_hash'])
def test_malformed_embedded_zip_never_publishes_a_cache(tmp_path, kind):
    source = tmp_path/'source'
    make_carrier(source)
    bundle = tmp_path/'bundle'
    def mutation(archive):
        if kind in ('extra', 'traversal'):
            archive.writestr('extra.js' if kind == 'extra' else '../escape.js', 'extra')
        elif kind == 'duplicate':
            archive.writestr('bootstrap.mjs', "import 'root';\n")
    descriptor = make_bundle(source, bundle, mutation)
    archive_path = bundle/descriptor['payload']
    if kind in ('symlink', 'missing', 'member_hash'):
        with zipfile.ZipFile(archive_path) as archive:
            members = [(item, archive.read(item)) for item in archive.infolist()]
        with zipfile.ZipFile(archive_path, 'w') as archive:
            for index, (item, content) in enumerate(members):
                if index == 0:
                    if kind == 'missing':
                        continue
                    if kind == 'symlink':
                        item.external_attr = (stat.S_IFLNK | 0o777) << 16
                    if kind == 'member_hash':
                        content = b'X'+content[1:]
                archive.writestr(item, content)
        descriptor.update(bytes=archive_path.stat().st_size, sha256=sha(archive_path.read_bytes()))
    if kind == 'payload_hash':
        descriptor['sha256'] = '0'*64
    (bundle/'payload.json').write_text(json.dumps(descriptor), encoding='utf-8')
    with pytest.raises(HarnessError):
        harness_client.extract_finance_carrier(bundle, tmp_path/'cache')
    assert not any(path.is_dir() and not path.name.startswith('.') for path in (tmp_path/'cache').iterdir())
    assert not (tmp_path/'escape.js').exists()


def test_native_cache_allows_only_owned_locked_copies(tmp_path):
    carrier = tmp_path/'carrier'
    make_carrier(carrier)
    native = tmp_path/'native'
    native.mkdir()
    harness_client._verify_native_cache(native, carrier)
    target = native/'leaf/1.0.0/win32-x64-msvc/native.node'
    target.parent.mkdir(parents=True)
    target.write_bytes((carrier/'node_modules/leaf/native.node').read_bytes())
    harness_client._verify_native_cache(native, carrier)
    target.write_bytes(b'corrupt-native')
    with pytest.raises(HarnessError, match='Native runtime cache'):
        harness_client._verify_native_cache(native, carrier)
    target.write_bytes((carrier/'node_modules/leaf/native.node').read_bytes())
    (native/'injected.js').write_text('injected', encoding='utf-8')
    with pytest.raises(HarnessError, match='Native runtime cache'):
        harness_client._verify_native_cache(native, carrier)


def test_native_guard_consumes_only_exact_manifest_and_still_rehashes_native_copies(tmp_path, monkeypatch):
    carrier = tmp_path/'carrier'
    make_carrier(carrier)
    checked = verify_finance_carrier(carrier)
    native = tmp_path/'native'; native.mkdir()
    target = native/'leaf/1.0.0/win32-x64-msvc/native.node'
    target.parent.mkdir(parents=True); target.write_bytes((carrier/'node_modules/leaf/native.node').read_bytes())
    monkeypatch.setattr(harness_client, 'verify_finance_carrier', lambda *_: pytest.fail('A guard must not repeat an already completed tree verification'))
    harness_client._verify_native_cache(native, carrier, verified_manifest=checked)
    forged = dict(checked); forged['files'] = []
    with pytest.raises(HarnessError, match='different verified carrier manifest'):
        harness_client._verify_native_cache(native, carrier, verified_manifest=forged)
    with pytest.raises(HarnessError, match='different verified carrier manifest'):
        harness_client._verify_native_cache(native, carrier, verified_manifest={'passed':True})
    stamp = target.stat()
    raw = target.read_bytes(); target.write_bytes(b'X'+raw[1:]); os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(HarnessError, match='Native runtime cache'):
        harness_client._verify_native_cache(native, carrier, verified_manifest=checked)


def test_start_uses_two_tree_scans_and_each_credential_uses_one_fresh_scan(tmp_path, monkeypatch):
    from types import SimpleNamespace
    source = tmp_path/'runtime'; source.mkdir()
    carrier = source/'finance-carrier/staged'
    make_carrier(carrier)
    monkeypatch.setenv('MINGJING_HOME', str(tmp_path/'app'))
    monkeypatch.setattr(harness_client, '_source_resource_root', lambda: source)
    identity = harness_client._launch_identity(source, {}, None)
    lock = {'launch':identity, 'assets_available':True, 'manifest_sha256':identity['dependency_lock_sha256']}
    monkeypatch.setattr(harness_client, 'runtime_fingerprint', lambda *_: lock)
    calls, sent = [], []
    original = harness_client._verify_files
    monkeypatch.setattr(harness_client, '_verify_files', lambda *args, **kwargs: calls.append(args[0]) or original(*args, **kwargs))
    # Prime fixture cache, then count a normal cache-hit startup. Production
    # cold extraction has its separate once-per-generation deployment check.
    harness_client.extract_finance_carrier(carrier)
    calls.clear()
    runtime = HarnessRuntime(tmp_path/'home', lambda *_: {'fixture':'returned'}, config={})
    monkeypatch.setattr(runtime, '_reader', lambda: None)
    monkeypatch.setattr(runtime, '_read_stderr', lambda: None)
    monkeypatch.setattr(runtime, 'request', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runtime, '_send', sent.append)
    monkeypatch.setattr(harness_client.subprocess, 'Popen', lambda *_args, **_kwargs: SimpleNamespace(
        stdout=[], stderr=[], stdin=None, poll=lambda:0, wait=lambda **_:0))
    try:
        runtime.start()
        assert len(calls) == 2
        calls.clear()
        runtime._answer({'jsonrpc':'2.0','id':'credential','method':'mj/credentials.resolve','params':{}})
        assert len(calls) == 1 and sent[-1]['result'] == {'fixture':'returned'}
        calls.clear()
        damaged = runtime._assets()/'node_modules/leaf/index.js'
        raw = damaged.read_bytes(); damaged.write_bytes(b'X'+raw[1:])
        runtime._answer({'jsonrpc':'2.0','id':'blocked-credential','method':'mj/credentials.resolve','params':{}})
        assert len(calls) == 1 and 'error' in sent[-1]
    finally:
        runtime.close()


def test_module_origin_rejects_historical_namespace_and_mixed_checkout(tmp_path, monkeypatch):
    from mingjing import config
    monkeypatch.setattr(harness_client, '__name__', 'mingjing.mingjing.harness_client')
    with pytest.raises(HarnessError, match='namespace'):
        harness_client._assert_transport_origin()
    monkeypatch.setattr(harness_client, '__name__', 'mingjing.harness_client')
    monkeypatch.setattr(config, '__file__', str(tmp_path/'old-mingjing/config.py'))
    with pytest.raises(HarnessError, match='different application checkout'):
        harness_client._assert_transport_origin()


def test_legacy_runtime_lock_is_rejected_without_rebinding_or_file_rewrite(tmp_path, monkeypatch):
    old_record = tmp_path/'run_manifest.json'
    old_record.write_bytes(b'{"agent":{"runtime":{"lock_schema_version":1}}}\n')
    original = old_record.read_bytes()
    runtime = HarnessRuntime(tmp_path/'home', lambda *_: {}, expected_runtime_lock={'lock_schema_version': 1})
    monkeypatch.setattr(harness_client, 'runtime_fingerprint', lambda *_: {'lock_schema_version': 2})
    monkeypatch.setattr(runtime, '_prepare_profile', lambda: pytest.fail('Old locks cannot prepare a new profile'))
    try:
        with pytest.raises(HarnessError, match='task snapshot'):
            runtime.start()
        assert old_record.read_bytes() == original and runtime._process is None
    finally:
        runtime.close()
