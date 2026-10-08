from __future__ import annotations

"""Execute fixed gold cases through Judge; never load prefilled predictions.

This deliberately small acceptance slice is not the five-company CRRV-Bench.
The real facts are individually checked against cells in the original PDF.
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import fields, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from bench.evaluate import evaluate_records
from bench.stats import rate_with_interval
from mingjing.audit import AuditLog, digest, kernel_fingerprint
from mingjing.evidence import make_evidence_id, sha256_file
from mingjing.facts import FactStore, fact_from_dict
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions, parse_number_claim
from mingjing.models import AtomicClaim

DEFAULT_CASES = PACKAGE_ROOT / "bench" / "acceptance_cases.json"


def load_cases(path: str | Path = DEFAULT_CASES) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "mingjing.acceptance.v1":
        raise ValueError("unsupported acceptance-case schema")
    seen = set()
    for case in data["cases"]:
        if case["id"] in seen:
            raise ValueError("duplicate acceptance case id")
        seen.add(case["id"])
        if any(key in case for key in ("prediction", "predicted", "pred", "result", "actual")):
            raise ValueError("acceptance cases must not contain prefilled predictions")
        if case.get("gold") not in {"verified", "violated", "abstained"}:
            raise ValueError("invalid gold status")
    return data


def _real_facts(data: dict, annual_report: Path | None, verify_source: bool) -> tuple[list, dict, dict, dict]:
    description = data["annual_report"]
    source = annual_report or PACKAGE_ROOT.parent / description["relative_path"]
    expected_sha = description["sha256"]
    source_verified = False
    document = None
    if verify_source:
        if not source.is_file():
            raise ValueError(f"真实年报不存在，请用 --annual-report 选择原报告：{source}")
        actual_sha = sha256_file(source)
        if actual_sha != expected_sha:
            raise ValueError("真实年报 SHA-256 与冻结真值来源不匹配")
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
        document = pymupdf.open(source)
    evidence_map = {}
    evidence_catalog = {}
    facts = []
    try:
        for item in data["real_facts"]:
            record = dict(item)
            if document is not None:
                page = document[record["page"] - 1]
                boxes = page.search_for(record["search_text"])
                if len(boxes) != 1:
                    raise ValueError(f"真值数值未能唯一定位到PDF单元格：{record['fact_id']}")
                box = boxes[0]
                text = page.get_textbox(box).strip()
                numeric = Decimal(text.replace(",", "").replace("，", "").replace("−", "-"))
                if numeric != Decimal(record["value"]):
                    raise ValueError(f"PDF单元格与冻结真值不一致：{record['fact_id']}")
                evidence_id = make_evidence_id(expected_sha, record["page"], text, tuple(box), record["row"], record["col"])
                evidence_catalog[evidence_id] = {
                    "document_sha256": expected_sha, "document": str(source.resolve()),
                    "page": record["page"], "bbox": list(box), "text": text,
                    "row": record["row"], "col": record["col"],
                    "table_label": record["table_label"], "column_label": record["column_label"],
                    "cell_verified_against_pdf": True,
                }
            else:
                # Unit-test/diagnostic mode makes the lack of source checking
                # visible; these placeholders never count as PDF evidence hits.
                evidence_id = f"unverified-gold:{record['fact_id']}"
            evidence_map[record["fact_id"]] = evidence_id
            record.update(
                source_doc=expected_sha, source_path=str(source.resolve()),
                source_type="pdf_table" if document is not None else "unverified_gold",
                publish_date=description["publish_date"], evidence=[evidence_id], decimals=2,
            )
            facts.append(fact_from_dict(record))
        source_verified = document is not None
    finally:
        if document is not None:
            document.close()
    audit = {"path": str(source.resolve()), "sha256": expected_sha, "source_verified": source_verified, "facts_checked": len(facts) if source_verified else 0, "company_count": 1, "report_count": 1, "page_count_checked": 1 if source_verified else 0}
    return facts, evidence_map, evidence_catalog, audit


def _claim(case: dict, evidence_map: dict) -> AtomicClaim:
    data = dict(case["claim"])
    data.setdefault("claim_id", case["id"])
    data.setdefault("start", 0)
    data.setdefault("end", len(data["text"]))
    data["caliber_hint"] = tuple(data.get("caliber_hint", ()))
    source = data.get("declared_source")
    if source and source.startswith("evidence:"):
        reference = source[len("evidence:"):]
        data["declared_source"] = "evidence:" + evidence_map.get(reference, reference)
    allowed = {field.name for field in fields(AtomicClaim)}
    unsupported = set(data) - allowed
    if unsupported:
        raise ValueError(f"unsupported claim fields: {sorted(unsupported)}")
    return AtomicClaim(**data)


def _repair(claim: AtomicClaim, result: dict, judge: Judge) -> dict[str, Any] | None:
    if result["status"] != "violated":
        return None
    correction = result.get("correction") or {}
    value = correction.get("correct_value")
    structured = None
    if value is not None and result.get("error_type") not in {"E2", "E7"}:
        try:
            parsed, unit = parse_number_claim(str(value))
            if parsed is not None:
                from mingjing.judge import _source_numeric_position
                position, valid = _source_numeric_position(claim)
                if not valid or position is None or not claim.numeric_text:
                    raise ValueError('Cannot repair a structured value without an unambiguous source occurrence')
                repaired_text = claim.text[:position] + str(value) + claim.text[position+len(claim.numeric_text):]
                fixed = replace(claim, text=repaired_text, candidate_value=str(parsed), unit=unit or "元",
                                numeric_text=str(value), source_numeric_start=position, declared_source=None)
                structured = judge.verify(fixed).to_dict()
        except (ValueError, TypeError, ArithmeticError):
            structured = None
    replacement = str(correction.get("replacement_text") or "")
    repaired_claims = extract_mentions(replacement, company=claim.company, fallback_period=claim.period) if replacement else []
    # Text repair must preserve the intended account; silently changing subject
    # would not be a successful financial correction.
    relevant = [item for item in repaired_claims if item.account == claim.account]
    text_results = [judge.verify(item).to_dict() for item in relevant]
    text_pass = bool(text_results) and all(item["status"] == "verified" for item in text_results)
    if claim.relation in {"gt", "lt", "ge", "le"}:
        text_pass = text_pass and any(item.relation in {"gt", "lt", "ge", "le"} for item in relevant)
    fields_required = ("correct_value", "source", "calculation", "basis", "replacement_text")
    five_fields_present = all(str(correction.get(key) or "").strip() for key in fields_required)
    source_cites_evidence = any(evidence in str(correction.get("source", "")) for evidence in result.get("evidence", []))
    qualified = five_fields_present and source_cites_evidence and text_pass
    return {"correction": correction, "structured_result": structured, "structured_pass": structured["status"] == "verified" if structured is not None else None, "text_claims": [item.to_dict() for item in relevant], "text_results": text_results, "text_pass": text_pass, "five_fields_present": five_fields_present, "source_cites_evidence": source_cites_evidence, "qualified": qualified}


def run_acceptance(cases_path: str | Path = DEFAULT_CASES, out: str | Path | None = None, *, annual_report: str | Path | None = None, verify_source: bool = True) -> dict[str, Any]:
    started = time.perf_counter()
    data = load_cases(cases_path)
    real, mapping, evidence_catalog, source_audit = _real_facts(data, Path(annual_report) if annual_report else None, verify_source)
    groups = {key: [fact_from_dict(item) for item in value] for key, value in data["fixture_facts"].items()}
    groups["real"] = real
    for facts in groups.values():
        for fact in facts:
            for evidence in fact.evidence:
                mapping.setdefault(evidence, evidence)
    records = []
    snapshots = []
    repairs = []
    audit = None
    destination = Path(out) if out else None
    run_id = "acceptance-" + digest(Path(cases_path).read_bytes())[:12]
    if destination:
        destination.mkdir(parents=True, exist_ok=True)
        # A benchmark invocation starts a new log instead of concatenating
        # independently initialized audit hash chains into one file.
        for name in ("audit.jsonl", "session.jsonl"):
            target = destination / name
            if target.is_file():
                target.write_text("", encoding="utf-8")
        audit = AuditLog(destination, run_id)
        audit.emit("source_verification", input=source_audit, output={"evidence_cells": len(evidence_catalog)})
    for case in data["cases"]:
        facts = groups[case["facts"]]
        claim = _claim(case, mapping)
        judge = Judge(FactStore(facts), case.get("report_date", "2024-04-22"))
        result = judge.verify(claim).to_dict()
        expected_error = None if case["error_type"] == "clean" else case["error_type"]
        strict_match = result["status"] == case["gold"] and result.get("error_type") == expected_error
        expected_evidence = [mapping.get(reference, reference) for reference in case.get("expected_evidence", [])]
        evidence_hit = set(expected_evidence).issubset(result.get("evidence", [])) if expected_evidence else None
        real_expected = [item for item in expected_evidence if item in evidence_catalog]
        pdf_hit = bool(evidence_hit) if real_expected and len(real_expected) == len(expected_evidence) else None
        repair = _repair(claim, result, judge)
        if repair is not None:
            repairs.append({"id": case["id"], **repair})
        record = {
            "id": case["id"], "set": case["set"], "variant": case.get("variant"),
            "source": "real_pdf" if case["facts"] == "real" else "synthetic_fixture",
            "gold": case["gold"], "prediction": result["status"], "error_type": case["error_type"],
            "predicted_error_type": result.get("error_type"), "strict_match": strict_match,
            "is_trap": case.get("is_trap", False), "evidence_hit": evidence_hit,
            "evidence_page_hit": pdf_hit, "evidence_table_hit": pdf_hit, "evidence_cell_hit": pdf_hit,
            "repair_pass": repair["text_pass"] if repair is not None else None,
            "modification_qualified": repair["qualified"] if repair is not None else None,
            "result": result,
        }
        records.append(record)
        snapshots.append({"id": case["id"], "claim": claim.to_dict(), "facts": [fact.to_dict() for fact in facts], "report_date": case.get("report_date", "2024-04-22")})
        if audit:
            audit.emit("benchmark_judgement", input=snapshots[-1], output=record)
            if repair is not None:
                audit.emit("benchmark_repair_recompute", input=repair["text_claims"], output=repair["text_results"])
    metrics = evaluate_records(records)
    # Binary error-detection scores alone would hide an E4 mislabeled E1 and
    # legitimate abstention. Exact tri-state/type checks are separate gates.
    strict_groups = defaultdict(list)
    for record in records:
        strict_groups[record["error_type"]].append(record)
    strict_by_type = {key: rate_with_interval(sum(item["strict_match"] for item in group), len(group)) for key, group in sorted(strict_groups.items())}
    failure_ids = [item["id"] for item in records if not item["strict_match"]]
    predictions = Counter(item["prediction"] for item in records)
    pdf_records = [item for item in records if item["evidence_page_hit"] is not None]
    qualified = [item for item in records if item["modification_qualified"] is not None]
    numeric_repairs = [item for item in repairs if item["structured_pass"] is not None]
    gold_errors = [item for item in records if item["gold"] == "violated"]
    repairs_qualified = all(item["qualified"] for item in repairs)
    evidence_complete = all(item["evidence_hit"] is not False for item in records)
    metrics.update({
        "scope": data["scope"], "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
        "source_verification": source_audit, "case_file_sha256": sha256_file(cases_path), "kernel_fingerprint": kernel_fingerprint(),
        "strict_match": rate_with_interval(len(records) - len(failure_ids), len(records)), "strict_by_error_type": strict_by_type,
        "scoped_kernel_acceptance": "passed" if not failure_ids and source_audit["source_verified"] and repairs_qualified and evidence_complete else "failed",
        "scoped_gates": {"exact_status_and_error_type": not failure_ids, "original_pdf_source_verified": source_audit["source_verified"], "all_required_evidence_hit": evidence_complete, "all_generated_repairs_recomputed_and_qualified": repairs_qualified},
        "full_crrv_bench_acceptance": "not_completed", "failed_case_ids": failure_ids,
        "prediction_counts": dict(predictions), "set_counts": dict(Counter(item["set"] for item in records)),
        "error_coverage": rate_with_interval(sum(item["prediction"] != "abstained" for item in gold_errors), len(gold_errors)),
        "correct_error_type_detection": rate_with_interval(sum(item["strict_match"] for item in gold_errors), len(gold_errors)),
        "false_accepts": sum(item["gold"] == "violated" and item["prediction"] == "verified" for item in records),
        "three_state_accuracy": rate_with_interval(sum(item["gold"] == item["prediction"] for item in records), len(records)),
        "pdf_evidence": {level: rate_with_interval(sum(bool(item[f"evidence_{level}_hit"]) for item in pdf_records), len(pdf_records)) for level in ("page", "table", "cell")},
        "modification_qualification": rate_with_interval(sum(bool(item["modification_qualified"]) for item in qualified), len(qualified)),
        "structured_numeric_repair": rate_with_interval(sum(item["structured_pass"] for item in numeric_repairs), len(numeric_repairs)),
        "text_repair": rate_with_interval(sum(item["text_pass"] for item in repairs), len(repairs)),
        "runtime_ms": round((time.perf_counter() - started) * 1000), "model_requests": 0, "model_cost": 0,
        "unmet_requirements": data["unmet_requirements"],
    })
    if destination:
        for name, value in (("metrics.json", metrics), ("kernel_inputs.json", snapshots), ("evidence_catalog.json", evidence_catalog), ("repairs.json", repairs)):
            (destination / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        (destination / "predictions.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8")
        if audit:
            audit.emit("benchmark_metrics_registered", input={"predictions_sha256": sha256_file(destination / "predictions.jsonl"), "case_file_sha256": metrics["case_file_sha256"], "evaluator_sha256": sha256_file(Path(__file__))}, output={"metrics_sha256": sha256_file(destination / "metrics.json"), "strict_match": metrics["strict_match"], "text_repair": metrics["text_repair"], "scoped_kernel_acceptance": metrics["scoped_kernel_acceptance"]})
            audit.session("acceptance_complete", output={"strict_match": metrics["strict_match"], "failed_case_ids": failure_ids, "scope": data["scope"]})
        lines = ["# 明镜固定小样本内核验收", "", f"范围：{data['scope']}", "", f"真实来源验证：{source_audit['source_verified']}；固定样本 {len(records)} 条；状态和错误类型均匹配 {len(records) - len(failure_ids)} 条。", f"范围内判定：{metrics['scoped_kernel_acceptance']}。完整 CRRV-Bench：未完成。", "", "## 未通过样本", ""]
        lines.extend(f"- {item['id']}：预期 {item['gold']}/{item['error_type']}，实际 {item['prediction']}/{item['predicted_error_type']}" for item in records if not item["strict_match"])
        if not failure_ids:
            lines.append("无判定失败。")
        lines.extend(["", "## 仍未完成的要求", ""])
        lines.extend(f"- {item['set']}：{item['requirement']}。{item['detail']}" for item in data["unmet_requirements"])
        lines.extend(["", "所有本报告数字来自本次 Judge 运行及 bench.evaluate；样本文件没有预填 prediction。修复指标仅使用内核生成的修改建议，重新抽取和判定。"])
        (destination / "acceptance_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run source-checked MingJing kernel acceptance cases")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--out", default=str(PACKAGE_ROOT / "runs" / "acceptance-bench"))
    parser.add_argument("--annual-report")
    parser.add_argument("--skip-source-verification", action="store_true", help="Diagnostic only; cannot pass source acceptance")
    args = parser.parse_args(argv)
    report = run_acceptance(args.cases, args.out, annual_report=args.annual_report, verify_source=not args.skip_source_verification)
    print(json.dumps({key: report[key] for key in ("run_id", "total", "strict_match", "scoped_kernel_acceptance", "full_crrv_bench_acceptance", "failed_case_ids", "runtime_ms")}, ensure_ascii=False))
    return 0 if report["scoped_kernel_acceptance"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
