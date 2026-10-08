"""Scoped native EXE acceptance; uses saved routes only in an isolated DPAPI profile.

The driver host has Python. The EXE's PATH contains only Windows System32 and
its original user profile stays read-only. This is not clean-VM or G5 proof.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time
import uuid
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from scripts.delivery_smoke import clean_environment, force_cleanup, launch, require, sha256, shutdown
except ImportError:
    from delivery_smoke import clean_environment, force_cleanup, launch, require, sha256, shutdown


class LocalAPI:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))

    def raw(self, path, method="GET", data=None, timeout=30):
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = Request(self.base + path, data=body, method=method,
                          headers={"Content-Type": "application/json; charset=utf-8"})
        with self.opener.open(request, timeout=timeout) as response:
            return response.headers.get("Content-Type", ""), response.read()

    def json(self, path, method="GET", data=None, timeout=30):
        content_type, body = self.raw(path, method, data, timeout)
        require("application/json" in content_type, "unexpected_local_response_type")
        return json.loads(body.decode("utf-8"))


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


def run_task(api, payload, timeout_s):
    task = api.json("/api/run", "POST", payload)
    run_id = task.get("run_id") or task["id"]
    deadline = time.monotonic() + timeout_s
    last_heartbeat = 0
    while time.monotonic() < deadline:
        state = api.json("/api/runs/" + run_id)
        if state["status"] in {"completed", "partial"}:
            return state, api.json("/api/runs/" + run_id + "/results")
        require(state["status"] not in {"failed", "cancelled"}, "native_exe_task_failed")
        if time.monotonic() - last_heartbeat > 25:
            print(json.dumps({"stage": "running", "run_id": run_id, "status": state["status"]}), flush=True)
            last_heartbeat = time.monotonic()
        time.sleep(0.3)
    api.json("/api/runs/" + run_id + "/cancel", "POST", {})
    raise TimeoutError("native_exe_task_budget_exhausted")


def artifact_scan(paths, secrets):
    raw_keys = [secret.encode("utf-8") for secret in secrets if secret]
    overlap = max((len(key) for key in raw_keys), default=1) - 1
    scanned = 0
    for path in paths:
        if not path.is_file() or path.name in {"instance.lock"} or path.suffix == ".lock":
            continue
        scanned += 1
        tail = b""
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                data = tail + block
                require(not any(key in data for key in raw_keys), "plaintext_key_in_acceptance_artifact")
                tail = data[-overlap:] if overlap else b""
    return scanned


def ask_material_question(api, session_id, question, timeout_s=210):
    api.json('/api/sessions/'+session_id+'/messages', 'POST', {'question': question})
    deadline = time.monotonic()+timeout_s
    while time.monotonic() < deadline:
        state = api.json('/api/sessions/'+session_id)
        if state['status'] in {'ready', 'failed', 'cancelled'}:
            require(state['status'] == 'ready', 'native_material_question_failed')
            require(state['messages'][-1].get('role') == 'assistant', 'native_question_missing_guarded_answer')
            require(state.get('usage', {}).get('model_requests', 0) > 0, 'native_question_no_model_request')
            require(any(event.get('tool') == 'mj_submit_report' and event.get('status') == 'success'
                        for event in state.get('events', [])), 'native_question_no_slot_submission')
            return state
        time.sleep(0.3)
    api.json('/api/sessions/'+session_id+'/cancel', 'POST', {})
    raise TimeoutError('native_material_question_timeout')


def execute(args):
    from mingjing.config import AppConfig, ConfigManager, SecretStore, app_root, ensure_workspace, save_config
    from mingjing.providers import resolve_route
    require(os.name == "nt", "native_exe_acceptance_requires_windows")
    out = Path(args.out).resolve()
    require(not out.exists() or not any(out.iterdir()), "acceptance_directory_must_be_new")
    out.mkdir(parents=True, exist_ok=True)
    user_root = app_root()
    user_paths = [user_root / name for name in ("config.json", "secrets.json", "secrets.key", "config.v1.bak.json")]
    before = {path.name: sha256(path) if path.is_file() else None for path in user_paths}
    require((user_root / "config.json").is_file(), "saved_model_configuration_is_required")
    require(json.loads((user_root / "config.json").read_text(encoding="utf-8")).get("version", 1) >= 2,
            "migrate_configuration_in_ui_before_readonly_acceptance")
    manager = ConfigManager()
    cfg = manager.get()
    config = cfg.to_dict()
    route = resolve_route(config)
    values = []
    home = out / "isolated-profile" / "明镜原生验收"
    clone = SecretStore(home)
    for role in ("default", "planner", "verifier", "reporter"):
        selected = resolve_route(config, role)
        ref = selected["credential_ref"]
        value = manager.get_secret(ref)
        require(bool(value), "selected_provider_credential_missing")
        if value not in values:
            values.append(value)
        clone.set(ref, value)
    cfg.workspace.data_root = str(home / "workspace")
    cfg.workspace.output_root = str(home / "workspace/runs")
    cfg.workspace.temp_root = str(home / "tmp")
    cfg.logging.directory = str(home / "logs")
    cfg.server.host, cfg.server.port, cfg.server.auto_open_browser = "127.0.0.1", 0, False
    cfg.server.bearer_token_required = False
    cfg.security.allowed_roots = [str(home / "workspace")]
    cfg.security.allow_external_paths = True
    cfg.agent.enabled, cfg.agent.cross_provider_fallback = True, False
    cfg.agent.budget.max_runtime_s = args.budget_s
    cfg.agent.budget.max_model_calls = args.model_calls
    cfg.agent.budget.max_total_tokens = args.total_tokens
    cfg.harness.enabled, cfg.harness.runtime_mode, cfg.harness.executable, cfg.harness.home = False, "embedded", "", ""
    cfg.harness.patch_file, cfg.harness.working_dir = "", ""
    ensure_workspace(cfg)
    save_config(cfg, home / "config.json")
    copied = out / "delivery"
    copied.mkdir()
    exe = copied / "MingJing.exe"
    shutil.copyfile(Path(args.exe).resolve(strict=True), exe)
    environment = clean_environment(out, home)
    require(not any(shutil.which(name, path=environment["PATH"]) for name in ("python", "node", "pnpm")), "external_tools_present_in_child_path")
    build = json.loads((ROOT / "dist/MingJing-build.json").read_text(encoding="utf-8"))
    require(sha256(exe) == build["sha256"], "exact_exe_build_receipt_mismatch")
    receipt = {"product": "MingJing", "version": build["version"], "status": "failed",
        "exe_sha256": sha256(exe), "source_sha256": build["source_sha256"],
        "runtime_manifest_sha256": build["runtime_manifest_sha256"],
        "profile_isolated": True, "clean_path_no_python_node": True,
        "acceptance_budget": asdict(cfg.agent.budget),
        "acceptance_budget_scope": "Explicit larger full-report acceptance operating point; does not certify the saved/default user budget",
        "environment": {"kind": "developer_host_with_restricted_child_path", "clean_windows_vm": False},
        "real_report_scope": "Complete 44-page Parallel Technology research PDF; deterministic annual-report evidence as of 2024-04-22",
        "started_at": datetime.now(timezone.utc).isoformat(), "checks": {
            "human_gold": "not_executed", "D3_time_holdout": "not_executed", "clean_windows_vm": "not_executed",
            "real_providers": {"kimi": {"status": "not_executed"}, "openai": {"status": "not_executed"}}},
        "release_gates": {gate: "not_accepted_by_this_scoped_driver" for gate in ("G0", "G1", "G4", "G5", "G6", "G7")},
        "limitations": ["No clean Windows VM proof", "No independent human gold or full G5 proof", "Coverage is processing coverage, not measured identification recall", "Full-report acceptance uses its recorded isolated budget; saved/default user budgets remain unmodified and unaccepted by this driver"]}
    process = stream = api = None
    completed_stages = []
    stage = "cold_start"
    try:
        process, stream, old_api, record = launch(exe, home, environment, out / "launcher.log")
        api = LocalAPI(record["url"])
        response = api.json("/api/config")
        capabilities = response["capabilities"]
        require(capabilities.get("pdf") is True and capabilities.get("harness") is True, "required_pdf_or_native_runtime_missing")
        receipt["checks"]["capabilities"] = capabilities
        require(api.json("/api/health").get("version") == build["version"], "frozen_health_version_mismatch")
        print(json.dumps({"stage": stage, "status": "passed", "version": build["version"]}), flush=True)
        completed_stages.append(stage)

        stage = "real_native_tool_probe"
        probe = api.json("/api/providers/" + quote(route["provider"], safe="") + "/test-agent", "POST", {"model": route["model"]}, timeout=360)
        require(probe.get("status") == "passed" and probe.get("native_runtime") is True and probe.get("tool_roundtrip") is True,
                "real_native_financial_tool_probe_failed")
        receipt["checks"]["native_agent"] = redact(probe, values)
        vendor = route.get("vendor", route["provider"])
        vendor = "deepseek" if vendor == "deepseek" else "kimi" if vendor == "moonshotai" else "openai" if vendor == "openai" else vendor
        receipt["checks"]["real_providers"][vendor] = {"status": "passed", "model": route["model"], "tool_roundtrip": True}
        print(json.dumps({"stage": stage, "status": "passed", "provider": route["provider"], "model": route["model"]}), flush=True)
        completed_stages.append(stage)

        stage = "kernel_demo_and_three_replays"
        demo = api.json("/api/demo", "POST", {})
        request = {key: demo[key] for key in ("draft", "corpus", "company", "period", "report_date")}
        state, results = run_task(api, {**request, "mode": "kernel"}, 120)
        require(all(results["summary"].get(status, 0) > 0 for status in ("verified", "violated", "abstained")), "kernel_demo_requires_all_three_states")
        replays = [api.json("/api/replay", "POST", {"run": state["out"]}) for _ in range(3)]
        require(all(item.get("status") == "passed" and item.get("recomputed") and item.get("integrity_ok") and item.get("numeric_equal") and item.get("network_calls") == 0 for item in replays), "three_kernel_recomputations_failed")
        receipt["checks"]["replays"] = replays
        receipt["checks"]["kernel_demo"] = {"run_id": state["id"], "summary": results["summary"]}
        baseline = results["results"]
        completed_stages.append(stage)

        stage = "native_demo_and_offline_agent_replay"
        native_state, native = run_task(api, {**request, "mode": "harness"}, args.budget_s + 120)
        require(native.get("model", {}).get("status") == "success" and native["model"].get("mapped_claims", 0) > 0, "native_demo_semantic_binding_failed")
        replay = api.json("/api/replay", "POST", {"run": native_state["out"], "mode": "agent"}, timeout=args.budget_s + 120)
        require(replay.get("status") == "passed" and replay.get("real_harness_loop") is True and replay.get("network_calls") == 0 and replay.get("numeric_equal") is True,
                "native_agent_cache_replay_failed")
        receipt["checks"]["agent_replay"] = replay
        print(json.dumps({"stage": stage, "status": "passed", "cached_model_requests": replay.get("cached_model_requests")}), flush=True)
        completed_stages.append(stage)

        stage = "complete_real_research_report"
        draft = Path(args.draft).resolve(strict=True)
        corpus = Path(args.corpus).resolve(strict=True)
        import fitz
        with fitz.open(draft) as document:
            source_pages = document.page_count
        require(source_pages == 44, "full_report_must_be_the_registered_44_page_pdf")
        full_request = {"draft": str(draft), "corpus": str(corpus), "company": "并行科技", "period": "2023Y",
                        "report_date": "2024-04-22", "mode": "harness", "task_type": "check"}
        full_state, full = run_task(api, full_request, args.budget_s + 120)
        model = full.get("model", {})
        require(model.get("status") == "success" and model.get("mapped_claims", 0) > 0, "full_report_model_or_semantic_mapping_failed")
        trace = full.get("tool_trace") or json.loads((Path(full_state["out"]) / "tool_trace.json").read_text(encoding="utf-8"))
        verifies = sum(item.get("tool") == "mj_verify" and item.get("status") == "success" for item in trace)
        evidence = [eid for item in full["results"] for eid in item.get("evidence", []) if ":p" in eid]
        require(verifies > 0 and evidence, "full_report_requires_live_financial_verification_and_page_evidence")
        require(all(item.get("evidence") for item in full["results"] if item["status"] in {"verified", "violated"}), "unsupported_numeric_conclusion")
        receipt["checks"]["real_api"] = {"run_id": full_state["id"], "status": full_state["status"],
            "model": redact(model, values), "summary": full["summary"], "partial": full.get("partial", False),
            "coverage": full.get("coverage", {}).get("counts", {}), "source_draft_sha256": sha256(draft),
            "source_pages": source_pages,
            "live_verify_calls": verifies, "page_evidence_count": len(evidence)}
        evidence_id = evidence[0]
        content_type, png = api.raw("/api/evidence/" + quote(evidence_id, safe="") + "?run=" + full_state["id"] + "&format=png")
        require("image/png" in content_type and png.startswith(b"\x89PNG\r\n\x1a\n"), "real_report_evidence_page_invalid")
        (out / "real-evidence-page.png").write_bytes(png)
        print(json.dumps({"stage": stage, "status": "passed_scoped", "summary": full["summary"], "partial": full.get("partial", False), "live_verify_calls": verifies}), flush=True)
        completed_stages.append(stage)

        stage = 'native_material_questions_and_analysis'
        session = api.json('/api/sessions', 'POST', {'run_id': full_state['id']})
        sid = session['session_id']
        target = next(item['claim_id'] for item in full['results'] if item['status'] == 'verified')
        question = '请使用mj_get_verdicts按claim_id查询'+target+'，只引用该项冻结detail槽位回答，并提交报告。'
        first = ask_material_question(api, sid, question)
        require(any(event.get('tool') == 'mj_get_verdicts' and event.get('status') == 'success'
                    for event in first.get('events', [])), 'native_question_no_verdict_read')
        api.json('/api/sessions/'+sid+'/resume', 'POST', {})
        second = ask_material_question(api, sid, '请使用mj_analyze查询2023Y的GROSS_MARGIN，提交返回的slot_template和slot_ids，不读取其他指标。')
        require(any(event.get('tool') == 'mj_analyze' and event.get('status') == 'success'
                    for event in second.get('events', [])), 'native_question_no_registered_analysis')
        require(len([m for m in second['messages'] if m['role'] == 'assistant']) == 2,
                'native_question_session_history_not_preserved')
        receipt['checks']['material_questions'] = {
            'status': 'passed', 'session_id': sid, 'default_followup_budget': True,
            'first_usage': first['usage'], 'analysis_usage': second['usage'],
            'native_resume': True, 'guarded_answer_count': 2}
        (out/'native-material-questions.json').write_text(json.dumps(redact(second, values), ensure_ascii=False, indent=2), encoding='utf-8')
        completed_stages.append(stage)

        stage = "missing_native_runtime_visible_fallback"
        current = api.json("/api/config")["config"]
        api.json("/api/config", "PATCH", {"revision": current["revision"], "llm": {"enabled": False},
                 "harness": {"enabled": True, "runtime_mode": "external", "executable": str(home / "absent-runtime/dsh.exe")}})
        failure_state, failure = run_task(api, {**request, "mode": "harness"}, 120)
        events = api.json("/api/runs/" + failure_state["id"] + "/events")["events"]
        require(failure.get("model", {}).get("status") == "fallback" and any(item.get("event") == "fallback" for item in events), "native_failure_was_not_visible")
        numeric_fields = ("claim_id", "status", "error_type", "observed", "expected", "formula", "operands", "evidence", "tolerance")
        numeric = lambda rows: [{key: item.get(key) for key in numeric_fields} for item in rows]
        require(numeric(failure["results"]) == numeric(baseline), "native_failure_changed_kernel_numeric_results")
        receipt["checks"]["native_failure"] = {"status": "passed", "fallback_visible": True, "numeric_equal": True}
        completed_stages.append(stage)

        stage = "export_and_credential_isolation"
        _, raw = api.raw("/api/runs/" + full_state["id"] + "/export")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            require(archive.testzip() is None, "run_zip_corrupt")
            require({"results.json", "facts.json", "claims.json", "audit.jsonl"} <= set(archive.namelist()), "run_zip_missing_required_outputs")
            require(all(not any(secret.encode("utf-8") in archive.read(name) for secret in values) for name in archive.namelist()), "plaintext_key_in_run_zip")
        config_export = api.json("/api/config/export")
        require(not any(secret in json.dumps(config_export, ensure_ascii=False) for secret in values), "plaintext_key_in_configuration_export")
        paths = [home / "config.json", home / "secrets.json"]
        paths += list((home / "workspace/runs").rglob("*")) + list((home / "logs").rglob("*"))
        scanned = artifact_scan(paths, values)
        receipt["checks"]["secret_scan"] = "passed"
        receipt["checks"]["secret_scan_files"] = scanned
        receipt["checks"]["user_profile_unchanged"] = before == {path.name: sha256(path) if path.is_file() else None for path in user_paths}
        require(receipt["checks"]["user_profile_unchanged"], "original_user_profile_changed")
        receipt["status"] = "passed_scoped_native_agent_and_exe"
        completed_stages.append(stage)
    except Exception as exc:
        receipt["failed_stage"], receipt["error_type"] = stage, type(exc).__name__
    finally:
        if process is not None and api is not None and process.poll() is None:
            try:
                shutdown(process, stream, api, home)
                process = stream = api = None
                receipt["checks"]["normal_shutdown"] = "passed"
                completed_stages.append("normal_shutdown")
            except Exception:
                receipt["checks"]["normal_shutdown"] = "failed"
                receipt["status"] = "failed"
        force_cleanup(process, stream, api)
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        receipt = redact(receipt, values)
        (out / "acceptance.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        smoke = {"status": "passed" if receipt["status"] == "passed_scoped_native_agent_and_exe" else "failed",
                 "package": {"mode": "exe_only", "exe_sha256": receipt["exe_sha256"]},
                 "checks": [{"name": name, "status": "passed"} for name in completed_stages],
                 "user_profile_modified": not receipt["checks"].get("user_profile_unchanged", False),
                 "limitations": receipt["limitations"]}
        (out / "smoke.json").write_text(json.dumps(smoke, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": receipt["status"], "version": receipt["version"], "failed_stage": receipt.get("failed_stage"), "exe_sha256": receipt["exe_sha256"]}), flush=True)
    return 0 if receipt["status"] == "passed_scoped_native_agent_and_exe" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", default=str(ROOT / "dist/MingJing.exe"))
    parser.add_argument("--out", default=str(ROOT / ("runs/exe-native-acceptance-" + uuid.uuid4().hex[:8])))
    annual_root = ROOT.parent / "年报和半年报"
    drafts = sorted((annual_root / "存放初稿或者财务草稿").rglob("*AP202404221630960822.pdf"))
    parser.add_argument("--draft", default=str(drafts[0]) if drafts else "")
    parser.add_argument("--corpus", default=str(annual_root / "reports/920493_并行科技"))
    parser.add_argument("--budget-s", type=int, default=1800)
    parser.add_argument("--model-calls", type=int, default=256,
                        help="Isolated full-report acceptance model-call budget; leaves saved user configuration untouched")
    parser.add_argument("--total-tokens", type=int, default=1000000,
                        help="Isolated full-report acceptance total-token budget; recorded in the acceptance receipt")
    args = parser.parse_args()
    require(1 <= args.budget_s <= 1800, "acceptance_budget_out_of_range")
    require(args.model_calls > 0 and args.total_tokens > 0, "acceptance_budget_must_be_positive")
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
