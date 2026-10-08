"""Scoped stage packaging must preserve binary epoch and honest scope."""
from pathlib import Path
import hashlib
import json
import zipfile

import pytest

from scripts import package_stage as stage


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def fixture(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[project]\nversion="1.0.0.dev2"\n', encoding='utf-8')
    exe = root / 'dist/MingJing.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'MZ fictional packaging test')
    build = {'version': '1.0.0.dev2', 'mode': 'onefile', 'sha256': stage.sha256(exe),
             'bytes': exe.stat().st_size, 'source_sha256': 'source-epoch',
             'runtime_manifest_sha256': 'runtime-lock'}
    native = {'schema': 'mingjing-stage-native-exe.v1', 'version': build['version'],
              'exe_sha256': build['sha256'], 'source_sha256': build['source_sha256'],
              'runtime_manifest_sha256': build['runtime_manifest_sha256'],
              'status': 'passed_scoped_native_agent_and_exe', 'scope': 'local source sample only',
              'checks': {'native_agent': {'status': 'passed', 'native_runtime': True, 'tool_roundtrip': True},
                         'real_api': {'model': {'status': 'success'}}, 'secret_scan': 'passed',
                         'user_profile_unchanged': True, 'normal_shutdown': 'passed',
                         'capabilities': {'pdf': True, 'harness': True},
                         'replays': [{'mode': mode, 'status': 'passed', 'numeric_equal': True,
                                      'network_calls': 0, 'real_harness_loop': True} for mode in ('kernel', 'agent')]}}
    smoke = {'status': 'passed', 'package': {'exe_sha256': build['sha256'], 'mode': 'exe_only'},
             'checks': [{'name': 'offline_demo', 'status': 'passed'}], 'user_profile_modified': False}
    build_path, smoke_path, native_path = (root / name for name in ('dist/MingJing-build.json', 'runs/smoke.json', 'runs/native.json'))
    for path, value in ((build_path, build), (smoke_path, smoke), (native_path, native)):
        write_json(path, value)
    for relative in ('LICENSE', *stage.DOCS, *stage.SAMPLES, 'docs/licenses/python/LICENSE.txt'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fictional stage resource')
    write_json(root / 'runtime/harness/source_snapshot.json', {'upstream_version': '0.1.6-alpha.2',
               'source_sha256': 'upstream-source', 'git_commit': None})
    monkeypatch.setattr(stage, 'configured_secret_values', lambda _: [b'protected-user-token'])
    monkeypatch.setattr(stage, 'scan_embedded_exe', lambda *args: {'status': 'passed'})
    monkeypatch.setattr(stage, 'verify_current_build_inputs', lambda *args: None)
    return root, exe, build_path, smoke_path, native_path


def create(tmp_path, monkeypatch):
    root, exe, build, smoke, native = fixture(tmp_path, monkeypatch)
    out = tmp_path / 'MingJing-stage-1.0.0.dev2'
    return root, out, dict(exe=exe, build_path=build, smoke_path=smoke, native_path=native)


def test_stage_is_explicitly_scoped_and_zip_members_match_hash_manifest(tmp_path, monkeypatch):
    root, out, kwargs = create(tmp_path, monkeypatch)
    # Unallowlisted history/config/real data must remain outside delivery.
    for relative in ('runs/real-response.json', 'config.json', 'secrets.json', 'reports/real.pdf'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'protected-user-token')
    receipt = stage.create_stage(root, out, **kwargs)
    assert receipt['formal_release'] is False
    assert receipt['delivery_kind'] == 'stage_candidate'
    assert 'clean_windows_vm' in receipt['not_executed']
    assert (out / 'MingJing.exe').read_bytes() == kwargs['exe'].read_bytes()
    with zipfile.ZipFile(receipt['archive']) as archive:
        manifest = json.loads(archive.read('MingJing-manifest.json'))
        assert manifest['source_commit'] is None
        assert '尚未登记' in archive.read('SOURCE.md').decode('utf-8')
        assert not {'config.json', 'secrets.json', 'reports/real.pdf', 'runs/real-response.json'} & set(archive.namelist())
        for row in manifest['files']:
            assert hashlib.sha256(archive.read(row['path'])).hexdigest() == row['sha256']
        assert manifest['release_gates']['G5'] == 'not_certified_by_stage'
    assert stage.sha256(Path(receipt['archive'])) == receipt['archive_sha256']


@pytest.mark.parametrize('receipt_name,field,bad', [
    ('build_path', 'sha256', 'different-binary'),
    ('native_path', 'source_sha256', 'different-source'),
    ('native_path', 'runtime_manifest_sha256', 'different-runtime'),
    ('native_path', 'status', 'passed'),
])
def test_rejects_receipt_epoch_mismatch(tmp_path, monkeypatch, receipt_name, field, bad):
    root, out, kwargs = create(tmp_path, monkeypatch)
    path = kwargs[receipt_name]
    value = json.loads(path.read_text(encoding='utf-8'))
    value[field] = bad
    write_json(path, value)
    with pytest.raises(ValueError):
        stage.create_stage(root, out, **kwargs)
    assert not out.exists()


def test_rejects_non_native_or_network_agent_replay(tmp_path, monkeypatch):
    root, out, kwargs = create(tmp_path, monkeypatch)
    value = json.loads(kwargs['native_path'].read_text(encoding='utf-8'))
    value['checks']['replays'][1]['real_harness_loop'] = False
    write_json(kwargs['native_path'], value)
    with pytest.raises(ValueError, match='replay'):
        stage.create_stage(root, out, **kwargs)


def test_secret_in_allowlisted_document_blocks_publish(tmp_path, monkeypatch):
    root, out, kwargs = create(tmp_path, monkeypatch)
    (root / 'docs/USER_GUIDE.md').write_bytes(b'document protected-user-token must not leak')
    with pytest.raises(ValueError, match='credential'):
        stage.create_stage(root, out, **kwargs)
    assert not out.exists()


def test_refuses_existing_folder_or_zip_without_changing_it(tmp_path, monkeypatch):
    root, out, kwargs = create(tmp_path, monkeypatch)
    archive = out.with_name(out.name + '.zip')
    archive.write_bytes(b'existing artifact')
    with pytest.raises(ValueError, match='overwrite'):
        stage.create_stage(root, out, **kwargs)
    assert archive.read_bytes() == b'existing artifact'


def test_current_source_epoch_is_checked_again_after_copy(tmp_path, monkeypatch):
    root, out, kwargs = create(tmp_path, monkeypatch)
    calls = []

    def verify(*_):
        calls.append(True)
        if len(calls) > 1:
            raise ValueError('source changed')

    monkeypatch.setattr(stage, 'verify_current_build_inputs', verify)
    with pytest.raises(ValueError, match='source changed'):
        stage.create_stage(root, out, **kwargs)
    assert not out.exists()
    assert not list(tmp_path.glob('*.stage-*'))


def test_private_key_block_is_rejected_even_without_configured_key(tmp_path):
    path = tmp_path / 'licence.txt'
    path.write_bytes(b'-----BEGIN PRIVATE KEY-----\nencoded-private-material\n')
    with pytest.raises(ValueError, match='Private key'):
        stage.stream_scan(path, [])


def test_encrypted_config_file_is_excluded_even_inside_licence_tree(tmp_path, monkeypatch):
    root, out, kwargs = create(tmp_path, monkeypatch)
    (root / 'docs/licenses/secrets.json').write_bytes(b'encrypted unrelated bytes')
    with pytest.raises(ValueError, match='credential'):
        stage.create_stage(root, out, **kwargs)


def test_executable_change_during_scan_cannot_enter_validated_package(tmp_path, monkeypatch):
    root, out, kwargs = create(tmp_path, monkeypatch)

    def changed(exe, *_):
        exe.write_bytes(b'MZ modified after acceptance')
        return {'status': 'passed'}

    monkeypatch.setattr(stage, 'scan_embedded_exe', changed)
    with pytest.raises(ValueError, match='EXE changed'):
        stage.create_stage(root, out, **kwargs)
    assert not out.exists()
