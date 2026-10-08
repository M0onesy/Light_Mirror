import hashlib
import json

import pytest

from scripts.upstream_source import regular_source, verify_upstream


def archive(tmp_path):
    upstream = tmp_path / 'upstream'
    upstream.mkdir()
    (upstream / 'source.ts').write_bytes(b'export const value = 1;\n')
    files = [{'path': 'source.ts', 'bytes': 24, 'sha256': hashlib.sha256((upstream / 'source.ts').read_bytes()).hexdigest()}]
    files[0]['bytes'] = (upstream / 'source.ts').stat().st_size
    lock = {'files': files, 'source_sha256': hashlib.sha256(json.dumps(files, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest(),
            'upstream_version': 'fixture', 'git_commit': None}
    path = tmp_path / 'runtime/harness/source_snapshot.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(lock), encoding='utf-8')
    return upstream


def test_changed_upstream_bytes_are_rejected_before_consumption(tmp_path):
    upstream = archive(tmp_path)
    assert verify_upstream(upstream, tmp_path)['status'] == 'passed'
    (upstream / 'source.ts').write_bytes(b'export const value = 2;\n')
    with pytest.raises(ValueError, match='differs from lock'):
        verify_upstream(upstream, tmp_path)


@pytest.mark.parametrize('relative', ['../secret.txt', '/tmp/secret.txt', 'C:/secret.txt', 'nested\\secret.txt'])
def test_source_archive_cannot_read_outside_registered_root(tmp_path, relative):
    with pytest.raises(ValueError, match='unsafe path'):
        regular_source(tmp_path, relative)


def test_inventory_hash_is_checked_before_source_access(tmp_path):
    upstream = archive(tmp_path)
    path = tmp_path / 'runtime/harness/source_snapshot.json'
    lock = json.loads(path.read_text())
    lock['files'][0]['path'] = '../outside'
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match='inventory hash'):
        verify_upstream(upstream, tmp_path)
