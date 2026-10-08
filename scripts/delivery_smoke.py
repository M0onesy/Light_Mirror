from __future__ import annotations

"""Test a shipped Windows EXE from an isolated directory and user profile.

Only the launcher is exercised. This script does not import MingJing source,
read the user's saved configuration, call a real model, or alter the kernel.
The host still has Python for running this test driver; the EXE's child PATH
contains only Windows System32, so this is not a claim of a fresh Windows VM.
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse
from urllib.request import ProxyHandler, Request, build_opener


class SmokeFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def financial_rows(payload):
    """Compare frozen financial fields, separately from declared Web decoration."""
    rows = payload.get('results')
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows), 'invalid result rows')
    decoration = {'execution_status', 'display_status'} if payload.get('view_schema') == 'run-result-view.v1' else set()
    return [{key: value for key, value in row.items() if key not in decoration} for row in rows]


def verify_export(archive, displayed, run_path):
    """Independently verify financial equivalence AND the frozen byte registry.

    /results declares a presentation schema adding UI execution/display status.
    Export must retain the original signed JSON, never rewrite it to that view.
    """
    required = {'claims.json', 'facts.json', 'results.json', 'audit.jsonl', 'run_manifest.json'}
    require(required <= set(archive.namelist()), 'run export lacks required artifacts')
    require(len(archive.namelist()) == len(set(archive.namelist())), 'run export has duplicate members')
    original_bytes = (run_path / 'results.json').read_bytes()
    require(archive.read('results.json') == original_bytes, 'export changed frozen results JSON bytes')
    original = json.loads(original_bytes.decode('utf-8'))
    require(financial_rows(original) == financial_rows(displayed), 'exported financial result fields differ')
    manifest_bytes = (run_path / 'run_manifest.json').read_bytes()
    require(archive.read('run_manifest.json') == manifest_bytes, 'export changed frozen manifest bytes')
    manifest = json.loads(manifest_bytes.decode('utf-8'))
    outputs = manifest.get('outputs')
    require(isinstance(outputs, list) and outputs, 'frozen output registry missing')
    names = set()
    for row in outputs:
        require(isinstance(row, dict) and isinstance(row.get('path'), str), 'invalid frozen output entry')
        relative = PurePosixPath(row['path'])
        require(not relative.is_absolute() and relative.as_posix() == row['path']
                and not any(part in {'.', '..'} or ':' in part or '\\' in part for part in relative.parts), 'unsafe frozen output path')
        require(row['path'] not in names, 'duplicate frozen output entry')
        names.add(row['path'])
        require(row['path'] in archive.namelist(), 'export omitted frozen output: ' + row['path'])
        raw = archive.read(row['path'])
        require(len(raw) == row.get('bytes') and hashlib.sha256(raw).hexdigest() == row.get('sha256'),
                'export changed frozen output: ' + row['path'])
    require('results.json' in names, 'frozen registry omits results JSON')
    return {'frozen_outputs_verified': len(names), 'frozen_results_sha256': hashlib.sha256(original_bytes).hexdigest(),
            'financial_fields_equal': True, 'frozen_bytes_equal': True}


def redact(value, credential):
    if isinstance(value, str):
        return value.replace(credential, "<redacted>")
    if isinstance(value, dict):
        return {key: redact(item, credential) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, credential) for item in value]
    return value


class API:
    def __init__(self, base):
        parsed = urlparse(base)
        require(parsed.hostname == "127.0.0.1", "test service is not loopback")
        self.base = base.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))

    def raw(self, path, method="GET", data=None):
        request = Request(self.base + path, method=method)
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            request.add_header("Content-Type", "application/json; charset=utf-8")
        with self.opener.open(request, data=body, timeout=20) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()

    def json(self, path, method="GET", data=None):
        _, content_type, body = self.raw(path, method, data)
        require("application/json" in content_type, f"non-JSON API response: {path}")
        return json.loads(body.decode("utf-8"))


class EmptyContentAPI(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.request_count += 1
        body = json.dumps({"model": "smoke-empty-local", "choices": [{"finish_reason": "stop", "message": {"content": "", "reasoning_content": "mock reasoning only"}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def prepare_delivery(args, out):
    delivery = out / "clean-delivery"
    delivery.mkdir()
    if args.zip:
        archive_path = Path(args.zip).resolve()
        zip_hash = sha256(archive_path)
        if args.zip_sha256:
            require(zip_hash == args.zip_sha256.lower(), "delivery ZIP SHA-256 mismatch")
        with zipfile.ZipFile(archive_path) as archive:
            require(archive.testzip() is None, "delivery ZIP CRC verification failed")
            for info in archive.infolist():
                relative = PurePosixPath(info.filename)
                require(not relative.is_absolute() and ".." not in relative.parts and ":" not in info.filename, "unsafe ZIP path")
                require(not stat.S_ISLNK(info.external_attr >> 16), "ZIP contains a symbolic link")
                target = delivery.joinpath(*relative.parts).resolve()
                require(target.is_relative_to(delivery.resolve()), "ZIP path escapes clean directory")
            archive.extractall(delivery)
        manifests = list(delivery.rglob("MingJing-manifest.json"))
        require(len(manifests) == 1, "delivery manifest missing or ambiguous")
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        exe = manifests[0].parent / manifest["entrypoint"]
        require(exe.resolve().is_relative_to(delivery.resolve()), "manifest EXE escapes delivery")
        require(exe.is_file() and sha256(exe) == manifest.get("sha256"), "manifest EXE SHA-256 mismatch")
        return exe, {"mode": "zip", "zip_sha256": zip_hash, "exe_sha256": sha256(exe), "manifest_verified": True, "version": manifest.get("version")}
    original = Path(args.exe).resolve()
    require(original.is_file(), "EXE does not exist")
    exe = delivery / "MingJing.exe"
    shutil.copy2(original, exe)
    require(sha256(original) == sha256(exe), "copied EXE hash mismatch")
    return exe, {"mode": "exe_only", "exe_sha256": sha256(exe), "copy_verified": True, "archive_manifest_verified": "not_applicable"}


def clean_environment(out, home):
    environment = os.environ.copy()
    for key in list(environment):
        upper = key.upper()
        if upper.startswith("MINGJING_") or upper in {"OPENAI_API_KEY", "DEEPSEEK_API_KEY", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}:
            environment.pop(key, None)
    system32 = Path(environment.get("SystemRoot", "C:/Windows")) / "System32"
    environment["PATH"] = str(system32)
    environment["LOCALAPPDATA"] = str(out / "isolated-localappdata")
    environment["TEMP"] = environment["TMP"] = str(out / "isolated-temp")
    environment["MINGJING_NO_BROWSER"] = "1"
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    Path(environment["TEMP"]).mkdir()
    Path(environment["LOCALAPPDATA"]).mkdir()
    return environment


def launch(exe, home, environment, logfile, requested_port=0):
    stream = logfile.open("wb")
    process = subprocess.Popen([str(exe), "--home", str(home), "--host", "127.0.0.1", "--port", str(requested_port), "--no-browser"], cwd=exe.parent, env=environment, stdout=stream, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    record = home / "workspace" / "server.json"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stream.close()
            raise SmokeFailure(f"EXE exited during startup with code {process.returncode}")
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            client = API(data["url"])
            health = client.json("/api/health")
            if health.get("status") == "ok" and health.get("instance_id") == data.get("instance_id"):
                return process, stream, client, data
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.15)
    process.terminate()
    stream.close()
    raise SmokeFailure("EXE startup timed out")


def shutdown(process, stream, client, home):
    client.json("/api/shutdown", "POST", {})
    try:
        code = process.wait(timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise SmokeFailure("EXE did not close normally") from exc
    stream.close()
    require(code == 0, "EXE normal shutdown returned nonzero")
    require(not (home / "workspace" / "server.json").exists(), "server instance record was not cleaned")


def force_cleanup(process, stream, client):
    if process and process.poll() is None:
        try:
            if client:
                client.json("/api/shutdown", "POST", {})
                process.wait(timeout=5)
        except Exception:
            taskkill = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "taskkill.exe"
            subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
    if stream and not stream.closed:
        stream.close()


def run_task(client, request):
    task = client.json("/api/run", "POST", request)
    run_id = task.get("run_id") or task["id"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        state = client.json(f"/api/runs/{run_id}")
        if state.get("status") == "completed":
            state.setdefault("run_id", run_id)
            return state, client.json(f"/api/runs/{run_id}/results")
        require(state.get("status") not in {"failed", "cancelled"}, "EXE financial run failed or cancelled")
        time.sleep(0.1)
    raise SmokeFailure("EXE financial run timed out")


def execute_smoke(args):
    require(os.name == "nt", "delivery smoke requires Windows")
    out = Path(args.out).resolve()
    require(not out.exists() or not any(out.iterdir()), "smoke output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    home = out / "isolated-profile" / "明镜验收"
    credential = "smoke-local-only-" + uuid.uuid4().hex
    summary = {"status": "failed", "started_at": datetime.now(timezone.utc).isoformat(), "checks": [], "real_model_requests": 0, "user_profile_modified": False, "limitations": ["测试驱动所在宿主机有Python；EXE子进程PATH仅System32。本测试不能替代真实无Python的全新Windows虚拟机。", "本地空content API和不存在Harness验证降级，不代表真实Harness SDK集成。"]}
    process = stream = client = mock = None
    step = "prepare_delivery"

    def passed(name, **details):
        summary["checks"].append({"name": name, "status": "passed", **details})
        print(json.dumps({"step": name, "status": "passed"}, ensure_ascii=False), flush=True)

    try:
        exe, package = prepare_delivery(args, out)
        summary["package"] = package
        passed(step, **package)
        environment = clean_environment(out, home)
        path_tools = {name: shutil.which(name, path=environment["PATH"]) for name in ("python", "node", "pnpm")}
        require(not any(path_tools.values()), "external Python/Node/pnpm found in isolated PATH")
        summary["environment"] = {"path": environment["PATH"], "external_tools_found": path_tools, "profile": str(home), "clean_directory": str(exe.parent)}
        # Occupy a requested port to verify automatic conflict recovery.
        occupied = socket.socket()
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        blocked_port = occupied.getsockname()[1]
        step = "cold_start_pdf_and_port_conflict"
        try:
            process, stream, client, record = launch(exe, home, environment, out / "launcher-first.log", blocked_port)
        finally:
            occupied.close()
        require(record["port"] != blocked_port, "occupied port was not recovered")
        config_response = client.json("/api/config")
        require(config_response["capabilities"]["pdf"] is True, "frozen EXE PDF capability is false")
        require(config_response["config"]["llm"]["enabled"] is False, "fresh profile unexpectedly enabled a model")
        html = client.raw("/")[2].decode("utf-8")
        require("明镜" in html, "embedded Web page is not valid Chinese UTF-8")
        passed(step, port_conflict_recovered=True, pdf=True, health=client.json("/api/health"))

        step = "duplicate_launch"
        duplicate_log = (out / "launcher-duplicate.log").open("wb")
        duplicate = subprocess.Popen([str(exe), "--home", str(home), "--no-browser"], cwd=exe.parent, env=environment, stdout=duplicate_log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        require(duplicate.wait(timeout=45) == 0, "duplicate EXE launch did not return existing instance")
        duplicate_log.close()
        require(client.json("/api/health")["instance_id"] == record["instance_id"], "duplicate launch replaced the original service")
        passed(step, existing_instance_reused=True)

        step = "demo_utf8_and_kernel"
        demo = client.json("/api/demo", "POST", {})
        unicode_draft = Path(demo["draft"]).with_name("真实输入格式_utf8_研报.md")
        shutil.copy2(demo["draft"], unicode_draft)
        preview = client.json("/api/draft/preview", "POST", {"draft": str(unicode_draft)})
        require("营业收入" in preview["text"] and "归母净利润" in preview["text"], "UTF-8 draft preview corrupted")
        request = {key: demo[key] for key in ("draft", "corpus", "company", "period", "report_date", "mode")}
        request["draft"] = str(unicode_draft)
        task, results = run_task(client, request)
        require(results["summary"] == {"verified": 4, "violated": 5, "abstained": 1}, "one-click demo verdict counts differ from 4/5/1")
        run_path = Path(task["out"])
        require(sha256(run_path / "input" / unicode_draft.name) == sha256(unicode_draft), "immutable UTF-8 input copy hash mismatch")
        result_hash = canonical_hash(financial_rows(results))
        passed(step, run_id=task["run_id"], summary=results["summary"], result_hash=result_hash, claims=len(results["claims"]))

        step = "three_genuine_replays"
        replays = [client.json("/api/replay", "POST", {"run": str(run_path)}) for _ in range(3)]
        require(all(item["status"] == "passed" and item["recomputed"] and item["numeric_equal"] and item["integrity_ok"] and item["network_calls"] == 0 for item in replays), "genuine replay or integrity gate failed")
        require(len({item["result_digest"] for item in replays}) == 1, "replay result digests differ")
        (out / "three_replays.json").write_text(json.dumps(replays, ensure_ascii=False, indent=2), encoding="utf-8")
        passed(step, replay_count=3, claims_recomputed=[item["claims_recomputed"] for item in replays], result_digest=replays[0]["result_digest"])

        step = "page_png_evidence_and_export"
        evidence_id = next(evidence for item in results["results"] for evidence in item["evidence"] if ":p" in evidence)
        endpoint = "/api/evidence/" + quote(evidence_id, safe="") + "?run=" + task["run_id"]
        evidence = client.json(endpoint)
        _, mime, png = client.raw(endpoint + "&format=png")
        require("image/png" in mime and png.startswith(b"\x89PNG\r\n\x1a\n"), "page evidence PNG is invalid")
        (out / "evidence-page.png").write_bytes(png)
        raw_export = client.raw(f"/api/runs/{task['run_id']}/export")[2]
        with zipfile.ZipFile(io.BytesIO(raw_export)) as archive:
            require(archive.testzip() is None, "run export ZIP failed CRC")
            export_proof = verify_export(archive, results, run_path)
        (out / "exported-run.zip").write_bytes(raw_export)
        passed(step, evidence_id=evidence_id, page=evidence.get("page"), png_bytes=len(png), export_bytes=len(raw_export), **export_proof)

        step = "local_empty_api_failure_and_kernel_fallback"
        mock = ThreadingHTTPServer(("127.0.0.1", 0), EmptyContentAPI)
        mock.request_count = 0
        threading.Thread(target=mock.serve_forever, daemon=True).start()
        local_url = f"http://127.0.0.1:{mock.server_port}/v1"
        cfg = client.json("/api/config")["config"]
        client.json("/api/config", "PATCH", {"revision": cfg["revision"], "llm": {"enabled": True, "base_url": local_url, "model": "smoke-empty-local", "timeout_s": 5, "max_tokens": 256}, "secrets": {"llm_api_key": credential}})
        health_check = client.json("/api/config/test-llm", "POST", {})
        require(health_check.get("ok") is False, "empty content was falsely reported as a healthy model")
        agent_task, agent_results = run_task(client, {**request, "mode": "agent"})
        require(canonical_hash(financial_rows(agent_results)) == result_hash, "API failure changed deterministic results")
        agent_audit = [json.loads(line) for line in (Path(agent_task["out"]) / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        require(any(item.get("type") == "model_call" and item.get("status") == "fallback" for item in agent_audit), "API downgrade was not audited")
        require(mock.request_count >= 2, "mock API was not actually contacted for health and extraction")
        passed(step, mock_requests=mock.request_count, fallback_visible=True, result_hash=result_hash, run_id=agent_task["run_id"])

        step = "missing_harness_fallback"
        cfg = client.json("/api/config")["config"]
        absent_harness = home / "missing-runtime" / "dsh.exe"
        client.json("/api/config", "PATCH", {"revision": cfg["revision"], "llm": {"enabled": False}, "harness": {"enabled": True, "runtime_mode": "external", "executable": str(absent_harness)}})
        harness_health = client.json("/api/config/test-harness", "POST", {})
        require(harness_health.get("ok") is False and harness_health.get("runtime_available") is False, "missing Harness reported available")
        harness_task, harness_results = run_task(client, {**request, "mode": "harness"})
        require(canonical_hash(financial_rows(harness_results)) == result_hash, "missing Harness changed deterministic results")
        harness_events = client.json(f"/api/runs/{harness_task['run_id']}/events")["events"]
        require(any(item.get("event") == "fallback" and item.get("requested_mode") == "harness" for item in harness_events), "Harness downgrade was not recorded")
        passed(step, fallback_visible=True, result_hash=result_hash, run_id=harness_task["run_id"])

        step = "config_export_import_and_secret_redaction"
        exported_config = client.json("/api/config/export")
        require(exported_config.get("secrets") == "omitted", "config export does not omit secrets")
        require(credential not in json.dumps(exported_config, ensure_ascii=False), "plaintext synthetic credential in config export")
        cfg = exported_config["config"]
        imported = client.json("/api/config/import", "POST", {"config": cfg, "revision": cfg["revision"]})
        require(imported["workspace"]["draft_path"] == str(unicode_draft), "Unicode config path import changed")
        (out / "config-export.json").write_text(json.dumps(exported_config, ensure_ascii=False, indent=2), encoding="utf-8")
        # The Windows instance lock deliberately denies a concurrent read of
        # its locked byte; it is a one-byte runtime marker, not an artifact.
        unsafe_files = [str(path.relative_to(home)) for path in home.rglob("*") if path.is_file() and path.name != "instance.lock" and credential.encode("utf-8") in path.read_bytes()]
        require(not unsafe_files, "plaintext synthetic credential found in profile artifacts")
        expected_revision = client.json("/api/config")["revision"]
        expected_ids = {task["run_id"], agent_task["run_id"], harness_task["run_id"]}
        passed(step, revision=expected_revision, UTF8_path_preserved=True, plaintext_credential_files=0)

        step = "normal_shutdown_restart_and_history"
        shutdown(process, stream, client, home)
        process = stream = client = None
        process, stream, client, restarted = launch(exe, home, environment, out / "launcher-restarted.log")
        require(restarted["instance_id"] != record["instance_id"], "restart did not create a new instance")
        require(client.json("/api/config")["revision"] == expected_revision, "configuration revision did not persist")
        history = client.json("/api/runs")["runs"]
        require(expected_ids <= {item["run_id"] for item in history}, "completed run history did not persist")
        require(client.json(f"/api/runs/{task['run_id']}/results")["results"] == results["results"], "historical numeric results changed after restart")
        shutdown(process, stream, client, home)
        process = stream = client = None
        passed(step, revision=expected_revision, persisted_runs=len(expected_ids), normal_shutdown=True)
        summary["status"] = "passed"
    except Exception as exc:
        summary["checks"].append({"name": step, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        force_cleanup(process, stream, client)
        if mock:
            mock.shutdown()
            mock.server_close()
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        sanitized = redact(summary, credential)
        (out / "smoke.json").write_text(json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8")
    return sanitized


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Isolated Windows MingJing delivery smoke test")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--zip")
    inputs.add_argument("--exe")
    parser.add_argument("--zip-sha256")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    result = execute_smoke(args)
    print(json.dumps({"status": result["status"], "checks": len(result["checks"]), "out": str(Path(args.out).resolve())}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
