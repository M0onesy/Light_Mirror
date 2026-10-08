"""Checkpoint durability when Windows readers temporarily hold the old file."""
import json
import os
from pathlib import Path
import threading
import time

import pytest

from mingjing.agent import write_json


@pytest.mark.skipif(os.name != 'nt', reason='Windows destination-sharing semantics')
@pytest.mark.parametrize('conversation', [False, True])
def test_checkpoint_survives_a_live_windows_progress_reader(tmp_path, conversation):
    from mingjing.sessions import FinancialSessions
    sid = 'session-' + 'a' * 24
    target = tmp_path / (sid + '.json' if conversation else 'agent_state.json')
    manager = FinancialSessions(tmp_path, None, None) if conversation else None

    def save(data):
        if manager:
            manager.save({'session_id': sid, **data})
        else:
            write_json(target, data)

    save({'stage': 'before', 'requests': 9})
    reader = target.open('rb')
    released = threading.Event()

    def release_reader():
        time.sleep(0.07)
        reader.close()
        released.set()

    thread = threading.Thread(target=release_reader)
    thread.start()
    try:
        save({'stage': 'resumed', 'requests': 10})
        assert released.is_set()
        expected = {'stage': 'resumed', 'requests': 10}
        if conversation:
            expected['session_id'] = sid
        assert json.loads(target.read_text(encoding='utf-8')) == expected
        assert not list(tmp_path.glob('*.tmp'))
    finally:
        thread.join(timeout=2)
        reader.close()


@pytest.mark.parametrize('winerror', [5, 32, 33, None])
def test_persistent_checkpoint_failure_preserves_the_last_valid_state(tmp_path, monkeypatch, winerror):
    target = tmp_path / 'agent_state.json'
    write_json(target, {'stage': 'last-valid', 'requests': 9})
    original = target.read_bytes()
    attempts, sleeps = [], []
    native_replace = Path.replace

    def deny_replace(source, destination):
        if destination == target:
            attempts.append(source)
            error = PermissionError('Test persistent write denial')
            if winerror is not None:
                error.winerror = winerror
            raise error
        return native_replace(source, destination)

    monkeypatch.setattr(Path, 'replace', deny_replace)
    monkeypatch.setattr('mingjing.agent.time.sleep', sleeps.append)
    with pytest.raises(PermissionError):
        write_json(target, {'stage': 'unwritten', 'requests': 10})
    assert target.read_bytes() == original
    assert not list(tmp_path.glob('*.tmp'))
    assert len(attempts) == (8 if winerror is not None else 1)
    assert len(sleeps) == (7 if winerror is not None else 0)
    assert sum(sleeps) <= 1
