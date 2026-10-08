"""A credential resolved after turn preparation cannot become a tool query."""
import json
import threading

import pytest

from mingjing.financial_tools import ToolDenied
from test_agent_lifecycle import lifecycle, read
from test_session_source_followup import conversation


@pytest.mark.parametrize('cached_replay', [False, True])
def test_followup_dispatcher_observes_later_resolved_credentials(lifecycle, tmp_path, cached_replay):
    sessions, sid, result = conversation(lifecycle)
    state = sessions.get(sid)
    out = tmp_path / 'guarded-message'
    agent = sessions._prepare_message_agent(state, out, result, cancel_event=threading.Event(),
        replay_cache={} if cached_replay else None)
    assert agent.dispatcher.secret_values is agent.resolved_secrets
    credential = 'controlled-fake-credential-only-for-local-test'
    agent.resolved_secrets.append(credential)
    envelope = {'session_id': sid, 'role': 'question', 'run_id': state['run_id'],
                'resource_lock': state['resource_lock'], 'call_id': 'credential-echo',
                'tool_name': 'mj_search_blocks', 'params': {'query': credential, 'limit': 1}}
    with pytest.raises(ToolDenied, match='credential'):
        agent.dispatcher.dispatch(envelope)
    assert agent.dispatcher.trace[-1]['status'] == 'denied'
    assert credential not in json.dumps(agent.dispatcher.trace)
    assert all(credential not in path.read_text(encoding='utf-8')
               for path in out.rglob('*.jsonl'))
    assert read(lifecycle.out / 'results.json') == result
