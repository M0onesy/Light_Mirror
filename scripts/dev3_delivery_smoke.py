"""Scoped dev3 delivery checks on the current upload-first workbench.

The driver uses Python and Playwright on this developer host. An EXE receives
only System32 on PATH. Private DPAPI/model-cache files live outside --out and
are removed after a normal run; --keep-open leaves that private profile for
recording. No full-report, independent VM, human-gold, or D3 claim is made.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.delivery_smoke import clean_environment, force_cleanup, launch, require, sha256, shutdown


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def redact(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {key: redact(child, secrets) for key, child in value.items()}
    if isinstance(value, list):
        return [redact(child, secrets) for child in value]
    return value


class API:
    def __init__(self, base):
        parsed = urlparse(base)
        require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1", "service_must_be_loopback")
        self.base = base.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))

    def raw(self, path, method="GET", data=None, *, body=None, timeout=30, allow_error=False):
        headers = {}
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        elif body is not None:
            headers["Content-Type"] = "application/octet-stream"
        request = Request(self.base + path, data=body, method=method, headers=headers)
        try:
            response = self.opener.open(request, timeout=timeout)
        except HTTPError as exc:
            if not allow_error:
                raise
            response = exc
        with response:
            return response.status, response.headers.get("Content-Type", ""), response.read()

    def json(self, path, method="GET", data=None, *, timeout=30):
        _, mime, body = self.raw(path, method, data, timeout=timeout)
        require("application/json" in mime, "expected_json_response")
        return json.loads(body.decode("utf-8"))

    def reject(self, path, method="POST", data=None):
        status, mime, raw = self.raw(path, method, data, allow_error=True)
        require(status in {400, 403, 404} and "application/json" in mime,
                "invalid_input_not_rejected:" + path)
        require(json.loads(raw).get("error"), "rejection_has_no_visible_reason")
        return {"http_status": status, "visible_error": True}


def private_scan(paths, secrets, excluded_dpapi):
    keys = [secret.encode("utf-8") for secret in secrets if secret]
    overlap = max((len(key) for key in keys), default=1) - 1
    count = 0
    for path in paths:
        if not path.is_file() or path in excluded_dpapi or path.name == "instance.lock" or path.suffix == ".lock":
            continue
        count += 1
        tail = b""
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                data = tail + block
                require(not any(key in data for key in keys), "plaintext_credential_in_artifact")
                tail = data[-overlap:] if overlap else b""
    return count


def start(args, executable, home, environment, logfile, port=0):
    if not args.source:
        process, stream, _, record = launch(executable, home, environment, logfile, port)
        return process, stream, API(record["url"]), record
    stream = logfile.open("wb")
    command = [sys.executable, "-m", "mingjing.launcher", "--home", str(home),
               "--host", "127.0.0.1", "--port", str(port), "--no-browser"]
    process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=stream,
                               stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stream.close()
            raise RuntimeError("source_launcher_exited:" + str(process.returncode))
        try:
            record = json.loads((home / "workspace/server.json").read_text("utf-8"))
            api = API(record["url"])
            if api.json("/api/health").get("instance_id") == record["instance_id"]:
                return process, stream, api, record
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.15)
    force_cleanup(process, stream, None)
    raise TimeoutError("source_startup_timeout")


def wait_batch(api, run_id, timeout):
    deadline, heartbeat = time.monotonic() + timeout, 0
    while time.monotonic() < deadline:
        state = api.json("/api/smart/runs/" + run_id)
        if state["status"] in {"completed", "partial"}:
            require(state.get("batch", {}).get("items"), "batch_has_no_result_items")
            require(not state["batch"].get("failures"), "batch_has_failed_items")
            child = state["batch"]["items"][0]
            base = "/api/smart/runs/" + run_id + "/items/" + quote(child["item_id"], safe="")
            return state, child, base, api.json(base + "/results.json")
        require(state["status"] not in {"failed", "cancelled", "interrupted"},
                "smart_batch_failed:" + str(state.get("error", "")) + ":" + str(state.get("trace", "")))
        if time.monotonic() - heartbeat > 25:
            print(json.dumps({"stage": "smart_running", "run_id": run_id, "status": state["status"]}), flush=True)
            heartbeat = time.monotonic()
        time.sleep(.3)
    api.json("/api/smart/runs/" + run_id + "/cancel", "POST", {})
    raise TimeoutError("smart_batch_timeout")


def upload_text(api, name, text, doc_type):
    query = urlencode({"name": name, "company": "甲公司", "period": "2023Y",
                       "report_date": "2024-04-01" if doc_type == "annual_report" else "2024-05-01",
                       "doc_type": doc_type})
    _, _, raw = api.raw("/api/kb/upload?" + query, "POST", body=text.encode("utf-8"))
    return json.loads(raw)["doc_id"]


def validate_occurrences(payload):
    items = payload["items"]
    summary = payload["summary"]
    require(items and len({item["cid"] for item in items}) == len(items), "occurrence_ids_missing_or_duplicate")
    require(summary["totals"]["total"] == len(items), "occurrence_total_does_not_match_rows")
    require(all(item.get("text") and item.get("page", 0) >= 1 and len(item.get("span", [])) == 2
                for item in items), "occurrence_source_addresses_missing")
    counts = {status: sum(item["status"] == status for item in items)
              for status in ("verified", "violated", "unverifiable", "skipped")}
    require(all(counts[status] == summary["totals"][status] for status in counts), "occurrence_verdict_total_mismatch")
    require(all(item.get("evidence") and item.get("operands") for item in items
                if item["status"] in {"verified", "violated"}), "judged_occurrence_lacks_evidence_or_operands")
    return {"occurrences": len(items), "assertions": summary["assertions"]["total"], "states": counts,
            "source_addresses": True, "judged_rows_have_evidence": True}


def verify_kernel_zip(raw, state, child):
    roots = {state["run_id"] + "/" + child["item_id"]: Path(state["batch"]["out"]) / child["item_id"],
             state["run_id"] + "/native/" + child["native_run_id"]: Path(child["canonical_run_path"])}
    checked = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(archive.testzip() is None and len(archive.namelist()) == len(set(archive.namelist())), "invalid_kernel_export")
        for member in archive.namelist():
            relative = PurePosixPath(member)
            require(not relative.is_absolute() and ".." not in relative.parts and ":" not in member, "unsafe_export_member")
        for prefix, root in roots.items():
            for filename in ("results.json", "run_manifest.json"):
                require(archive.read(prefix + "/" + filename) == (root / filename).read_bytes(), "export_changed_frozen_json")
            manifest = json.loads((root / "run_manifest.json").read_text("utf-8"))
            for entry in manifest["outputs"]:
                name = prefix + "/" + entry["path"]
                data = archive.read(name)
                require(len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"], "export_changed_frozen_output")
                checked += 1
    return {"frozen_outputs_verified": checked, "frozen_bytes_equal": True, "zip_sha256": hashlib.sha256(raw).hexdigest()}


def homepage_kernel(api, out, timeout):
    from playwright.sync_api import expect, sync_playwright
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(api.base, wait_until="networkidle")
            require(page.locator("#file").count() == 1, "current_upload_homepage_missing")
            page.screenshot(path=str(out / "01-homepage.png"))
            page.locator("#file").set_input_files([
                str(ROOT / "samples/smart/星河智造_2023年年度报告.pdf"),
                str(ROOT / "samples/smart/虚构证券_2024-04-25_星河智造深度报告.pdf")])
            expect(page.locator("#queue tbody tr")).to_have_count(2, timeout=90000)
            for index in range(2):
                expect(page.locator("#queue tbody tr").nth(index)).to_contain_text("已入库", timeout=90000)
            expect(page.locator("#start")).to_be_enabled(timeout=90000)
            expect(page.locator("#queue tbody")).to_contain_text("已入库", timeout=90000)
            page.locator("details.options summary").click()
            page.locator("#optLlm").uncheck()
            page.locator("#optStrict").uncheck()
            page.screenshot(path=str(out / "02-uploaded-fictional-materials.png"))
            with page.expect_response(lambda response: response.url.endswith("/api/smart/run")
                                      and response.request.method == "POST") as response:
                page.locator("#start").click()
            run_id = response.value.json()["run_id"]
            state, child, base, payload = wait_batch(api, run_id, timeout)
            page.wait_for_selector("#resultBody:not([hidden])", timeout=30000)
            page.screenshot(path=str(out / "03-kernel-results.png"), full_page=True)
            page.locator("#tab-wrong [data-cid]").first.click()
            page.wait_for_selector("#drawer:not([hidden])")
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(out / "04-source-evidence-drawer.png"))
            page.locator("#dClose").click()
            page.locator('#rTabs [data-tab="all"]').click()
            page.screenshot(path=str(out / "05-occurrence-ledger.png"), full_page=True)
            report_url = page.locator("#rHtml").get_attribute("href")
            require(report_url and report_url.endswith("/report.html"), "report_link_missing")
            report = page.context.new_page()
            report.on("pageerror", lambda error: errors.append(str(error)))
            report.goto(api.base + report_url, wait_until="networkidle")
            require(len(report.inner_text("body")) > 200, "report_is_empty")
            report.screenshot(path=str(out / "06-audit-report.png"), full_page=True)
            replay = api.json(base + "/replay", "POST", {}, timeout=120)
            (out / "kernel-replay.json").write_text(json.dumps(replay, ensure_ascii=False, indent=2), "utf-8")
            require(replay.get("status") == "identical" and replay.get("numeric_equal") is True
                    and replay.get("integrity_ok") is True and replay.get("network_calls") == 0,
                    "kernel_replay_not_identical:" + json.dumps(replay, ensure_ascii=False))
            require(not errors, "workbench_javascript_error")
            proof = validate_occurrences(payload)
            require(all(proof["states"][s] > 0 for s in ("verified", "violated", "unverifiable")), "kernel_missing_three_states")
            evidence_id = next(evidence for item in payload["items"] if item["status"] in {"verified", "violated"}
                               for evidence in item.get("evidence", []) if ":p" in evidence)
            _, mime, png = api.raw("/api/kb/evidence/" + quote(evidence_id, safe="") + ".png")
            require("image/png" in mime and png.startswith(b"\x89PNG\r\n\x1a\n"), "source_page_png_invalid")
            (out / "source-page.png").write_bytes(png)
            _, _, raw = api.raw("/api/smart/runs/" + run_id + "/export", timeout=120)
            proof.update(export=verify_kernel_zip(raw, state, child))
            require(payload.get("model", {}).get("request_count", 0) == 0, "kernel_demo_made_model_request")
            (out / "fictional-kernel-export.zip").write_bytes(raw)
            return {"run_id": run_id, "native_run_id": child["native_run_id"], "model_requests": 0,
                    "page_errors": errors, "report_readable": True, "png_bytes": len(png), "evidence_id": evidence_id,
                    "replay": replay, **proof}, payload
        finally:
            browser.close()


def small_smart(api, use_llm, timeout):
    filing = upload_text(api, "dev3虚构甲公司_annual.txt",
                         "甲公司2023年年度报告\n合并利润表\n单位：元\n项目  2023年度  2022年度\n"
                         "营业收入  100.00  90.00\n营业成本  60.00  50.00\n净利润  20.00  18.00\n", "annual_report")
    research = upload_text(api, "dev3虚构甲公司_research.txt",
                          "甲公司证券研究报告\n2023年公司营业收入为100.00元。\n2023年公司营业成本为65.00元。\n"
                          "2023年公司净利润为20.00元。\n2023年公司经营活动现金流量净额为15.00元。\n"
                          "2023年公司营业收入为100.00元。\n", "research_report")
    started = api.json("/api/smart/run", "POST", {"doc_ids": [filing, research], "mode": "check",
                       "options": {"use_llm": use_llm, "strict": False, "scope": "合并", "kb_lookup": False}})
    return wait_batch(api, started["run_id"], timeout)


def real_native(api, route, out, timeout):
    probe = api.json("/api/providers/" + quote(route["provider"], safe="") + "/test-agent", "POST",
                     {"model": route["model"]}, timeout=240)
    require(probe.get("status") == "passed" and probe.get("native_runtime") is True
            and probe.get("tool_roundtrip") is True, "saved_provider_tool_probe_failed")
    state, child, base, payload = small_smart(api, True, timeout)
    occurrence = validate_occurrences(payload)
    model = payload.get("model", {})
    require(model.get("status") == "success" and model.get("request_count", 0) > 0
            and model.get("mapped_claims", 0) > 0, "small_smart_live_model_failed")
    require(occurrence["occurrences"] > occurrence["assertions"], "repeat_occurrences_not_retained")
    same = [row for row in payload["items"] if row.get("metric") == "REVENUE" and row.get("relation") == "value"]
    require(len(same) >= 2 and all(row["status"] == "verified" for row in same), "repeat_verdict_projection_failed")
    canonical = api.json("/api/runs/" + child["native_run_id"] + "/results")
    target = next(row["claim_id"] for row in canonical["results"] if row["status"] == "verified")
    sid = payload.get("session_id") or api.json("/api/sessions", "POST", {"run_id": child["native_run_id"]})["session_id"]
    api.json("/api/sessions/" + sid + "/messages", "POST", {"question": "请使用mj_get_verdicts按claim_id查询"
             + target + "，只引用该项冻结detail槽位回答，并提交报告。"})
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        session = api.json("/api/sessions/" + sid)
        if session["status"] in {"ready", "failed", "cancelled"}:
            require(session["status"] == "ready", "material_followup_failed")
            break
        time.sleep(.3)
    else:
        api.json("/api/sessions/" + sid + "/cancel", "POST", {})
        raise TimeoutError("material_followup_timeout")
    message = session["messages"][-1]
    require(message["role"] == "assistant" and message.get("message_id")
            and message.get("agent_message_manifest_sha256"), "guarded_followup_not_frozen")
    require(session.get("usage", {}).get("model_requests", 0) > 0, "followup_had_no_real_model_request")
    require({"mj_get_verdicts", "mj_submit_report"} <= {row.get("tool") for row in session.get("events", [])
                                                              if row.get("status") == "success"}, "followup_tool_loop_missing")
    replay = api.json("/api/sessions/" + sid + "/messages/" + message["message_id"] + "/replay", "POST", {}, timeout=240)
    require(replay.get("status") == "passed" and replay.get("real_harness_loop") is True
            and replay.get("network_calls") == 0 and replay.get("answer_equal") is True
            and replay.get("references_equal") is True and replay.get("tool_trace_equal") is True,
            "message_offline_replay_failed")
    agent_replay = api.json("/api/replay", "POST", {"run": child["canonical_run_path"], "mode": "agent"}, timeout=timeout)
    require(agent_replay.get("status") == "passed" and agent_replay.get("real_harness_loop") is True
            and agent_replay.get("network_calls") == 0
            and all(agent_replay.get(key) is True for key in ("numeric_equal", "bindings_equal", "report_equal", "tool_trace_equal", "execution_equal")),
            "small_smart_cached_agent_replay_failed")
    return {"provider": route["provider"], "model": route["model"],
            "probe": {key: probe.get(key) for key in ("status", "native_runtime", "tool_roundtrip", "tool_calls", "tool_trace_hash", "latency_ms", "usage")},
            "batch_id": state["run_id"], "native_run_id": child["native_run_id"],
            "model_usage": {key: model.get(key) for key in ("status", "mode", "mapped_claims", "source_candidates", "request_count", "prompt_count", "tokens", "cost")},
            "partial": payload.get("partial", False), "occurrences": occurrence,
            "followup": {"session_id": sid, "message_id": message["message_id"], "usage": session["usage"],
                         "guarded_output_sha256": digest(message["text"]), "answer_not_copied": True},
            "message_replay": replay, "agent_replay": agent_replay}


def execute(args):
    from mingjing.config import SecretStore, app_root, ensure_workspace, load_config, save_config
    from mingjing.providers import resolve_route
    require(os.name == "nt", "dev3_delivery_smoke_requires_windows")
    require(not args.skip_real_model or args.source, "skip_real_model_is_only_allowed_in_source_simulation")
    out = Path(args.out).resolve()
    require(not out.exists() or not any(out.iterdir()), "output_directory_must_be_new")
    out.mkdir(parents=True, exist_ok=True)
    user_root = Path(args.saved_profile).resolve() if args.saved_profile else app_root()
    user_paths = [user_root / name for name in ("config.json", "secrets.json", "secrets.key", "config.v1.bak.json")]
    snapshot = lambda: {path.name: sha256(path) if path.is_file() else None for path in user_paths}
    before = snapshot()
    secrets, process, stream, api, home = [], None, None, None, None
    kept = False
    receipt = {"status": "failed", "version": "1.0.0.dev3", "started_at": now(), "checks": [],
               "scope": "current upload UI; fictional kernel and small saved-provider native task with one material follow-up",
               "independent_windows_vm": "not_executed", "human_gold": "not_executed", "D3_time_holdout": "not_executed",
               "real_responses_copied_to_output": False, "user_profile_modified": False,
               "driver_environment": "developer_host_with_python_and_playwright", "source_simulation": args.source}
    stage = "isolate_saved_profile"

    def passed(name, **details):
        receipt["checks"].append({"name": name, "status": "passed", **details})
        print(json.dumps({"stage": name, "status": "passed"}, ensure_ascii=False), flush=True)

    try:
        raw_config = json.loads((user_root / "config.json").read_text("utf-8"))
        require(raw_config.get("version", 1) >= 2, "saved_configuration_requires_ui_migration_before_readonly_smoke")
        require(json.loads((user_root / "secrets.json").read_text("utf-8")).get("backend") == "dpapi",
                "saved_secret_store_requires_dpapi_before_readonly_smoke")
        cfg = load_config(user_root / "config.json")
        saved = cfg.to_dict()
        route = resolve_route(saved)
        source_store = SecretStore(user_root)  # DPAPI format checked first; no migration/writes.
        home = Path(tempfile.mkdtemp(prefix="mingjing-dev3-private-"))
        isolated_store = SecretStore(home)
        for role in ("default", "planner", "verifier", "reporter"):
            selected = resolve_route(saved, role)
            value = source_store.get(selected["credential_ref"])
            require(bool(value), "saved_provider_credential_missing")
            if value not in secrets:
                secrets.append(value)
            isolated_store.set(selected["credential_ref"], value)
        # Change only driver storage/network-binding policy in the clone.
        cfg.workspace.data_root = str(home / "workspace")
        cfg.workspace.output_root = str(home / "workspace/runs")
        cfg.workspace.temp_root = str(home / "tmp")
        cfg.workspace.knowledge_root = str(home / "workspace/knowledge")
        cfg.workspace.draft_path = cfg.workspace.corpus_root = cfg.workspace.corpus_index = cfg.workspace.facts_path = ""
        cfg.logging.directory = str(home / "logs")
        cfg.server.host, cfg.server.port, cfg.server.auto_open_browser = "127.0.0.1", 0, False
        cfg.server.bearer_token_required = False
        cfg.security.allowed_roots, cfg.security.allow_external_paths = [str(home), str(ROOT / "samples")], True
        cfg.harness.enabled, cfg.harness.runtime_mode = False, "embedded"
        cfg.harness.executable = cfg.harness.home = cfg.harness.patch_file = cfg.harness.working_dir = ""
        ensure_workspace(cfg)
        Path(cfg.workspace.knowledge_root).mkdir(parents=True, exist_ok=True)
        save_config(cfg, home / "config.json")
        require(cfg.to_dict()["providers"] == saved["providers"] and cfg.to_dict()["agent"] == saved["agent"], "saved_routes_or_budget_changed")
        passed(stage, original_readonly=True, dpapi_clone=True, routes_and_budgets_unchanged=True)

        stage = "identify_exact_build"
        if args.source:
            executable = Path(sys.executable)
            files = [path for root in (ROOT / "mingjing", ROOT / "kernel", ROOT / "web", ROOT / "rulepack", ROOT / "scripts")
                     for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
            receipt.update(exe_sha256="not_applicable_source_simulation",
                           source_sha256=digest([{ "path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in sorted(files)]),
                           source_hash_basis="simulation code/UI/rule inventory; not the final build lock")
        else:
            original = Path(args.exe).resolve(strict=True)
            build = json.loads(Path(args.build_receipt).read_text("utf-8"))
            lock = json.loads(Path(args.source_lock).read_text("utf-8"))
            require(build["version"] == "1.0.0.dev3" and sha256(original) == build["sha256"], "exe_does_not_match_dev3_build_receipt")
            require(lock["source_sha256"] == build["source_sha256"] and digest(lock["inputs"]) == lock["source_sha256"], "source_build_lock_mismatch")
            for entry in lock["inputs"]:
                path = (ROOT / entry["path"]).resolve()
                require(path.is_relative_to(ROOT) and path.is_file() and sha256(path) == entry["sha256"], "source_changed_since_build:" + entry["path"])
            executable = home / "clean-delivery/MingJing.exe"
            executable.parent.mkdir()
            shutil.copy2(original, executable)
            require(sha256(executable) == build["sha256"], "isolated_exe_copy_hash_mismatch")
            receipt.update(exe_sha256=build["sha256"], source_sha256=build["source_sha256"],
                           runtime_manifest_sha256=build["runtime_manifest_sha256"], source_hash_basis="exact frozen build-source-lock inputs")
        passed(stage, exe_sha256=receipt["exe_sha256"], source_sha256=receipt["source_sha256"])
        environment = clean_environment(home, home)
        no_tools = not any(shutil.which(name, path=environment["PATH"]) for name in ("python", "node", "pnpm"))
        require(no_tools, "external_python_node_pnpm_in_child_path")
        receipt["restricted_child_path"] = environment["PATH"]

        stage = "cold_start_port_conflict"
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            blocked = occupied.getsockname()[1]
            process, stream, api, record = start(args, executable, home, environment, out / "launcher-first.log", blocked)
        require(record["port"] != blocked, "occupied_port_not_recovered")
        capabilities = api.json("/api/config")["capabilities"]
        require(capabilities.get("pdf") and capabilities.get("harness"), "pdf_or_embedded_harness_missing")
        require(api.json("/api/health")["version"] == "1.0.0.dev3", "health_version_mismatch")
        passed(stage, occupied_port_recovered=True, capabilities=capabilities)

        stage = "duplicate_launch"
        command = ([sys.executable, "-m", "mingjing.launcher"] if args.source else [str(executable)]) + ["--home", str(home), "--no-browser"]
        with (out / "launcher-duplicate.log").open("wb") as duplicate_log:
            duplicate = subprocess.Popen(command, cwd=ROOT if args.source else executable.parent, env=environment,
                                         stdout=duplicate_log, stderr=subprocess.STDOUT,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                require(duplicate.wait(timeout=45) == 0, "duplicate_launch_failed")
            except Exception:
                force_cleanup(duplicate, None, None)
                raise
        require(api.json("/api/health")["instance_id"] == record["instance_id"], "duplicate_replaced_service")
        passed(stage, existing_instance_reused=True)

        stage = "homepage_upload_kernel_occurrences_report_png_export_replay"
        kernel, payload = homepage_kernel(api, out, args.timeout_s)
        passed(stage, **kernel)

        stage = "invalid_scope_and_path_guards"
        rejected = {"invalid_scope": api.reject("/api/smart/run", data={"doc_ids": [], "options": {"scope": "集团自行推测口径"}}),
                    "invalid_scope_type": api.reject("/api/smart/run", data={"doc_ids": [], "options": {"scope": []}}),
                    "path_traversal": api.reject("/api/kb/%2e%2e/config.json", "GET"),
                    "upload_path": api.reject("/api/kb/upload?name=..%2Fconfig.json", data={}),
                    "task_path_override": api.reject("/api/smart/run", data={"doc_ids": [], "draft": str(user_root / "config.json")})}
        current = api.json("/api/config")["config"]
        api.json("/api/config", "PATCH", {"revision": current["revision"], "security": {"allow_external_paths": False}})
        try:
            rejected["outside_allowed_roots"] = api.reject("/api/draft/preview", data={"draft": str(user_root / "config.json")})
        finally:
            current = api.json("/api/config")["config"]
            api.json("/api/config", "PATCH", {"revision": current["revision"], "security": {"allow_external_paths": True}})
        passed(stage, **rejected)

        stage = "small_duplicate_occurrence_kernel_control"
        _, _, _, small = small_smart(api, False, args.timeout_s)
        proof = validate_occurrences(small)
        require(proof["occurrences"] > proof["assertions"], "duplicate_occurrences_missing")
        require(all(proof["states"][status] > 0 for status in ("verified", "violated", "unverifiable")), "small_control_missing_three_states")
        passed(stage, **proof)

        stage = "real_saved_provider_native_and_material_message_replay"
        if args.skip_real_model:
            receipt["checks"].append({"name": stage, "status": "not_executed_source_simulation"})
        else:
            passed(stage, **real_native(api, route, out, args.timeout_s))

        stage = "normal_exit_restart_history"
        expected_revision = api.json("/api/config")["revision"]
        shutdown(process, stream, api, home)
        process = stream = api = None
        process, stream, api, restarted = start(args, executable, home, environment, out / "launcher-restart.log")
        require(restarted["instance_id"] != record["instance_id"], "restart_kept_old_instance_id")
        require(api.json("/api/config")["revision"] == expected_revision, "restart_config_revision_changed")
        history = api.json("/api/smart/runs")["runs"]
        require(kernel["run_id"] in {row["run_id"] for row in history}, "smart_history_not_restored")
        frozen = api.json("/api/runs/" + kernel["native_run_id"] + "/results")
        require(frozen["model"].get("request_count", 0) == 0, "kernel_history_model_count_changed")
        passed(stage, configuration_persisted=True, batch_history_persisted=True, new_instance=True)
        if args.keep_open:
            kept = True
            receipt["kept_open"] = {"url": api.base, "pid": process.pid, "private_profile": str(home),
                                    "private_profile_must_not_be_packaged": True,
                                    "shutdown_endpoint": api.base + "/api/shutdown"}
        else:
            shutdown(process, stream, api, home)
            process = stream = api = None
        stage = "credentials_and_original_profile"
        scanned = private_scan(list(home.rglob("*")) + list(out.rglob("*")), secrets,
                               {home / "secrets.json", home / "secrets.key"})
        require(before == snapshot(), "saved_user_configuration_or_credentials_changed")
        passed(stage, scanned_files=scanned, plaintext_credentials_found=0,
               dpapi_secret_files_excluded=True, user_profile_unchanged=True)
        receipt["status"] = "passed_source_simulation_without_real_provider" if args.skip_real_model else (
            "passed_source_scoped_dev3" if args.source else "passed_scoped_dev3_candidate")
    except Exception as exc:
        receipt["checks"].append({"name": stage, "status": "failed", "error": type(exc).__name__ + ": " + str(exc)})
    finally:
        receipt["user_profile_modified"] = before != snapshot()
        if not kept or receipt["status"] == "failed":
            force_cleanup(process, stream, api)
            if home and home.exists():
                # Only remove the exact private directory created by this run.
                require(home.parent == Path(tempfile.gettempdir()).resolve()
                        and home.name.startswith("mingjing-dev3-private-"), "private_cleanup_target_invalid")
                try:
                    shutil.rmtree(home)
                    receipt["private_profile_removed"] = True
                except OSError:
                    receipt["private_profile_removed"] = False
                    receipt["private_profile_cleanup_pending"] = str(home)
        elif stream and not stream.closed:
            stream.close()
        if receipt["user_profile_modified"]:
            receipt["status"] = "failed"
        receipt["finished_at"] = now()
        receipt = redact(receipt, secrets)
        (out / "smoke.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), "utf-8")
        # Scan the sealed summary too; never publish copied live response text.
        private_scan(out.rglob("*"), secrets, set())
    return receipt


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--exe", type=Path)
    source.add_argument("--source", action="store_true")
    parser.add_argument("--build-receipt", type=Path, default=ROOT / "dist/MingJing-build.json")
    parser.add_argument("--source-lock", type=Path, default=ROOT / "dist/build-source-lock.json")
    parser.add_argument("--saved-profile", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout-s", type=int, default=900)
    parser.add_argument("--skip-real-model", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args(argv)
    result = execute(args)
    print(json.dumps({"status": result["status"], "out": str(args.out.resolve()),
                      "kept_open": result.get("kept_open")}, ensure_ascii=False))
    return 0 if result["status"].startswith("passed_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
