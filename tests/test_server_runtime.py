"""Runtime regressions for the actual Web/frozen task orchestration."""

import copy
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import cli, server
from mingjing.config import ConfigManager, SecretStore, app_root
from mingjing.evidence import make_evidence_id, sha256_file


class TestConfig:
    __test__ = False

    def __init__(self, name="first", enabled=True):
        self.value = {
            "security": {"allow_external_paths": True, "follow_symlinks": False},
            "run": {"timeout_s": 10},
            "llm": {"enabled": enabled, "base_url": "https://example.invalid/v1", "model": name,
                    "chat_path": "/custom/chat", "timeout_s": 7, "max_tokens": 500,
                    "temperature": 0.2, "thinking": "disabled", "api_key_ref": name},
        }

    def get(self):
        return copy.deepcopy(self.value)

    def secret(self, ref):
        return "private-token-" + ref


def task(tmp_path, name, config=None):
    draft = tmp_path / "draft.md"
    draft.write_text("并行科技2023年营业收入4.96亿元。", encoding="utf-8")
    corpus = tmp_path / "facts.json"
    corpus.write_text("[]", encoding="utf-8")
    return server.RunTask(name, {"draft": str(draft), "corpus": str(corpus), "company": "并行科技",
                                "period": "2023Y", "mode": "agent"}, tmp_path / "runs", config or TestConfig(name))


def test_frozen_runs_do_not_share_model_secrets_and_restore_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("MINGJING_LLM_API_KEY", "original-token")
    first_entered = threading.Event()
    release_first = threading.Event()
    seen = []

    def fake_check(args):
        before = {key: os.environ.get(key) for key in server._LLM_ENV_KEYS}
        assert args.company == "并行科技"
        assert args.cancel_event is not None
        args.progress_callback("model")
        if args.run_id == "first":
            first_entered.set()
            assert release_first.wait(3)
        seen.append((args.run_id, before, {key: os.environ.get(key) for key in server._LLM_ENV_KEYS}))
        return 0

    monkeypatch.setattr(cli, "command_check", fake_check)
    first, second = task(tmp_path, "first"), task(tmp_path, "second")
    first.start()
    assert first_entered.wait(3)
    second.start()
    # Wait for the second task to reach the cancellable runtime queue.
    deadline = time.monotonic() + 3
    while not any(e.get("name") == "waiting_for_runtime" for e in second.events) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert second.thread.is_alive()
    assert not seen
    release_first.set()
    first.thread.join(3)
    second.thread.join(3)
    assert first.status == second.status == "completed"
    assert [row[0] for row in seen] == ["first", "second"]
    for name, before, after in seen:
        assert before == after
        assert before["MINGJING_LLM_MODEL"] == name
        assert before["MINGJING_LLM_API_KEY"] == "private-token-" + name
        assert before["MINGJING_LLM_CHAT_PATH"] == "/custom/chat"
        assert before["MINGJING_LLM_THINKING"] == "disabled"
    assert os.environ["MINGJING_LLM_API_KEY"] == "original-token"


def test_frozen_failure_redacts_secret_and_restores_disabled_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("MINGJING_LLM_MODEL", "inherited-model")
    monkeypatch.setenv("MINGJING_LLM_API_KEY", "inherited-token")
    seen = []

    def fake_check(args):
        seen.append(os.environ.get("MINGJING_LLM_API_KEY"))
        if args.run_id == "failing":
            raise RuntimeError("provider echoed private-token-failing")
        assert not os.environ.get("MINGJING_LLM_MODEL")
        return 0

    monkeypatch.setattr(cli, "command_check", fake_check)
    failing = task(tmp_path, "failing")
    failing._run()
    disabled = task(tmp_path, "disabled", TestConfig("disabled", enabled=False))
    disabled._run()
    assert failing.status == "failed"
    assert "private-token-failing" not in json.dumps(failing.events)
    assert "private-token-failing" not in failing.error
    assert seen == ["private-token-failing", None]
    assert os.environ["MINGJING_LLM_API_KEY"] == "inherited-token"
    assert os.environ["MINGJING_LLM_MODEL"] == "inherited-model"


def test_cancel_waiting_frozen_task_never_enters_kernel(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def fake_check(args):
        calls.append(args.run_id)
        entered.set()
        assert release.wait(3)
        return 0

    monkeypatch.setattr(cli, "command_check", fake_check)
    active, queued = task(tmp_path, "active"), task(tmp_path, "queued")
    active.start()
    assert entered.wait(3)
    queued.start()
    queued.cancelled.set()
    queued.thread.join(2)
    release.set()
    active.thread.join(3)
    assert queued.status == "cancelled"
    assert active.status == "completed"
    assert calls == ["active"]


def test_source_cancel_interrupts_silent_child_process(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    original_popen = subprocess.Popen
    children = []

    def quiet_child(args, **kwargs):
        child = original_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(server.subprocess, "Popen", quiet_child)
    running = task(tmp_path, "cancel-test")
    running.start()
    deadline = time.monotonic() + 3
    while not children and time.monotonic() < deadline:
        time.sleep(0.01)
    assert children
    running.cancelled.set()
    running.thread.join(4)
    assert not running.thread.is_alive()
    assert running.status == "cancelled"
    assert children[0].poll() is not None


@pytest.mark.parametrize("response", [
    {}, {"choices": []}, {"choices": [None]}, {"choices": [{"message": []}]},
    {"choices": [{"message": {"reasoning_content": "thinking", "content": ""}}]},
    {"choices": [{"message": {"content": "OK"}, "finish_reason": "length"}]},
])
def test_connection_rejects_http_200_without_complete_content(monkeypatch, response):
    from mingjing import http_transport
    handler = server.Handler.__new__(server.Handler)
    handler.server = SimpleNamespace(state=SimpleNamespace(config=TestConfig()))
    monkeypatch.setattr(http_transport, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(response).encode()))
    assert handler._test_llm({})["ok"] is False


def test_connection_uses_ui_path_parameters_and_v4_default(monkeypatch):
    from mingjing import http_transport
    handler = server.Handler.__new__(server.Handler)
    handler.server = SimpleNamespace(state=SimpleNamespace(config=TestConfig()))
    captured = []

    def reply(request, timeout):
        captured.append((request.full_url, json.loads(request.data), timeout))
        return io.BytesIO(json.dumps({"model": "response-model", "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}).encode())

    monkeypatch.setattr(http_transport, "urlopen", reply)
    answer = handler._test_llm({"model": "deepseek-v4-pro", "thinking": "auto", "chat_path": "/v2/chat", "timeout_s": 9, "max_tokens": 123, "temperature": 0.1})
    assert answer["ok"]
    assert answer["model"] == "response-model"
    assert captured[0][0].endswith("/v2/chat")
    assert captured[0][1]["thinking"] == {"type": "disabled"}
    assert captured[0][1]["max_tokens"] == 123
    assert captured[0][2] == 9


def test_home_isolation_and_thinking_round_trip(tmp_path, monkeypatch):
    isolated = tmp_path / "isolated"
    monkeypatch.setenv("MINGJING_HOME", str(isolated))
    assert app_root() == isolated
    manager = ConfigManager(secret_store=SecretStore(isolated))
    manager.update_from_dict({"llm": {"thinking": "enabled"}}, expected_revision=0)
    assert manager.path == isolated / "config.json"
    assert ConfigManager(secret_store=SecretStore(isolated)).public_dict()["llm"]["thinking"] == "enabled"


def test_evidence_page_png_is_bound_to_original_document(tmp_path):
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "annual.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 50), "Revenue 495671818.88")
        doc.save(source)
    evidence_id = make_evidence_id(sha256_file(source), 1, "Revenue", [45, 35, 250, 60])
    info = server._evidence_info({"source_path": str(source)}, evidence_id)
    assert server._evidence_png(info, {"security": {"allow_external_paths": True}}).startswith(b"\x89PNG")
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        server._evidence_png(info, {"security": {"allow_external_paths": True}})


def test_run_id_cannot_escape_output_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MINGJING_HOME", str(tmp_path))
    state = server.AppState()
    with pytest.raises(ValueError, match="run_id"):
        state.launch({"run_id": "../../escape"})
    assert state.get_task("../outside") is None
    with pytest.raises(ValueError, match="candidate facts"):
        state.launch({"run_id": "bad-fact", "candidate_value": 999999})


def test_real_http_config_utf8_revision_and_secret_export(tmp_path, monkeypatch):
    monkeypatch.setenv("MINGJING_HOME", str(tmp_path / "profile"))
    state = server.AppState()
    httpd = server.MingJingServer(("127.0.0.1", 0), state)
    serving = threading.Thread(target=httpd.serve_forever, daemon=True)
    serving.start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    def request(path, body=None, method=None):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        with urllib.request.urlopen(urllib.request.Request(base + path, data=raw, method=method,
                                                         headers={"Content-Type": "application/json"})) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        assert request("/api/health")["status"] == "ok"
        result = request("/api/config", {"revision": 0, "run": {"company": "并行科技"},
                                          "llm": {"thinking": "disabled"},
                                          "secrets": {"llm_api_key": "test-only-private-token"}}, "PATCH")
        assert result["run"]["company"] == "并行科技"
        assert result["revision"] == 1
        exported = request("/api/config/export")
        assert exported["secrets"] == "omitted"
        assert "test-only-private-token" not in json.dumps(exported)
        with pytest.raises(urllib.error.HTTPError) as failure:
            request("/api/config", {"revision": 0, "run": {"company": "stale"}}, "PATCH")
        assert failure.value.code == 409
        assert request("/api/config")["config"]["run"]["company"] == "并行科技"
    finally:
        httpd.shutdown()
        httpd.server_close()
        serving.join(2)


def test_real_http_encoded_evidence_id_opens_highlighted_pdf_page(tmp_path, monkeypatch):
    from urllib.parse import quote
    fitz = pytest.importorskip("fitz")
    monkeypatch.setenv("MINGJING_HOME", str(tmp_path / "profile"))
    source = tmp_path / "annual.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 50), "Revenue 495671818.88")
        doc.save(source)
    evidence_id = make_evidence_id(sha256_file(source), 1, "Revenue", [45, 35, 250, 60])
    state = server.AppState()
    out = state.runs_root / "saved-run"
    out.mkdir()
    (out / "results.json").write_text(json.dumps({"run_id": "saved-run", "facts": [{"source_path": str(source), "evidence": [evidence_id]}]}), encoding="utf-8")
    httpd = server.MingJingServer(("127.0.0.1", 0), state)
    serving = threading.Thread(target=httpd.serve_forever, daemon=True)
    serving.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_port}/api/evidence/{quote(evidence_id, safe='')}?run=saved-run&format=png"
        with urllib.request.urlopen(url) as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.read().startswith(b"\x89PNG")
    finally:
        httpd.shutdown()
        httpd.server_close()
        serving.join(2)


def test_harness_request_records_visible_fallback(tmp_path, monkeypatch):
    from mingjing.harness_client import HarnessRuntime, HarnessError
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    def missing(self):
        raise HarnessError("Injected missing native runtime")
    monkeypatch.setattr(HarnessRuntime, "start", missing)
    config = TestConfig("fallback", enabled=False)
    running = task(tmp_path, "harness-fallback", config)
    running.request["mode"] = "harness"
    running._run()
    assert running.status == "completed"
    event = next(event for event in running.events if event["event"] == "fallback")
    assert "HarnessError" in event["reason"]
    assert event["selected_mode"] == "kernel"
    data = json.loads((running.out / "results.json").read_text(encoding="utf-8"))
    assert data["model"]["status"] == "fallback"
    assert sum(data["summary"].values()) > 0


def test_harness_health_requires_native_start_and_separates_model_test(tmp_path, monkeypatch):
    from mingjing.harness_client import HarnessRuntime
    config = TestConfig()
    handler = server.Handler.__new__(server.Handler)
    handler.server = SimpleNamespace(state=SimpleNamespace(config=config))
    monkeypatch.setattr(HarnessRuntime, "start", lambda self: {"native_loop": True, "shell": False, "code_mode": False})
    monkeypatch.setattr(HarnessRuntime, "close", lambda self: None)
    response = handler._test_harness({})
    assert response["runtime_available"] is True
    assert response["semantic_integration"] is True
    assert response["agent_capability"] == "not_tested"
    assert response["description"]["shell"] is False
