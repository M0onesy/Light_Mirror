"""Exercise the same launcher/instance boundary used by MingJing.exe."""

import json
import os
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mingjing import launcher
from mingjing.config import ConfigManager, SecretStore


def test_instance_lock_is_exclusive_and_recoverable(tmp_path):
    first = launcher.InstanceLock(tmp_path / "instance.lock")
    second = launcher.InstanceLock(tmp_path / "instance.lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_backend_import_failure_is_visible_without_fake_health(tmp_path, monkeypatch):
    monkeypatch.setenv("MINGJING_NO_BROWSER", "1")
    monkeypatch.setattr(launcher, "_server_factory", lambda: None)
    assert launcher.run(home=str(tmp_path), open_browser=False) == 1
    assert "完整 Web 后端不可导入" in (tmp_path / "logs" / "launcher.log").read_text(encoding="utf-8")
    assert not (tmp_path / "workspace" / "server.json").exists()
    recovered = launcher.InstanceLock(tmp_path / "instance.lock")
    assert recovered.acquire()
    recovered.release()


def test_launcher_resolves_one_application_namespace_without_historical_fallback(monkeypatch):
    imported = []
    factory = lambda **kwargs: None
    def import_backend(name):
        imported.append(name)
        assert name == "mingjing.server"
        return SimpleNamespace(serve=factory)
    monkeypatch.delenv("MINGJING_SERVER_MODULE", raising=False)
    monkeypatch.setattr(launcher, "__package__", "mingjing.mingjing")
    monkeypatch.setattr(launcher.importlib, "import_module", import_backend)
    assert launcher._server_factory() is factory
    assert imported == ["mingjing.server"]


def test_launcher_rejects_external_backend_module_override(monkeypatch):
    monkeypatch.setenv('MINGJING_SERVER_MODULE', 'mingjing.mingjing.server')
    monkeypatch.setattr(launcher.importlib, 'import_module', lambda _: pytest.fail('legacy backend was imported'))
    with pytest.raises(ValueError, match='only loads'):
        launcher._server_factory()


def test_real_launcher_port_conflict_duplicate_instance_and_ui_shutdown(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    monkeypatch.setenv("MINGJING_HOME", str(home))
    monkeypatch.setenv("MINGJING_NO_BROWSER", "1")
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        busy_port = occupied.getsockname()[1]
        config = ConfigManager(secret_store=SecretStore(home))
        config.update_from_dict({"server": {"host": "localhost", "port": busy_port, "auto_open_browser": False}}, expected_revision=0)
        command = [sys.executable, "-m", "mingjing.launcher", "--home", str(home), "--no-browser"]
        child = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
                                 env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace")
        try:
            deadline = time.monotonic() + 8
            record_path = home / "workspace" / "server.json"
            while not record_path.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert record_path.exists(), child.communicate(timeout=1)[0]
            record = json.loads(record_path.read_text(encoding="utf-8"))
            assert record["host"] == "localhost"
            assert record["port"] != busy_port
            from mingjing import __version__
            assert record["version"] == __version__
            with urllib.request.urlopen(record["url"] + "api/health", timeout=2) as response:
                health = json.loads(response.read())
            assert health["service"] == "mingjing"
            assert health["instance_id"] == record["instance_id"]
            duplicate = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(),
                                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
            assert duplicate.returncode == 0
            assert "已在运行" in duplicate.stdout
            assert json.loads(record_path.read_text(encoding="utf-8"))["pid"] == record["pid"]
            req = urllib.request.Request(record["url"] + "api/shutdown", method="POST", data=b"{}",
                                         headers={"Content-Type": "application/json", "Origin": "https://untrusted.example"})
            with pytest.raises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(req, timeout=2)
            assert rejected.value.code == 401
            req = urllib.request.Request(record["url"] + "api/shutdown", method="POST", data=b"{}",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as response:
                assert response.status == 202
            assert child.wait(timeout=4) == 0
            assert not record_path.exists()
            assert ConfigManager(secret_store=SecretStore(home)).public_dict()["server"]["port"] == busy_port
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=4)

