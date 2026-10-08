"""History survives output directory changes without rewriting frozen results."""
import hashlib
import json
import threading
import urllib.error
import urllib.request

import pytest

from mingjing import server


def _fixture_finish(task):
    task.out.mkdir(parents=True, exist_ok=True)
    (task.out / "results.json").write_text(json.dumps({"run_id": task.id, "partial": False, "summary": {}}), encoding="utf-8")
    task.status = "completed"
    task.emit("completed")
    task.registry_callback()


def test_cross_output_roots_survive_restart_and_restore_events(tmp_path, monkeypatch):
    monkeypatch.setenv("MINGJING_HOME", str(tmp_path / "profile"))
    monkeypatch.setattr(server.RunTask, "start", _fixture_finish)
    state = server.AppState()
    first = state.launch({"run_id": "first-run", "mode": "kernel"})
    first_hash = hashlib.sha256((first.out / "results.json").read_bytes()).hexdigest()
    alternate = tmp_path / "second-output"
    state.config.update({"workspace": {"output_root": str(alternate)}})
    second = state.launch({"run_id": "second-run", "mode": "kernel"})
    assert second.out.parent == alternate
    restarted = server.AppState()
    assert {task.id for task in restarted.list_tasks()} == {"first-run", "second-run"}
    assert restarted.get_task("first-run").out == first.out
    assert restarted.get_task("first-run").events[-1]["event"] == "completed"
    assert hashlib.sha256((first.out / "results.json").read_bytes()).hexdigest() == first_hash
    with pytest.raises(ValueError, match="run_id"):
        restarted.launch({"run_id": "first-run"})
    registry = json.loads(restarted.registry_path.read_text(encoding="utf-8"))
    assert len(registry["roots"]) == 2
    assert set(registry["runs"]) == {"first-run", "second-run"}


def test_failed_and_interrupted_history_is_persistent_without_results(tmp_path, monkeypatch):
    monkeypatch.setenv("MINGJING_HOME", str(tmp_path / "profile"))
    monkeypatch.setattr(server.RunTask, "start", lambda task: None)
    state = server.AppState()
    failed = state.launch({"run_id": "failed-run"})
    failed.status = "failed"
    failed.registry_callback()
    state.launch({"run_id": "interrupted-run"})
    restored = server.AppState()
    assert restored.get_task("failed-run").status == "failed"
    assert restored.get_task("interrupted-run").status == "interrupted"


def test_material_http_rejects_candidate_numbers_and_passes_validated_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("MINGJING_HOME", str(tmp_path / "profile"))
    state = server.AppState()
    captured = []
    monkeypatch.setattr(state.sessions, "add_materials", lambda sid, payload: captured.append((sid, payload)) or {"run_id": "new-version"}, raising=False)
    monkeypatch.setattr(state.sessions, "list", lambda: [], raising=False)
    service = server.MingJingServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    draft = tmp_path / "新材料.md"
    draft.write_text("营业收入新增来源。", encoding="utf-8")
    sid = "session-" + "a" * 24
    base = f"http://127.0.0.1:{service.server_port}"
    try:
        request = urllib.request.Request(base + "/api/agent/sessions/" + sid + "/materials", method="POST",
                    data=json.dumps({"draft": str(draft)}, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            assert response.status == 202
        assert captured == [(sid, {"draft": str(draft.resolve())})]
        request.data = json.dumps({"draft": str(draft), "candidate_value": 999999}).encode()
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(request)
        assert denied.value.code == 400
        assert len(captured) == 1
        with urllib.request.urlopen(base + "/api/agent/sessions") as response:
            assert json.loads(response.read())["sessions"] == []
    finally:
        service.shutdown()
        service.server_close()
        thread.join(2)
