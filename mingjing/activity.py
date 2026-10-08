"""Safe activity DTOs, separate from full audit/runtime event payloads."""
from __future__ import annotations


def project_events(events: list[dict]) -> list[dict]:
    """Expose lifecycle and tool identities without prompts or model content."""
    result, tool_names = [], {}
    common = {'time', 'run_id', 'session_id', 'role', 'call_id', 'tool', 'status', 'percent',
              'name', 'reason', 'error', 'selected_mode', 'requested_mode', 'partial', 'native_agent',
              'next_action', 'source', 'sha256', 'text'}
    host_kinds = {'started', 'phase', 'analysis_intent', 'tool', 'partial', 'fallback', 'completed',
                  'failed', 'cancelled', 'log'}
    for index, original in enumerate(events):
        if not isinstance(original, dict):
            continue
        kind = original.get('event')
        item = {'index': index, 'event': kind}
        if kind != 'agent_event':
            if kind not in host_kinds:
                continue
            item.update({key: value for key, value in original.items()
                         if key in common and isinstance(value, (str, int, float, bool, type(None)))})
            if isinstance(item.get('text'), str):
                item['text'] = item['text'][:2000]
            result.append(item)
            continue
        detail = original.get('detail') or {}
        if not isinstance(detail, dict):
            continue
        event = detail.get('event') or {}
        if not isinstance(event, dict):
            continue
        native_kind = event.get('type')
        if native_kind not in {'tool/call', 'tool/result', 'turn/start', 'turn/end', 'step/start', 'step/end'}:
            # Includes assistant/message, system prompt, request headers,
            # reasoning metadata and stream deltas. The audit retains them.
            continue
        data = event.get('data') or {}
        if not isinstance(data, dict):
            data = {}
        session_id = detail.get('sessionId') if isinstance(detail.get('sessionId'), str) else None
        item.update(time=original.get('time'), run_id=original.get('run_id'),
                    session_id=session_id, native_type=native_kind,
                    event=native_kind.replace('/', '_'))
        for key in ('turn', 'step'):
            if isinstance(data.get(key), int):
                item[key] = data[key]
        if native_kind == 'tool/call':
            name, call_id = data.get('name'), data.get('callId')
            if isinstance(name, str) and name.startswith('mj_') and isinstance(call_id, str):
                tool_names[(item['session_id'], call_id)] = name
                item.update(tool=name, call_id=call_id, status='running')
        elif native_kind == 'tool/result':
            message = data.get('message') or {}
            source = message.get('source') or {} if isinstance(message, dict) else {}
            call_id = source.get('callId') if isinstance(source, dict) else None
            if not isinstance(call_id, str):
                call_id = None
            content = message.get('content') or [] if isinstance(message, dict) else []
            failed = any(block.get('isError') is True for block in content if isinstance(block, dict))
            item.update(call_id=call_id, tool=tool_names.get((item['session_id'], call_id)),
                        status='failed' if failed else 'completed')
        else:
            item['status'] = 'running' if native_kind.endswith('/start') else 'completed'
        result.append(item)
    return result
