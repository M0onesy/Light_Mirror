"""Single-process launcher used by the MingJing Windows delivery.

The launcher deliberately knows very little about the application.  It owns
the process lifetime, local workspace directories and browser opening, while
the HTTP service remains in ``mingjing.server`` (or ``mingjing.web_server``).
Keeping this boundary small makes the same code usable from a source checkout
and from a PyInstaller one-file executable.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import socket
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class RuntimePaths:
    """Directories created for one local MingJing installation."""

    home: Path
    workspace: Path
    runs: Path
    logs: Path
    temp: Path

    @classmethod
    def create(cls, home: str | os.PathLike[str] | None = None) -> "RuntimePaths":
        if home is None:
            home = os.environ.get("MINGJING_HOME")
        if home is None:
            local = os.environ.get("LOCALAPPDATA")
            home = Path(local) / "MingJing" if local else Path.home() / ".mingjing"
        root = Path(home).expanduser().resolve()
        paths = cls(root, root / "workspace", root / "workspace" / "runs", root / "logs", root / "tmp")
        for path in (paths.home, paths.workspace, paths.runs, paths.logs, paths.temp):
            path.mkdir(parents=True, exist_ok=True)
        return paths


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _server_factory() -> Callable[..., Any] | None:
    """Find the service implementation without importing optional packages."""

    configured = os.environ.get("MINGJING_SERVER_MODULE", "")
    if configured and configured != 'mingjing.server':
        raise ValueError('MingJing only loads the current mingjing.server application')
    names = ['mingjing.server']
    attrs = ("create_server", "build_server", "start_server", "serve", "run_server", "serve_app")
    for name in dict.fromkeys(names):
        if not name:
            continue
        try:
            module = importlib.import_module(name)
        except (ImportError, ModuleNotFoundError):
            continue
        for attr in attrs:
            candidate = getattr(module, attr, None)
            if callable(candidate):
                return candidate
    return None


def _call_factory(factory: Callable[..., Any], host: str, port: int, paths: RuntimePaths) -> Any:
    """Pass only parameters accepted by a service factory.

    This permits the backend to expose a small ``create_server(host, port)``
    function while richer implementations can also consume the runtime paths
    and browser setting.
    """

    values = {
        "host": host,
        "bind": host,
        "port": port,
        "paths": paths,
        "runtime_paths": paths,
        "home": paths.home,
        "data_root": paths.workspace,
        "open_browser": False,
    }
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory(host, port)
    kwargs = {
        name: values[name]
        for name, parameter in signature.parameters.items()
        if name in values and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    }
    return factory(**kwargs)


class InstanceLock:
    """An OS-owned lock automatically released on normal or abnormal exit."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _startup_failure(paths: RuntimePaths, message: str) -> int:
    # Do not start a placeholder server that might be mistaken for a healthy
    # financial agent. A failed backend is a failed application startup.
    (paths.logs / "launcher.log").write_text(message + "\n", encoding="utf-8")
    print("MingJing 启动失败：" + message, file=sys.stderr, flush=True)
    if os.name == "nt" and getattr(sys, "frozen", False) and os.environ.get("MINGJING_NO_BROWSER", "").lower() not in {"1", "true", "yes"}:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "明镜启动失败。\n" + message + "\n日志：" + str(paths.logs / "launcher.log"), "明镜 MingJing", 0x10)
    return 1


def _normalise_server(result: Any, host: str, port: int) -> tuple[Any, int, bool]:
    """Return ``(server, actual_port, should_serve)`` for backend results."""

    if isinstance(result, tuple) and result:
        result = result[0]
    if result is None:
        return None, port, False
    if isinstance(result, int):
        return None, result, False
    actual = getattr(result, "server_address", (host, port))[1]
    return result, int(actual), hasattr(result, "serve_forever")


def run(*, host: str | None = None, port: int | None = None, home: str | None = None, open_browser: bool | None = None) -> int:
    """Start the local service and block until it exits.

    The backend binds the requested port (or zero) itself, avoiding a race
    between probing a port and binding it. Saved service settings are defaults.
    """

    paths = RuntimePaths.create(home)
    # The config layer reads MINGJING_HOME during construction.  Set it before
    # importing the backend so source and frozen launches use the same profile.
    os.environ["MINGJING_HOME"] = str(paths.home)
    record_path = paths.workspace / "server.json"
    lock = InstanceLock(paths.home / "instance.lock")
    if not lock.acquire():
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if record.get("url"):
                    if open_browser is not False and os.environ.get("MINGJING_NO_BROWSER", "").lower() not in {"1", "true", "yes"}:
                        webbrowser.open(str(record["url"]))
                    print("MingJing 已在运行：" + str(record["url"]), flush=True)
                    return 0
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        return _startup_failure(paths, "已有实例持有锁，但未生成服务地址；请稍后再次启动。")
    instance_id = uuid.uuid4().hex
    server: Any = None
    try:
        record_path.unlink(missing_ok=True)
        from .config import load_config, SecretStore, validate_server_binding
        configured = load_config().server
        selected_host = host if host is not None else configured.host
        selected_port = port if port is not None else configured.port
        selected_browser = configured.auto_open_browser if open_browser is None else bool(open_browser)
        validate_server_binding(selected_host, configured, SecretStore(paths.home).get, port=selected_port)
        factory = _server_factory()
        if factory is None:
            return _startup_failure(paths, "完整 Web 后端不可导入，请重新安装当前交付程序。")
        server, actual_port, should_serve = _normalise_server(_call_factory(factory, selected_host, selected_port, paths), selected_host, selected_port)
        if server is None or not should_serve:
            return _startup_failure(paths, "完整 Web 后端未返回可运行服务。")
        server.instance_id = instance_id
        display_host = f"[{selected_host}]" if ":" in selected_host else selected_host
        url = f"http://{display_host}:{actual_port}/"
        from .config import _atomic_write_json
        _atomic_write_json(record_path, {"pid": os.getpid(), "url": url, "host": selected_host, "port": actual_port,
                                          "instance_id": instance_id, "version": __import__(__package__, fromlist=['__version__']).__version__})
        if selected_browser and os.environ.get("MINGJING_NO_BROWSER", "").lower() not in {"1", "true", "yes"}:
            webbrowser.open(url)
        print(f"MingJing {__import__(__package__, fromlist=['__version__']).__version__} is running at {url}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    except Exception as exc:
        return _startup_failure(paths, f"{type(exc).__name__}: {exc}")
    finally:
        if server is not None:
            state = getattr(server, "state", None)
            if state is not None:
                tasks = list(state.tasks.values())
                for task in tasks:
                    task.cancelled.set()
                deadline = time.monotonic() + 3
                for task in tasks:
                    if task.thread.is_alive():
                        task.thread.join(max(0, deadline - time.monotonic()))
            server.server_close()
        try:
            if json.loads(record_path.read_text(encoding="utf-8")).get("instance_id") == instance_id:
                record_path.unlink()
        except (OSError, ValueError):
            pass
        lock.release()


def main(argv: list[str] | None = None) -> int:
    from .import_integrity import assert_application_origin
    assert_application_origin(Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])))
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Start the MingJing local Web application")
    parser.add_argument("--host", default=os.environ.get("MINGJING_HOST"))
    parser.add_argument("--port", type=int, default=int(os.environ["MINGJING_PORT"]) if os.environ.get("MINGJING_PORT") else None)
    parser.add_argument("--home", default=os.environ.get("MINGJING_HOME"))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    return run(host=args.host, port=args.port, home=args.home, open_browser=False if args.no_browser else None)


if __name__ == "__main__":
    raise SystemExit(main())
