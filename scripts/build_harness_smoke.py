"""Keyless black-box smoke through the real official dsh AgentLoop/profile."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.harness_client import HarnessRuntime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "runs/harness-native-smoke")
    parser.add_argument("--protocol", choices=("deepseek", "openai-completions"), default="deepseek")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    state = {"calls": [], "tools": [], "cache": {}, "credentials": 0}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = request["messages"]
            previous = [m for m in messages if m.get("role") == "tool"]
            state["calls"].append({"path": self.path, "step": len(previous), "model": request["model"], "tools": [t['function']['name'] for t in request.get('tools', [])]})
            if len(previous) == 0:
                name, params = "mj_list_reports", {}
            elif len(previous) == 1:
                assert "report-native-001" in previous[-1]["content"]
                name, params = "mj_search_blocks", {"query": "report-native-001", "limit": 2}
            elif len(previous) == 2:
                assert "block-native-008" in previous[-1]["content"]
                name, params = "mj_read_context", {"mention_id": "block-native-008"}
            else:
                name, params = None, None
            if name:
                delta = {"tool_calls": [{"index": 0, "id": f"call-native-{len(previous)}", "type": "function", "function": {"name": name, "arguments": json.dumps(params)}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"content": "Financial evidence chain completed."}, "stop"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                {"id": "mock-dsh-native", "model": "native-fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "mock-dsh-native", "model": "native-fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50}},
            ]
            for event in events:
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    tools = [
        {"name": "mj_list_reports", "description": "List locked report resource IDs.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "mj_search_blocks", "description": "Search a locked report resource.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"], "additionalProperties": False}},
        {"name": "mj_read_context", "description": "Read the block ID returned by search.", "inputSchema": {"type": "object", "properties": {"mention_id": {"type": "string"}}, "required": ["mention_id"], "additionalProperties": False}},
    ]
    spec = {"session_id": "native-smoke-root", "role": "probe", "run_id": "native-smoke", "resource_lock": "smoke-lock",
            "route": {"provider": "fixture", "model": "native-fixture", "protocol": args.protocol, "base_url": f"http://127.0.0.1:{server.server_port}", "credential_ref": "fixture-key", "max_tokens": 1000, "reasoning": "off", "temperature": 0, "timeout_s": 20, "retries": 0},
            "tool_definitions": tools, "allowed_tools": [t["name"] for t in tools], "max_steps": 8}

    def dispatch(method, params):
        if method == "mj/credentials.resolve":
            assert params["credential_ref"] == "fixture-key"
            state["credentials"] += 1
            return {"api_key": "keyless-fixture-only"}
        if method == "mj/model.cache.get":
            chunks = state["cache"].get(params["request_hash"])
            return {"hit": bool(chunks), **({"chunks": chunks} if chunks else {})}
        if method == "mj/model.cache.put":
            state["cache"][params["request_hash"]] = params["chunks"]
            return {}
        assert method == "mj/tool.dispatch"
        assert params["role"] == "probe" and params["run_id"] == "native-smoke" and params["resource_lock"] == "smoke-lock"
        state["tools"].append(params)
        if params["tool_name"] == "mj_list_reports":
            return {"reports": [{"id": "report-native-001", "company": "FinancialFixture"}]}
        if params["tool_name"] == "mj_search_blocks":
            assert params["params"]["query"] == "report-native-001"
            return {"blocks": [{"id": "block-native-008"}]}
        assert params["params"]["mention_id"] == "block-native-008"
        return {"block_id": "block-native-008", "text": "Revenue [masked] yuan, fiscal 2023.", "evidence_id": "fixture:p8"}

    notifications = []
    config = {"initialize_timeout_s": 90}
    first = HarnessRuntime(args.out / "home-original", dispatch, lambda m, p: notifications.append({"method": m, "params": p}), config)
    try:
        describe = first.start()
        first.open_session(**spec)
        run = first.prompt(spec["session_id"], "Use the report ID, then search block ID, then read context. Finish after the evidence chain.", timeout_s=90)
        assert run["finish_reason"] == "completed", run
        assert len(state["tools"]) == 3
        assert run["final_response"] == "Financial evidence chain completed."
        types = [event["type"] for event in run["events"]]
        assert types.count("step/start") == 4 and types.count("tool/call") == 3 and types.count("tool/result") == 3
        usage = first.describe()["budget_usage"]
    finally:
        first.close()
    calls_before = len(state["calls"])
    server.shutdown()
    server.server_close()
    replay_spec = {**spec, "session_id": "native-smoke-replay", "replay_only": True}
    replay = HarnessRuntime(args.out / "home-replay", dispatch, config=config)
    try:
        replay.start()
        replay.open_session(**replay_spec)
        replay_run = replay.prompt(replay_spec["session_id"], "Use the report ID, then search block ID, then read context. Finish after the evidence chain.", timeout_s=90)
        assert replay_run["finish_reason"] == "completed", replay_run
        assert replay_run["final_response"] == run["final_response"]
        assert len(state["calls"]) == calls_before
    finally:
        replay.close()
    resumed = HarnessRuntime(args.out / "home-original", dispatch, config=config)
    try:
        resumed.start()
        receipt = resumed.resume(**spec)
        assert receipt["resumed"] is True
        assert resumed.cancel(spec["session_id"])["quiescent"]
        assert resumed.close_session(spec["session_id"])["closed"]
    finally:
        resumed.close()
    result = {"status": "passed_native_harness_profile", "describe": describe, "usage": usage,
              "model_requests": len(state["calls"]), "live_tools": 3, "cached_replay_tools": 3,
              "cache_entries": len(state["cache"]), "cross_process_resume": True,
              "replay_network_requests": 0, "step_events": types.count("step/start"), "final_response": run["final_response"]}
    (args.out / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "session-notifications.json").write_text(json.dumps(notifications, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
