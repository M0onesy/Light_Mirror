from __future__ import annotations

"""Offline replay that executes the frozen claims against the frozen facts.

The replay never loads a draft parser, model adapter, HTTP client or Harness.
The original artifacts are read-only; replay records are separate outputs.
"""

import json
import time
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from .audit import digest, kernel_fingerprint
from .evidence import sha256_file
from .facts import FactStore, fact_from_dict
from .judge import Judge
from .models import AtomicClaim
from .file_io import atomic_write_text, io_path, record_path


def _read_json(path: Path) -> Any:
    return json.loads(io_path(path).read_text(encoding="utf-8"))


def _check_integrity(root: Path, lock: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if lock.get("schema_version", 0) < 2 or not lock.get("outputs"):
        issues.append({"kind": "legacy_manifest", "reason": "运行未保存完整输出哈希；需重新核查生成冻结运行"})
    for group in ("inputs", "outputs"):
        for entry in lock.get(group, []):
            raw = entry.get("path", "")
            path = Path(raw) if group == "inputs" else record_path(io_path(root / raw).resolve())
            if group == "outputs" and not path.is_relative_to(record_path(io_path(root).resolve())):
                issues.append({"kind": "unsafe_output_path", "path": raw})
                continue
            if not io_path(path).is_file():
                issues.append({"kind": "missing_file", "group": group, "path": raw})
                continue
            actual = sha256_file(path)
            if actual != entry.get("sha256"):
                issues.append({"kind": "hash_mismatch", "group": group, "path": raw, "expected": entry.get("sha256"), "actual": actual})
    frozen_kernel = lock.get("kernel_fingerprint")
    current_kernel = kernel_fingerprint()
    if not frozen_kernel:
        issues.append({"kind": "missing_kernel_fingerprint"})
    elif frozen_kernel != current_kernel:
        issues.append({"kind": "kernel_changed", "expected": digest(frozen_kernel), "actual": digest(current_kernel)})
    required = {"claims.json", "facts.json", "results.json", "audit.jsonl"}
    recorded = {entry.get("path") for entry in lock.get("outputs", [])}
    for name in sorted(required - recorded):
        issues.append({"kind": "unlocked_required_output", "path": name})
    return issues


def _check_audit(root: Path, run_id: str) -> list[dict[str, Any]]:
    path = root / "audit.jsonl"
    if not io_path(path).is_file():
        return [{"kind": "missing_audit"}]
    issues = []
    previous = ""
    number = 0
    for line in io_path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        number += 1
        try:
            event = json.loads(line)
            saved = event.pop("event_hash", None)
            if digest(event) != saved or event.get("prev_event_hash") != previous:
                issues.append({"kind": "audit_chain_mismatch", "line": number})
            if event.get("run_id") != run_id:
                issues.append({"kind": "audit_run_mismatch", "line": number})
            previous = saved
        except (json.JSONDecodeError, AttributeError):
            issues.append({"kind": "invalid_audit_event", "line": number})
    return issues


def _claim_from_dict(data: dict[str, Any]) -> AtomicClaim:
    if data.get("candidate_value") is not None:
        try:
            if not Decimal(str(data["candidate_value"])).is_finite():
                raise ValueError("non-finite claim value")
        except InvalidOperation as exc:
            raise ValueError("invalid claim value") from exc
    allowed = {item.name for item in fields(AtomicClaim)}
    clean = {key: value for key, value in data.items() if key in allowed}
    clean["caliber_hint"] = tuple(clean.get("caliber_hint", ()))
    return AtomicClaim(**clean)


def _field_differences(original: list[dict], replayed: list[dict]) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    if len(original) != len(replayed):
        differences.append({"kind": "result_count", "original": len(original), "replayed": len(replayed)})
    for index, (before, after) in enumerate(zip(original, replayed)):
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                differences.append({"index": index, "claim_id": after.get("claim_id"), "field": key, "original": before.get(key), "replayed": after.get(key)})
    return differences


def _analysis_differences(original: Any, replayed: Any, path: str = "analysis") -> list[dict[str, Any]]:
    """Compare every frozen analysis field without hiding changes in a digest."""
    if isinstance(original, dict) and isinstance(replayed, dict):
        return [difference for key in sorted(set(original) | set(replayed))
                for difference in _analysis_differences(original.get(key), replayed.get(key), f"{path}.{key}")]
    if isinstance(original, list) and isinstance(replayed, list):
        result = []
        if len(original) != len(replayed):
            result.append({"field": f"{path}.count", "original": len(original), "replayed": len(replayed)})
        for index, (before, after) in enumerate(zip(original, replayed)):
            result.extend(_analysis_differences(before, after, f"{path}[{index}]"))
        return result
    return [] if original == replayed else [{"field": path, "original": original, "replayed": replayed}]


def _frozen_pending(original: dict, frozen_claims: list[dict]) -> tuple[set[str], list[dict]]:
    """Validate new Agent execution omissions without changing legacy runs."""
    if 'pending_claim_ids' not in original:
        return set(), []
    pending = original['pending_claim_ids']
    if (not isinstance(pending, list) or any(not isinstance(mid, str) for mid in pending)
            or len(set(pending)) != len(pending)):
        return set(), [{'kind': 'invalid_pending_claim_ids'}]
    frozen_ids = [claim['claim_id'] for claim in frozen_claims]
    known = set(frozen_ids)
    pending_set = set(pending)
    issues = []
    if len(known) != len(frozen_ids):
        issues.append({'kind': 'duplicate_frozen_claim_ids'})
    if not pending_set.issubset(known):
        issues.append({'kind': 'pending_claim_outside_frozen_claims'})
    ledger = {row.get('claim_id'): row for row in original.get('coverage', {}).get('mentions', []) if row.get('claim_id')}
    for mid in pending:
        row = ledger.get(mid, {})
        if row.get('status') != 'pending' or row.get('reason') not in {'native_binding_not_submitted', 'native_verification_not_executed'}:
            issues.append({'kind': 'pending_claim_ledger_mismatch', 'claim_id': mid})
    results = original.get('results', [])
    judged = [row.get('claim_id') for row in results]
    if len(set(judged)) != len(judged) or pending_set.intersection(judged) or set(judged) | pending_set != known:
        issues.append({'kind': 'frozen_execution_partition_mismatch'})
    execution = original.get('execution', {})
    if not isinstance(execution, dict):
        return set(), issues + [{'kind': 'frozen_execution_metadata_mismatch'}]
    native = execution.get('native_verified_claim_ids', [])
    preflight = execution.get('preflight_claim_ids', [])
    if (not isinstance(native, list) or not isinstance(preflight, list)
            or any(not isinstance(mid, str) for mid in native + preflight)
            or len(set(native)) != len(native) or len(set(preflight)) != len(preflight)):
        return set(), issues + [{'kind': 'frozen_execution_metadata_mismatch'}]
    if (execution.get('pending_claim_ids') != pending or execution.get('judged_claim_ids') != judged
            or pending_set.intersection(native) or not set(native + preflight).issubset(judged)
            or set(native).intersection(preflight)):
        issues.append({'kind': 'frozen_execution_metadata_mismatch'})
    return pending_set if not issues else set(), issues


def replay_run(run: str | Path) -> dict[str, Any]:
    started = time.monotonic()
    root = record_path(io_path(run).resolve())
    original = _read_json(root / "results.json")
    lock_path = root / "run_manifest.json"
    lock = _read_json(lock_path) if io_path(lock_path).is_file() else {}
    frozen_claims = _read_json(root / "claims.json")
    frozen_facts = _read_json(root / "facts.json")
    run_id = original.get("run_id", "")
    issues = _check_integrity(root, lock) + _check_audit(root, run_id)
    if frozen_claims != original.get("claims"):
        issues.append({"kind": "claims_payload_mismatch"})
    if frozen_facts != original.get("facts"):
        issues.append({"kind": "facts_payload_mismatch"})
    if lock.get("run_id") != run_id:
        issues.append({"kind": "manifest_run_mismatch"})
    facts = []
    for item in frozen_facts:
        try:
            numeric = Decimal(str(item["value"]))
            if not numeric.is_finite():
                raise ValueError("non-finite fact value")
        except (KeyError, InvalidOperation) as exc:
            raise ValueError("冻结事实含非法数值，不能重放") from exc
        facts.append(fact_from_dict(item))
    claims = [_claim_from_dict(item) for item in frozen_claims]
    pending, pending_issues = _frozen_pending(original, frozen_claims)
    issues.extend(pending_issues)
    judge = Judge(FactStore(facts), original.get("report_date"))
    replayed = []
    replayed_analysis = None
    with localcontext() as context:
        settings = lock.get("decimal_context", {})
        for key in ("prec", "rounding", "Emin", "Emax", "capitals", "clamp"):
            if key in settings:
                setattr(context, key, settings[key])
        for signal in context.traps:
            if signal.__name__ in settings.get("traps", {}):
                context.traps[signal] = settings["traps"][signal.__name__]
        for claim in claims:
            if claim.claim_id in pending:
                continue
            result = judge.verify(claim).to_dict()
            result["claim_text"] = claim.text
            result["source_span"] = {"start": claim.start, "end": claim.end}
            replayed.append(result)
        if original.get("analysis") is not None:
            from .analysis import analyze_financials
            analysis = original["analysis"]
            parameters = analysis.get("parameters", {})
            replayed_analysis = analyze_financials(
                FactStore(facts), analysis["company"], analysis.get('requested_periods', analysis["periods"]), analysis.get("report_date"),
                analysis.get("scope", "合并"), confidence_threshold=parameters.get("confidence_threshold", "0.95"),
                cash_threshold=parameters.get("cash_threshold", "0.5"),
                non_recurring_threshold=parameters.get("non_recurring_threshold", "0.30"),
                disclosures=original.get('accounting_disclosures', []),
            )
    summary = {key: sum(item["status"] == key for item in replayed) for key in ("verified", "violated", "abstained")}
    differences = _field_differences(original.get("results", []), replayed)
    if original.get("summary") != summary:
        differences.append({"field": "summary", "original": original.get("summary"), "replayed": summary})
    differences.extend(_analysis_differences(original.get("analysis"), replayed_analysis))
    if original.get('coverage_statistics') is not None:
        from .coverage_stats import coverage_statistics
        recomputed_statistics = coverage_statistics(original.get('coverage'), frozen_claims, replayed)
        differences.extend(_analysis_differences(original['coverage_statistics'], recomputed_statistics, 'coverage_statistics'))
    numeric_equal = not differences
    output = {
        "run_id": run_id, "mode": "replay", "status": "passed" if numeric_equal and not issues else "failed",
        "recomputed": True, "network_calls": 0, "claims_recomputed": len(claims)-len(pending), "facts_loaded": len(facts),
        "claims_frozen": len(claims), "pending_claims": len(pending),
        "numeric_equal": numeric_equal, "integrity_ok": not issues, "summary": summary,
        "result_digest": digest(replayed), "original_result_digest": digest(original.get("results", [])),
        "source_digest": digest(frozen_claims), "facts_digest": digest(frozen_facts),
        "analysis_recomputed": replayed_analysis is not None,
        "analysis_metrics_recomputed": len((replayed_analysis or {}).get("metrics", [])),
        "analysis_digest": digest(replayed_analysis), "original_analysis_digest": digest(original.get("analysis")),
        "differences": differences, "integrity_issues": issues,
        "elapsed_ms": round((time.monotonic() - started) * 1000), "time": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_text(root / "replay.json", json.dumps(output, ensure_ascii=False, indent=2))
    with (root / "replay_history.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n")
    return output
