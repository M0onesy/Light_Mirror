"""End-to-end local Web product acceptance without optional model calls."""

import io
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest

from mingjing import server


def test_demo_run_preview_evidence_replay_export_and_saved_preferences(tmp_path, monkeypatch):
    pytest.importorskip("fitz")
    monkeypatch.setenv("MINGJING_HOME", str(tmp_path / "profile"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    state = server.AppState()
    httpd = server.MingJingServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    def request(path, value=None, method=None):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8") if value is not None else None
        req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        sample = request("/api/demo", {}, "POST")
        assert sample["mode"] == "kernel"
        assert Path(sample["draft"]).is_file()
        assert Path(sample["corpus"]).is_dir()
        assert (Path(sample["corpus"]) / "demo_annual.pdf").is_file()
        assert str(tmp_path) in sample["draft"]
        preview = request("/api/draft/preview", {"draft": sample["draft"]}, "POST")
        assert sample["company"] in preview["text"]
        assert len(preview["sha256"]) == 64
        task = request("/api/run", sample, "POST")
        run_id = task["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            summary = request("/api/runs/" + run_id)
            if summary["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert summary["status"] == "completed", summary
        results = request("/api/runs/" + run_id + "/results")
        assert all(results["summary"][key] > 0 for key in ("verified", "violated", "abstained"))
        source_path = next(fact["source_path"] for fact in results["facts"] if fact.get("source_path"))
        assert str(tmp_path) in source_path
        evidence_id = next(e for result in results["results"] for e in result.get("evidence", []))
        with urllib.request.urlopen(base + "/api/evidence/" + quote(evidence_id, safe="") + "?run=" + run_id + "&format=png", timeout=5) as response:
            assert response.read().startswith(b"\x89PNG")
        replay = request("/api/replay", {"run": summary["out"]}, "POST")
        assert replay["status"] == "passed"
        with urllib.request.urlopen(base + "/api/runs/" + run_id + "/export", timeout=5) as response:
            assert response.headers["Content-Type"] == "application/zip"
            exported = response.read()
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            names = archive.namelist()
            assert "results.json" in names
            assert "report.html" in names
            assert "replay.json" in names
            assert not any("secrets" in name or "config.json" in name for name in names)
        config = request("/api/config")["config"]
        assert config["workspace"]["draft_path"] == sample["draft"]
        assert config["run"]["company"] == sample["company"]
        assert config["run"]["period"] == sample["period"]
        events = request("/api/runs/" + run_id + "/events")["events"]
        assert any(event.get("percent") == 100 for event in events)
        for asset in ("/product.css", "/product.js"):
            with urllib.request.urlopen(base + asset, timeout=5) as response:
                assert response.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)


def test_export_white_list_and_secret_guard(tmp_path):
    config = server.LocalConfig.__new__(server.LocalConfig)
    config.get = lambda: {"llm": {"api_key_ref": "key"}}
    config.secret = lambda ref: "sensitive-api-key" if ref == "key" else ""
    task = server.RunTask("demo", {}, tmp_path, config)
    task.out.mkdir()
    (task.out / "results.json").write_text('{"status":"ok"}', encoding="utf-8")
    (task.out / "secrets.json").write_text("not-exportable", encoding="utf-8")
    (task.out / "config.json").write_text("not-exportable", encoding="utf-8")
    (task.out / "private.txt").write_text("not-exportable", encoding="utf-8")
    with zipfile.ZipFile(io.BytesIO(server._export_run(task, config))) as archive:
        assert archive.namelist() == ["results.json"]
    (task.out / "audit.jsonl").write_text("sensitive-api-key", encoding="utf-8")
    with pytest.raises(ValueError, match="未脱敏密钥"):
        server._export_run(task, config)
