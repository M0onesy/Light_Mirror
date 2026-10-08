"""Real Windows IO beyond MAX_PATH, without changing public resource paths."""
import json
import os
from pathlib import Path
import shutil

import pytest

from mingjing.agent import write_json
from mingjing.audit import AuditLog
from mingjing.config import (AppConfig, ConfigError, _atomic_write_json, canonicalize_path,
                            load_config, save_config, validate_path)
from mingjing.file_io import atomic_write_text, io_path, record_path
from mingjing import reparse
from test_reparse_packets import source


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Real Windows long-path IO')


@pytest.fixture
def long_folder(tmp_path):
    anchor = tmp_path/'owned-long-output'
    folder = anchor
    while len(str(folder.absolute())) < 320:
        folder /= 'financial-output-' + 'x' * 36
    io_path(folder).mkdir(parents=True)
    assert len(str(folder)) > 260
    try:
        yield folder
    finally:
        assert anchor.absolute().is_relative_to(tmp_path.resolve())
        shutil.rmtree(io_path(anchor, force_extended=True))


def test_real_long_reparse_publish_reuse_load_and_audit_keep_canonical_resources(source, long_folder):
    before = [fact.to_dict() for fact in source['baseline_facts']]
    source_bytes = Path(source['source_resources'][0]['path']).read_bytes()
    args = dict(source, output_root=long_folder/'reparse/parent')
    summary = reparse.create_reparse_packet(**args)
    assert summary['status'] == 'ready' and summary['new_facts_count'] > 0
    located = reparse.resolve_reparse_packet(args['output_root'], summary['packet_id'])
    assert len(str(located)) > 390 and not str(located).startswith('\\\\?\\')
    raw = io_path(located).read_bytes()
    assert reparse.create_reparse_packet(**args) == summary
    assert io_path(located).read_bytes() == raw
    assert reparse.list_reparse_packets(args['output_root'], expected_source_generation=source['source_generation']) == [summary]
    facts = reparse.load_reparse_packet(located, source_resources=source['source_resources'],
        baseline_facts=source['baseline_facts'], company=source['company'], as_of_date=source['as_of_date'],
        expected_source_generation=source['source_generation'])
    assert any(fact.account == 'REVENUE' and fact.period == '2024Y' for fact in facts)
    assert all(not fact.source_path.startswith('\\\\?\\') for fact in facts)
    audit_root = args['output_root']/'audit'/summary['packet_id']
    audit = AuditLog(audit_root, source['source_generation']['run_id'])
    assert audit.counter == 2 and not str(audit.path).startswith('\\\\?\\')
    audit.session('long_path_verified', packet_id=summary['packet_id'])
    assert json.loads(io_path(audit.session_path).read_text(encoding='utf-8'))['packet_id'] == summary['packet_id']
    assert [fact.to_dict() for fact in source['baseline_facts']] == before
    assert Path(source['source_resources'][0]['path']).read_bytes() == source_bytes
    assert not list(io_path(args['output_root']).glob('*.tmp'))


def test_long_reparse_cancellation_before_commit_preserves_existing_files(source, long_folder, monkeypatch):
    summary = reparse.create_reparse_packet(**source)
    packet = json.loads(io_path(reparse.resolve_reparse_packet(source['output_root'], summary['packet_id'])).read_text(encoding='utf-8'))
    sentinel = long_folder/'existing-frozen.json'
    atomic_write_text(sentinel, '{"frozen":true}')
    original = io_path(sentinel).read_bytes()
    def cancelled(*_args, **_kwargs):
        raise InterruptedError('Cancellation after serialization, before atomic publish')
    monkeypatch.setattr(reparse, '_checkpoint', cancelled)
    with pytest.raises(InterruptedError):
        reparse._publish_packet(long_folder, packet, None, None)
    assert io_path(sentinel).read_bytes() == original
    assert [record_path(path) for path in io_path(long_folder).iterdir()] == [sentinel]


@pytest.mark.parametrize('writer', [write_json, _atomic_write_json], ids=['agent-checkpoint', 'configuration'])
def test_long_atomic_failure_preserves_last_valid_json(long_folder, monkeypatch, writer):
    target = long_folder/'state.json'
    writer(target, {'stage': 'valid', 'requests': 10})
    original = io_path(target).read_bytes()
    replace = Path.replace
    def deny(source, destination):
        if record_path(destination) == target:
            error = PermissionError('Persistent Windows sharing denial')
            error.winerror = 32
            raise error
        return replace(source, destination)
    monkeypatch.setattr(Path, 'replace', deny)
    monkeypatch.setattr('mingjing.file_io.time.sleep', lambda _: None)
    with pytest.raises(PermissionError):
        writer(target, {'stage': 'unwritten', 'requests': 11})
    assert io_path(target).read_bytes() == original
    assert not list(io_path(long_folder).glob('*.tmp'))


def test_long_path_validation_preserves_type_and_allowed_root_guards(long_folder, tmp_path):
    target = long_folder/'resource.json'
    write_json(target, {'resource': 'financial'})
    assert canonicalize_path(target, must_exist=True) == target
    assert validate_path(target, allowed_roots=[str(tmp_path)], must_exist=True, kind='file') == target
    with pytest.raises(ConfigError, match='outside allowed roots'):
        validate_path(target, allowed_roots=[str(tmp_path/'other')], must_exist=True, kind='file')
    with pytest.raises(ConfigError, match='expected a file'):
        validate_path(long_folder, must_exist=True, kind='file')


def test_long_configuration_reloads_saved_settings_without_losing_canonical_paths(long_folder):
    target = long_folder/'configuration/config.json'
    config = AppConfig().ensure_defaults()
    config.workspace.output_root = str(long_folder/'reports')
    assert save_config(config, target) == target
    reloaded = load_config(target)
    assert reloaded.workspace.output_root == config.workspace.output_root
    assert not reloaded.workspace.output_root.startswith('\\\\?\\')


def test_long_audit_open_failure_does_not_advance_in_memory_chain(long_folder, monkeypatch):
    audit = AuditLog(long_folder, 'long-path-run')
    audit.emit('before')
    before = io_path(audit.path).read_bytes(), audit.counter, audit.prev_hash
    native_open = Path.open
    def denied(path, mode='r', *args, **kwargs):
        if record_path(path) == audit.path and mode == 'a':
            raise PermissionError('Audit path temporarily unavailable')
        return native_open(path, mode, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', denied)
        with pytest.raises(PermissionError):
            audit.emit('unwritten')
    assert (io_path(audit.path).read_bytes(), audit.counter, audit.prev_hash) == before
    assert audit.emit('after')['event_id'] == 'event-000002'
    assert AuditLog(long_folder, 'long-path-run').counter == 2
