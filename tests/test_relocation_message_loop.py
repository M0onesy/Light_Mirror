"""Moved message cache tested with the real pinned loop and a keyless fixture.

The initial capture calls a local fixture, not a real model provider. Replays
must consume cached requests without contacting even that fixture.
"""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from mingjing.relocation import SCHEMA
from mingjing.relocation_replay import replay_relocated
from test_message_replay import native_conversation, complete_turn
from test_native_runtime_safety import loop_carrier


@pytest.fixture
def tmp_path():
    # Separate agent test runs may prune pytest's shared numbered cache while
    # an offline replay is reading it. Own this fixture for its full lifetime.
    with tempfile.TemporaryDirectory(prefix='mjrel-test-') as home:
        source = Path(home)/'source'
        source.mkdir()
        yield source


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_moved_first_and_continuing_messages_replay_original_tool_loop(native_conversation, tmp_path):
    f = native_conversation
    first = complete_turn(f, '请引用营业收入原文。')
    second = complete_turn(f, '请再引用相同营业收入原文。')
    counts = dict(f.counts)
    before = hashes(tmp_path)
    workspace = tmp_path.parent
    target = workspace/'Light_Mirror'/'evidence'/'message-fixtures'/tmp_path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_path), target)
    location = workspace/'Light_Mirror'/'evidence'/'relocation'/('map-'+tmp_path.name+'.json')
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(json.dumps({'schema': SCHEMA, 'workspace_abs': str(workspace), 'entries': [
        {'old': str(tmp_path), 'new': target.relative_to(workspace).as_posix(), 'kind': 'directory'}
    ]}), encoding='utf8')
    receipts = []
    for message in (first, second):
        out = location.parent/'tests'/(tmp_path.name+'-'+message['message_id'])
        result = replay_relocated(mode='message', run=target/'run', relocation_map=location,
                                  out=out, session_file=target/'conversations'/(f.sid+'.json'),
                                  message_id=message['message_id'])
        assert result['status'] == 'passed', result
        assert result['real_harness_loop'] and result['cached_model_requests'] == 3
        assert result['answer_equal'] and result['references_equal'] and result['tool_trace_equal']
        assert result['relocation']['fingerprints_unchanged']
        assert result['relocation']['network_attempts'] == 0
        receipts.append(result)
    assert counts == f.counts
    after = hashes(target)
    assert before == after
    if destination := os.environ.get('MINGJING_RELOCATION_MESSAGE_TEST_RECEIPT'):
        receipt = Path(destination)
        assert not receipt.exists(), 'Do not overwrite earlier validation evidence'
        receipt.parent.mkdir(parents=True, exist_ok=True)
        encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf8')
        receipt.write_text(json.dumps({'schema': 'mingjing.relocation-message-fixture.v1', 'status': 'passed',
            'provider': 'local_keyless_fixture_not_a_real_provider', 'replays': receipts,
            'capture_counters': counts, 'counters_after_replay': dict(f.counts),
            'original_file_count': len(before), 'original_before_sha256': hashlib.sha256(encode(before)).hexdigest(),
            'original_after_sha256': hashlib.sha256(encode(after)).hexdigest(),
            'original_bytes_unchanged': True}, ensure_ascii=False, indent=2), encoding='utf8')
