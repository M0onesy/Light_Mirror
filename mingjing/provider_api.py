"""Provider diagnostics; a successful echo is never an Agent capability pass."""
from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from urllib.request import Request

from .agent import FinancialAgent
from .audit import digest
from .providers import resolve_route
from .http_transport import urlopen


def discover(config: dict, provider_id: str, secret_resolver) -> dict:
    provider = config.get('providers', {}).get(provider_id)
    if not provider:
        raise ValueError('unknown provider')
    key = secret_resolver(provider['credential_ref']) or ''
    base = provider['base_url'].rstrip('/')
    headers = {'Content-Type': 'application/json'}
    if provider['protocol'] == 'anthropic-messages':
        headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
    else:
        headers['Authorization'] = 'Bearer ' + key
    request = Request(base + '/models', headers=headers)
    try:
        with urlopen(request, timeout=min(provider.get('timeout_s', 30), 30),
                     verify_tls=provider.get('verify_tls', True)) as response:
            data = json.loads(response.read())
        models = [{"id": item['id'], "capability_status": 'declared_only'} for item in data.get('data', []) if isinstance(item, dict) and isinstance(item.get('id'), str)]
        return {"ok": bool(models), "models": models, "agent_capability": 'not_tested'}
    except Exception as exc:
        return {"ok": False, "models": [], "error": type(exc).__name__, "agent_capability": 'not_executed'}


def test_connection(config: dict, provider_id: str, secret_resolver, root: Path, model: str | None = None, *, lifecycle=None) -> dict:
    """One no-tool turn through the same native protocol adapter as the Agent.

    A complete text response proves connectivity only. It never marks tool
    capability passed and never falls back to another provider or protocol.
    """
    cfg = copy.deepcopy(config)
    provider = cfg.get('providers', {}).get(provider_id)
    if not provider:
        raise ValueError('unknown provider')
    selected = model or (provider['models'][0]['id'] if provider.get('models') else '')
    if not selected:
        return {'ok': False, 'status': 'not_executed', 'reason': 'no_selected_model', 'agent_capability': 'not_tested'}
    # Only the fixed diagnostic prompt is sent. This copied flag permits a
    # connection test before the user authorizes real financial materials.
    provider['material_access'] = True
    cfg['agent']['routing'] = {'default': {'provider': provider_id, 'model': selected},
                               'planner': None, 'verifier': None, 'reporter': None}
    cfg['agent']['budget'].update(max_model_calls=1, max_tool_calls=0, max_runtime_s=90, max_total_tokens=4096)
    run_id = 'connection-' + uuid.uuid4().hex[:12]
    root = root / run_id
    root.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / 'samples'
    if not source.is_dir():
        import sys
        source = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'samples'
    agent = FinancialAgent({'draft': str(source / 'demo_draft.md'), 'corpus': str(source / 'corpus'),
                            'company': '明镜示例公司', 'period': '2024Y', 'report_date': '2025-03-31'},
                           cfg, secret_resolver, root, run_id=run_id)
    started, native_started, session_opened, observed = time.monotonic(), False, False, False
    sid = run_id + '-probe'
    result = {'provider': provider_id, 'protocol': provider['protocol'], 'model': selected,
              'run_id': run_id, 'artifact_path': str(root), 'agent_capability': 'not_tested',
              'tool_roundtrip': False, 'material': 'fictitious_connection_probe'}
    try:
        if lifecycle:
            lifecycle('start', agent)
            observed = True
        agent.prepare()
        agent.session_records = []
        agent.start_runtime()
        native_started = True
        route = resolve_route(cfg)
        route['max_tokens'] = min(route.get('max_tokens') or 2048, 2048)
        agent.dispatcher.register_session(sid, 'probe', [])
        agent.runtime.open_session(session_id=sid, role='probe', run_id=run_id,
            resource_lock=agent.resource_lock, route=route, tool_definitions=[], allowed_tools=[],
            budget=agent.budget, max_steps=1,
            persona='你是明镜连接诊断助手。只回复测试文本，不读取材料，不调用工具。')
        session_opened = True
        response = agent.runtime.prompt(sid, '请仅回复：MingJing connected', timeout_s=60,
                                        cancel_event=agent.cancelled)
        reply, finish = response.get('final_response'), response.get('finish_reason')
        complete = finish is not None and finish not in {'error', 'aborted', 'hook', 'cancelled', 'max-tokens', 'max-steps'}
        readable = isinstance(reply, str) and bool(reply.strip())
        ok = complete and readable
        result.update(ok=ok, status='passed' if ok else 'failed', reply_received=readable,
                      reply_sha256=digest(reply) if readable else None, finish_reason=finish,
                      reason=None if ok else 'incomplete_turn' if not complete else 'empty_content')
        agent.audit.emit('connection_probe', provider=provider_id, protocol=provider['protocol'], model=selected,
                         status=result['status'], response_hash=result['reply_sha256'], finish_reason=finish,
                         tools_enabled=False, agent_capability='not_tested')
    except Exception as exc:
        result.update(ok=False, status='failed', reason=type(exc).__name__)
    finally:
        if session_opened:
            try:
                agent.runtime.close_session(sid)
            except Exception:
                pass
            agent.dispatcher.sessions[sid]['active'] = False
        try:
            if agent.runtime:
                agent.runtime.close()
        finally:
            if observed:
                lifecycle('end', agent)
    result.update(native_runtime=native_started, latency_ms=round((time.monotonic() - started) * 1000),
                  usage={'model_requests': agent.native_request_count,
                         'tokens': agent.total_tokens if agent.usage_known else 'unknown'})
    (root / 'connection.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def test_agent(config: dict, provider_id: str, secret_resolver, root: Path, model: str | None = None, *, lifecycle=None) -> dict:
    cfg = copy.deepcopy(config)
    provider = cfg.get('providers', {}).get(provider_id)
    if not provider:
        raise ValueError('unknown provider')
    selected = model or (provider['models'][0]['id'] if provider.get('models') else '')
    if not selected:
        return {"ok": False, "status": 'not_executed', "reason": 'no_selected_model'}
    cfg['agent']['routing'] = {"default": {"provider": provider_id, "model": selected}, "planner": None, "verifier": None, "reporter": None}
    cfg['agent']['budget'].update(max_model_calls=12, max_tool_calls=20, max_runtime_s=180, max_total_tokens=24000)
    run_id = 'capability-' + uuid.uuid4().hex[:12]
    root = root/run_id
    root.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1]/'samples'
    if not source.is_dir():
        import sys
        source = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))/'samples'
    agent = FinancialAgent({"draft": str(source/'demo_draft.md'), "corpus": str(source/'corpus'),
                            "company": '明镜示例公司', "period": '2024Y', "report_date": '2025-03-31'},
                           cfg, secret_resolver, root, run_id=run_id)
    started = time.monotonic()
    observed = False
    try:
        if lifecycle:
            lifecycle('start', agent)
            observed = True
        agent.prepare()
        agent.session_records = []
        agent.start_runtime()
        agent.run_role('probe', [], '执行一次真实金融工具闭环，仅使用虚构演示材料。先调用 mj_list_reports。'
                       '然后 mj_search_blocks 搜索营业收入，最后把搜索结果中实际返回的 block_id 传给 mj_read_context 的 mention_id。'
                       '必须连续完成这些工具调用，然后用一句话说明证据来自哪个页面。不得猜测资源ID。')
        trace = agent.dispatcher.trace
        calls = [event['tool'] for event in trace if event['status'] == 'success']
        search = [event for event in trace if event['tool'] == 'mj_search_blocks' and isinstance(event.get('output'), list)]
        returned_ids = {block['block_id'] for event in search for block in event['output']}
        dependent = any(event['tool'] == 'mj_read_context' and event.get('input', {}).get('mention_id') in returned_ids for event in trace)
        ok = {'mj_list_reports', 'mj_search_blocks', 'mj_read_context'} <= set(calls) and dependent
        result = {"ok": ok, "status": 'passed' if ok else 'failed', "provider": provider_id, "model": selected,
                  "native_runtime": True, "tool_roundtrip": dependent, "tool_calls": calls,
                  "tool_trace_hash": digest(trace), "latency_ms": round((time.monotonic()-started)*1000),
                  "usage": 'unknown', "run_id": run_id, "artifact_path": str(root)}
    except Exception as exc:
        result = {"ok": False, "status": 'failed', "provider": provider_id, "model": selected,
                  "reason": type(exc).__name__, "run_id": run_id, "artifact_path": str(root)}
    finally:
        try:
            if agent.runtime:
                agent.runtime.close()
        finally:
            if observed:
                lifecycle('end', agent)
    (root/'capability.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if hasattr(agent, 'dispatcher'):
        (root/'tool_trace.json').write_text(json.dumps(agent.dispatcher.trace, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
