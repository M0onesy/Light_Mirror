"""Offline model-cache playback through the actual Harness tool loop."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .agent import FinancialAgent, orchestration_fingerprint, validate_task_semantics
from .audit import digest
from .config import configured_temp_root
from .replay import _analysis_differences, _check_audit, _check_integrity, _field_differences


def logical_session_trace(trace: list[dict], records: list[dict]) -> dict:
    """Keep each role/scope session's strict call sequence across interleaving.

    Session IDs and call IDs differ between executions. Map sessions by their
    trusted role, ordered mention IDs and occurrence within that role/scope.
    No call is sorted or treated as an unordered set inside a session.
    """
    mapping, counts, result = {}, {}, {}
    for record in records:
        sid, role, ids = record.get('session_id'), record.get('role'), record.get('mention_ids')
        if not isinstance(sid, str) or sid in mapping or not isinstance(role, str) or not isinstance(ids, list):
            raise ValueError('Invalid or duplicate trusted replay session mapping')
        if any(not isinstance(mid, str) for mid in ids):
            raise ValueError('Invalid replay session mention scope')
        scope = (role, tuple(ids))
        ordinal = counts.get(scope, 0)
        counts[scope] = ordinal + 1
        key = (*scope, ordinal)
        mapping[sid] = key
        result[key] = []
    for event in trace:
        sid = event.get('session_id')
        key = mapping.get(sid)
        if key is None or key[0] != event.get('role'):
            raise ValueError('Tool trace has no matching trusted logical session')
        result[key].append({name: event.get(name) for name in ('role', 'tool', 'input', 'output', 'status')})
    return result


def replay_agent(run: Path | str) -> dict:
    root = Path(run).resolve()
    required = ['agent_lock.json', 'agent_state.json', 'agent_model_cache.json', 'bindings.json', 'runtime.json']
    missing = [name for name in required if not (root/name).is_file()]
    if missing:
        return {"status": 'failed', "mode": 'agent_replay', "reason": 'missing_native_agent_cache', "missing": missing, "network_calls": 0}
    read = lambda name: json.loads((root/name).read_text(encoding='utf-8'))
    lock, state, original = read('agent_lock.json'), read('agent_state.json'), read('results.json')
    issues = _check_integrity(root, read('run_manifest.json')) + _check_audit(root, original['run_id'])
    try:
        validate_task_semantics(lock, state['request'])
    except ValueError as exc:
        issues.append({'kind': 'task_semantics_lock_mismatch', 'reason': str(exc)})
    if lock.get('agent') != orchestration_fingerprint(lock['config']):
        issues.append({'kind': 'agent_runtime_or_plugin_changed'})
    if issues:
        return {"status": 'failed', "mode": 'agent_replay', "reason": 'integrity_mismatch', "issues": issues, "network_calls": 0}
    if original.get('model', {}).get('status') != 'success':
        return {"status": 'failed', "mode": 'agent_replay', "reason": 'original_not_native_success', "network_calls": 0}
    cache = read('agent_model_cache.json')
    if not cache:
        return {"status": 'failed', "mode": 'agent_replay', "reason": 'empty_model_cache', "network_calls": 0}
    request = dict(state['request'], draft=original['draft'])
    result = {"status": 'failed', "mode": 'agent_replay', "network_calls": 0, "real_harness_loop": False}
    with tempfile.TemporaryDirectory(prefix='mingjing-agent-replay-', dir=configured_temp_root(lock['config'])) as temporary:
        agent = FinancialAgent(request, lock['config'], lambda ref: (_ for _ in ()).throw(PermissionError('Replay credentials forbidden')),
                               Path(temporary), run_id=original['run_id'], replay_cache=cache,
                               frozen_knowledge=lock.get('knowledge'))
        try:
            agent.prepare()
            if agent.snapshot.get('task_semantics') != lock.get('task_semantics'):
                raise ValueError('Frozen executed business policies changed')
            if agent.snapshot.get('facts_digest') != lock.get('facts_digest'):
                raise ValueError('Frozen fact index changed')
            agent.resource_lock = lock['resource_lock']
            agent.dispatcher.resource_lock = agent.resource_lock
            agent.start_runtime()
            agent.bind()
            agent.calculate()
            agent.report()
            differences = _field_differences(original['results'], agent.results)
            differences.extend(_analysis_differences(original.get('analysis'), agent.analysis))
            execution_equal = (original.get('pending_claim_ids', []) == agent.pending_claim_ids
                               and original.get('execution', {}).get('preflight_claim_ids', []) == agent.preflight_claim_ids
                               and original.get('execution', {}).get('native_analysis_completed', False) == bool(agent.native_analysis_sessions()))
            original_bindings = read('bindings.json')
            bindings_equal = original_bindings['A'] == agent.dispatcher.bindings['planner'] and original_bindings['B'] == agent.dispatcher.bindings['verifier']
            report_equal = original.get('agent_report') == agent.dispatcher.templates.get('reporter')
            trace_equal = logical_session_trace(read('tool_trace.json'), state['sessions']) == logical_session_trace(
                agent.dispatcher.trace, agent.session_records)
            result.update(status='passed' if not differences and bindings_equal and report_equal and trace_equal and execution_equal else 'failed',
                          real_harness_loop=True, numeric_equal=not differences, bindings_equal=bindings_equal,
                          differences=differences, report_equal=report_equal, tool_trace_equal=trace_equal,
                          execution_equal=execution_equal, analysis_recomputed=agent.analysis is not None,
                          tool_calls=len(agent.dispatcher.trace),
                          cached_model_requests=agent.native_request_count, result_digest=digest(agent.results))
        except Exception as exc:
            result.update(reason=type(exc).__name__, details=agent.redact(str(exc)),
                          tool_calls=len(agent.dispatcher.trace) if hasattr(agent, 'dispatcher') else 0)
        finally:
            if agent.runtime:
                agent.runtime.close()
    (root/'agent_replay.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
