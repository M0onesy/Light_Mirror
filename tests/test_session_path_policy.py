"""Actual storage escapes must fail before conversation files are created."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from mingjing.config import ConfigError
from mingjing.sessions import FinancialSessions


SID = 'session-' + 'a' * 24


class Configuration:
    def __init__(self, value=None):
        self.value = value or {'security': {'allow_external_paths': True}}

    def get(self):
        return self.value


def restricted(root):
    return {'security': {'allow_external_paths': False, 'allowed_roots': [str(root)],
                         'follow_symlinks': False}}


def directory_link(link, target):
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                capture_output=True, check=False)
        assert result.returncode == 0, 'Controlled Windows junction could not be created'
    else:
        link.symlink_to(target, target_is_directory=True)


def test_constructor_rejects_unapproved_root_before_creating_it(tmp_path):
    allowed, denied = tmp_path/'allowed', tmp_path/'outside-sessions'
    with pytest.raises(ConfigError):
        FinancialSessions(denied, lambda _: None, Configuration(restricted(allowed)))
    assert not denied.exists()


def test_current_policy_change_rejects_session_save_without_output(tmp_path):
    config = Configuration()
    sessions = FinancialSessions(tmp_path/'sessions', lambda _: None, config)
    config.value = restricted(tmp_path/'new-allowed')
    with pytest.raises(ConfigError):
        sessions.save({'session_id': SID})
    assert not list(sessions.root.iterdir())


def test_frozen_policy_is_checked_before_persisting_session_state(tmp_path):
    sessions = FinancialSessions(tmp_path/'sessions', lambda _: None, Configuration())
    with pytest.raises(ConfigError):
        sessions.save({'session_id': SID, 'config': restricted(tmp_path/'other')})
    assert not list(sessions.root.iterdir())


def test_replaced_session_root_junction_cannot_read_or_write_outside(tmp_path):
    root, outside = tmp_path/'sessions', tmp_path/'outside'
    sessions = FinancialSessions(root, lambda _: None, Configuration())
    outside.mkdir()
    original = json.dumps({'session_id': SID, 'status': 'ready'}).encode()
    target = outside/(SID+'.json')
    target.write_bytes(original)
    root.rmdir()  # Empty test-owned directory only; no recursive removal.
    directory_link(root, outside)
    for action in (lambda: sessions.get(SID), lambda: sessions.save({'session_id': SID})):
        with pytest.raises(ConfigError):
            action()
    assert target.read_bytes() == original and len(list(outside.iterdir())) == 1


@pytest.mark.parametrize('escape', ['session', 'harness'])
def test_message_and_harness_junctions_fail_before_preparation(tmp_path, monkeypatch, escape):
    sessions = FinancialSessions(tmp_path/'sessions', lambda _: None, Configuration())
    outside = tmp_path/'outside'
    outside.mkdir()
    if escape == 'session':
        directory_link(sessions.root/SID, outside)
    else:
        (sessions.root/SID).mkdir()
        directory_link(sessions.root/SID/'harness', outside)
    state = {'session_id': SID, 'messages': [], 'config': sessions.config.get(),
             'run_path': str(tmp_path/'not-read')}
    monkeypatch.setattr(sessions, 'get', lambda _: state)
    prepared = []
    monkeypatch.setattr(sessions, '_prepare_message_agent', lambda *a, **kw: prepared.append(True))
    with pytest.raises(ConfigError):
        sessions._answer_inner(SID, 'Read only the approved material')
    assert prepared == [] and not list(outside.iterdir())
    assert not (sessions.root/SID/'message-0000').exists()


def test_approved_current_and_frozen_storage_preserves_roundtrip(tmp_path):
    config = Configuration(restricted(tmp_path))
    sessions = FinancialSessions(tmp_path/'sessions', lambda _: None, config)
    state = {'session_id': SID, 'status': 'ready', 'config': config.get(), 'messages': []}
    sessions.save(state)
    assert sessions.get(SID) == state
