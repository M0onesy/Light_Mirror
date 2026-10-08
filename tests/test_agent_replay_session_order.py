import copy

import pytest

from mingjing.agent_replay import logical_session_trace


def records(prefix):
    return [{'session_id': prefix+'-A', 'role': 'planner', 'mention_ids': ['m1', 'm2']},
            {'session_id': prefix+'-B', 'role': 'verifier', 'mention_ids': ['m1', 'm2']},
            {'session_id': prefix+'-C', 'role': 'coordinator', 'mention_ids': ['m1', 'm2']}]


def trace(prefix, order):
    events = {
        'A1': {'session_id': prefix+'-A', 'role': 'planner', 'tool': 'mj_list_mentions', 'input': {}, 'output': {'mentions': ['masked']}, 'status': 'success'},
        'A2': {'session_id': prefix+'-A', 'role': 'planner', 'tool': 'mj_submit_binding', 'input': {'mention_id': 'm1'}, 'output': {'submitted': True}, 'status': 'success'},
        'B1': {'session_id': prefix+'-B', 'role': 'verifier', 'tool': 'mj_read_context', 'input': {'mention_id': 'm1'}, 'output': {'text': 'masked'}, 'status': 'success'},
        'B2': {'session_id': prefix+'-B', 'role': 'verifier', 'tool': 'mj_submit_binding', 'input': {'mention_id': 'm1'}, 'output': {'submitted': True}, 'status': 'success'},
        'C1': {'session_id': prefix+'-C', 'role': 'coordinator', 'tool': 'mj_get_fact', 'input': {'binding_id': 'm1'}, 'output': {'fact': 'f1'}, 'status': 'success'},
        'C2': {'session_id': prefix+'-C', 'role': 'coordinator', 'tool': 'mj_verify', 'input': {'binding_id': 'm1'}, 'output': {'status': 'verified'}, 'status': 'success'},
    }
    return [copy.deepcopy(events[key]) for key in order]


def test_replay_allows_only_cross_session_interleaving_and_changed_physical_ids():
    original = logical_session_trace(trace('old', ['A1', 'B1', 'A2', 'B2', 'C1', 'C2']), records('old'))
    replayed = logical_session_trace(trace('new', ['B1', 'B2', 'A1', 'A2', 'C1', 'C2']), list(reversed(records('new'))))
    assert original == replayed


@pytest.mark.parametrize('changed', ['binder_order', 'dependent_tools', 'input', 'output', 'scope', 'missing_call'])
def test_replay_rejects_order_input_output_or_scope_changes_inside_logical_session(changed):
    original_trace = trace('old', ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'])
    replay_trace = trace('new', ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'])
    replay_records = records('new')
    if changed == 'binder_order':
        replay_trace[0], replay_trace[1] = replay_trace[1], replay_trace[0]
    elif changed == 'dependent_tools':
        replay_trace[4], replay_trace[5] = replay_trace[5], replay_trace[4]
    elif changed == 'input':
        replay_trace[4]['input']['binding_id'] = 'm2'
    elif changed == 'output':
        replay_trace[5]['output']['status'] = 'abstained'
    elif changed == 'scope':
        replay_records[0]['mention_ids'] = ['m2', 'm1']
    else:
        replay_trace.pop()
    assert logical_session_trace(original_trace, records('old')) != logical_session_trace(replay_trace, replay_records)


def test_replay_keeps_repeated_same_scope_sessions_separate():
    original_records = records('old') + [{'session_id': 'old-A-repair', 'role': 'planner', 'mention_ids': ['m1', 'm2']}]
    replay_records = records('new') + [{'session_id': 'new-A-repair', 'role': 'planner', 'mention_ids': ['m1', 'm2']}]
    original_trace = trace('old', ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'])
    replay_trace = trace('new', ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'])
    original_trace.append({**original_trace[0], 'session_id': 'old-A-repair'})
    replay_trace.append({**replay_trace[0], 'session_id': 'new-A-repair'})
    assert logical_session_trace(original_trace, original_records) == logical_session_trace(replay_trace, replay_records)
    replay_trace[-1]['session_id'] = 'new-A'
    assert logical_session_trace(original_trace, original_records) != logical_session_trace(replay_trace, replay_records)


@pytest.mark.parametrize('invalid', ['unmapped', 'role', 'duplicate_session'])
def test_replay_refuses_untrusted_trace_session_identity(invalid):
    calls, sessions = trace('old', ['A1']), records('old')
    if invalid == 'unmapped':
        calls[0]['session_id'] = 'unknown'
    elif invalid == 'role':
        calls[0]['role'] = 'verifier'
    else:
        sessions.append(sessions[0])
    with pytest.raises(ValueError, match='session'):
        logical_session_trace(calls, sessions)
