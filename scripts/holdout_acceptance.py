from __future__ import annotations

"""Seal a five-company PDF holdout, then evaluate its manually frozen gold.

This script cannot derive gold facts with the production parser. Gold cells
must be reviewed from original PDFs and explicitly recorded in D.jsonl.
No kernel parameter is tuned by this script.
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.evaluate import evaluate_records
from bench.stats import rate_with_interval
from mingjing.audit import digest, kernel_fingerprint
from mingjing.evidence import make_evidence_id, sha256_file


def source_fingerprint():
    # The executable rules live in these modules. Rulepack cards are display
    # metadata in the current implementation and are separately recorded.
    return {**{key: value for key, value in kernel_fingerprint().items() if key.startswith("module:")}, **{f"pipeline:{name}": sha256_file(ROOT / "mingjing" / f"{name}.py") for name in ("parser", "mentions")}}


def seal_holdout(corpus: Path, seal: Path, selection_offset: int = 0):
    if seal.exists():
        raise ValueError("保留集已经封存，禁止覆盖原始封存时间或源码哈希")
    if selection_offset < 0:
        raise ValueError("selection offset must be nonnegative")
    candidates = sorted(path for path in corpus.rglob("*2023*年度报告*.pdf") if "920493" not in str(path) and path.name.startswith("2024"))[selection_offset:selection_offset + 5]
    if len(candidates) != 5:
        raise ValueError("不足5家独立2023年年报")
    rows = []
    for path in candidates:
        entity = next(part for part in path.parts if part.partition("_")[0].isdigit())
        rows.append({"ticker": entity.partition("_")[0], "company": entity.partition("_")[2], "path": str(path.resolve()), "relative_path": path.relative_to(corpus).as_posix(), "sha256": sha256_file(path), "report_period": "2023Y", "publish_date": path.name[:10]})
    data = {"schema_version": "mingjing.holdout.seal.v1", "sealed_at": datetime.now(timezone.utc).isoformat(), "selection": f"Five paths at offset {selection_offset} in lexicographic order among 2024 disclosures of 2023 annual reports, excluding the development company 920493", "selection_offset": selection_offset, "historical_cutoff": "2024-06-30", "source_fingerprint": source_fingerprint(), "rulepack_metadata_at_seal": {key:value for key,value in kernel_fingerprint().items() if key.startswith("rule:")}, "reports": rows, "gold_generation": "Manual original-PDF table review; production parser output is prohibited as gold", "review_status": "awaiting_manual_gold", "sample_scope": "Five annual reports, three financial accounts and two periods each; small holdout, not full CRRV-Bench"}
    seal.parent.mkdir(parents=True, exist_ok=True)
    seal.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def inspect_sources(seal: Path, out: Path):
    import pymupdf
    data = json.loads(seal.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    for report in data["reports"]:
        path = Path(report["path"])
        if sha256_file(path) != report["sha256"]:
            raise ValueError("PDF在封存后发生变化")
        doc = pymupdf.open(path)
        excerpts = []
        # These are direct PDF text pages for manual review, not parser facts.
        for index in range(min(25, len(doc))):
            text = doc[index].get_text()
            if "营业收入" in text and ("归属于" in text or "经营活动" in text):
                excerpts.append({"page": index + 1, "text": text})
        doc.close()
        (out / f"{report['ticker']}_source_pages.json").write_text(json.dumps(excerpts, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"reports": len(data["reports"]), "out": str(out)}


def _verify_gold(seal_data: dict, gold: list[dict]):
    import pymupdf
    reports = {item["ticker"]: item for item in seal_data["reports"]}
    documents = {}
    try:
        for row in gold:
            if "prediction" in row or row.get("gold_source") != "manual_original_pdf":
                raise ValueError("D真值必须来自原PDF人工审查，不能预填预测")
            report = reports[row["ticker"]]
            if report["sha256"] != row["document_sha256"]:
                raise ValueError("D真值报告哈希与封存不一致")
            if row["ticker"] not in documents:
                path = Path(report["path"])
                if sha256_file(path) != report["sha256"]:
                    raise ValueError("封存PDF发生变化")
                documents[row["ticker"]] = pymupdf.open(path)
            page = documents[row["ticker"]][row["page"] - 1]
            boxes = page.search_for(row["search_text"])
            matching = [box for box in boxes if all(abs(a-b) < 1 for a, b in zip(box, row["bbox"]))]
            if len(matching) != 1:
                raise ValueError("真值单元格坐标不能唯一反查")
            text = page.get_textbox(matching[0]).strip()
            value = Decimal(text.replace(",", "").replace("，", "").replace("−", "-")) * Decimal(row.get("unit_scale", "1"))
            if value != Decimal(row["value"]):
                raise ValueError("原PDF单元格与D真值不一致")
            evidence = make_evidence_id(row["document_sha256"], row["page"], text, row["bbox"])
            if evidence != row["evidence_id"]:
                raise ValueError("D证据ID不一致")
    finally:
        for doc in documents.values():
            doc.close()


def evaluate_holdout(seal: Path, gold_path: Path, out: Path):
    from mingjing.facts import FactStore
    from mingjing.judge import Judge
    from mingjing.mentions import extract_mentions
    from mingjing.parser import load_or_extract_facts

    if (out / "holdout_metrics.json").exists():
        raise ValueError("既有保留集验收结果已冻结，禁止覆盖；诊断复算请指定新输出目录")
    started = time.perf_counter()
    seal_data = json.loads(seal.read_text(encoding="utf-8"))
    gold = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    _verify_gold(seal_data, gold)
    code_changed = seal_data["source_fingerprint"] != source_fingerprint()
    by_ticker = {}
    for row in gold:
        by_ticker.setdefault(row["ticker"], []).append(row)
    records = []
    parser_rows = []
    for report in seal_data["reports"]:
        import pymupdf
        # Independently verify production evidence against original PDF blocks.
        # A correct corroborating page need not be the manual gold-table page.
        pdf = pymupdf.open(report["path"])
        original_evidence = {}
        for page_number, page in enumerate(pdf, 1):
            for block in page.get_text("blocks"):
                snippet = block[4].strip()
                original_evidence[make_evidence_id(report["sha256"], page_number, snippet, block[:4])] = snippet
        pdf.close()
        # Facts used by Judge come from the unchanged production parser, never
        # from the gold rows above.
        facts = load_or_extract_facts(report["path"], company=report["company"], period="2023Y", publish_date=report["publish_date"])
        store = FactStore(facts)
        judge = Judge(store, seal_data["historical_cutoff"])
        parser_rows.extend(fact.to_dict() for fact in facts)
        for row in by_ticker[report["ticker"]]:
            base_text = f"{report['company']}{row['period'][:4]}年{row['label']}{row['value']}元。"
            probes = [("exact", base_text, "verified", "clean")]
            if row["period"] == "2023Y":
                changed = Decimal(row["value"]) + max(abs(Decimal(row["value"])) * Decimal("0.01"), Decimal("10000"))
                probes.append(("injected_E1", f"{report['company']}2023年{row['label']}{changed}元。", "violated", "E1"))
            for variant, text, expected_status, error in probes:
                claims = extract_mentions(text, company=report["company"], fallback_period=row["period"])
                relevant = [claim for claim in claims if claim.account == row["account"] and claim.period == row["period"] and claim.relation == "value"]
                results = [judge.verify(claim).to_dict() for claim in relevant]
                expected_error = None if error == "clean" else error
                strict = len(results) == 1 and results[0]["status"] == expected_status and results[0].get("error_type") == expected_error
                result = results[0] if len(results) == 1 else {"status": "abstained", "error_type": "E8", "reason": "未能抽取唯一匹配原子陈述", "evidence": []}
                # Page evidence is checked by document hash prefix and page;
                # parser uses row boxes, while gold freezes exact cell boxes.
                prefix = f"{row['document_sha256'][:12]}:p{row['page']}:"
                page_hit = any(item.startswith(prefix) for item in result.get("evidence", []))
                result_evidence = result.get("evidence", [])
                evidence_valid = bool(result_evidence) and all(item in original_evidence for item in result_evidence)
                snippet = "\n".join(original_evidence.get(item, "") for item in result_evidence)
                target = Decimal(row["value"]) / Decimal(row.get("unit_scale", "1"))
                numeric_tokens = re.findall(r"[-−]?\d[\d,，]*(?:\.\d+)?", snippet)
                evidence_contains_gold_value = any(Decimal(token.replace(",", "").replace("，", "").replace("−", "-")) == target for token in numeric_tokens)
                evidence_hit = evidence_valid and evidence_contains_gold_value
                selected = store.query(report["company"], row["account"], row["period"], cutoff=seal_data["historical_cutoff"])
                fact_exact = selected is not None and selected.value == Decimal(row["value"])
                record = {"id": row["id"] + "-" + variant, "ticker": report["ticker"], "company": report["company"], "set": "D", "variant": variant, "gold": expected_status, "error_type": error, "prediction": result["status"], "predicted_error_type": result.get("error_type"), "strict_match": strict, "production_fact_equals_gold": fact_exact, "evidence_hit": evidence_hit, "gold_page_hit": page_hit, "evidence_original_blocks_verified": evidence_valid, "evidence_contains_gold_value": evidence_contains_gold_value, "gold_evidence_id": row["evidence_id"], "draft_text": text, "claims": [claim.to_dict() for claim in relevant], "result": result}
                records.append(record)
    metrics = evaluate_records(records)
    failures = [row["id"] for row in records if not row["strict_match"] or not row["production_fact_equals_gold"] or not row["evidence_hit"]]
    cell_records = [row for row in records if row["variant"] == "exact"]
    metrics.update({"scope": seal_data["sample_scope"], "source": "Actual unchanged production parser + mentions + Judge", "gold_sha256": sha256_file(gold_path), "seal_sha256": sha256_file(seal), "sealed_at": seal_data["sealed_at"], "historical_cutoff": seal_data["historical_cutoff"], "code_changed_since_seal": code_changed, "evaluated_source_fingerprint": source_fingerprint(), "evaluator_sha256": sha256_file(Path(__file__)), "rule_metadata_changed_since_seal": seal_data.get("rulepack_metadata_at_seal", {}) != {key:value for key,value in kernel_fingerprint().items() if key.startswith("rule:")}, "report_count": len(seal_data["reports"]), "company_count": len(by_ticker), "gold_cell_count": len(gold), "strict_accuracy": rate_with_interval(sum(row["strict_match"] for row in records), len(records)), "fact_exact_accuracy": rate_with_interval(sum(row["production_fact_equals_gold"] for row in records), len(records)), "fact_exact_cell_accuracy": rate_with_interval(sum(row["production_fact_equals_gold"] for row in cell_records), len(cell_records)), "prediction_counts": dict(Counter(row["prediction"] for row in records)), "status": "passed_small_holdout" if not failures and not code_changed else "failed_small_holdout", "failed_case_ids": failures, "runtime_ms": round((time.perf_counter()-started)*1000), "model_requests": 0, "full_crrv_bench_acceptance": "not_completed", "limitations": ["五家公司各一份2023年年报、三个科目、两个期间，不代表全量保留集", "仅金额和E1注入，未测保留公司上的E2-E9或真实研报语义召回", "人工单次原PDF审查，未进行第二位独立审阅者复核", "披露日期历史截断+源码封存，不等同于预注册长期时间外推实验"]})
    out.mkdir(parents=True, exist_ok=True)
    (out / "holdout_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "production_parser_facts.json").write_text(json.dumps(parser_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "holdout_predictions.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in records), encoding="utf-8")
    (out / "measurement_audit.json").write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "seal_sha256": metrics["seal_sha256"], "gold_sha256": metrics["gold_sha256"], "evaluator_sha256": metrics["evaluator_sha256"], "outputs": {name: sha256_file(out / name) for name in ("holdout_metrics.json", "production_parser_facts.json", "holdout_predictions.jsonl")}, "metrics_source": "bench.evaluate + bench.stats Wilson intervals computed from actual production outputs", "status": metrics["status"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 五家公司小规模封存保留集验收", "", f"状态：{metrics['status']}。封存后源码变化：{code_changed}。", f"公司数：{len(by_ticker)}；原报告数：{len(seal_data['reports'])}；原PDF人工审查金额单元格：{len(gold)}；执行案例：{len(records)}。", f"严格状态/错误类型匹配：{sum(row['strict_match'] for row in records)}/{len(records)}；生产事实精确匹配：{sum(row['production_fact_equals_gold'] for row in records)}/{len(records)}。", "", "## 失败案例", ""]
    lines.extend("- " + item for item in failures)
    lines.extend(["", "## 范围限制", ""])
    lines.extend("- " + item for item in metrics["limitations"])
    (out / "holdout_report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    return metrics


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seal", "inspect", "evaluate"))
    parser.add_argument("--corpus", default=str(ROOT.parent / "年报和半年报" / "reports"))
    parser.add_argument("--seal", default=str(ROOT / "bench" / "holdout_seal_manifest.json"))
    parser.add_argument("--gold", default=str(ROOT / "bench" / "D.jsonl"))
    parser.add_argument("--out", default=str(ROOT / "runs" / "holdout-acceptance"))
    parser.add_argument("--selection-offset", type=int, default=0)
    args = parser.parse_args(argv)
    if args.action == "seal":
        result = seal_holdout(Path(args.corpus), Path(args.seal), args.selection_offset)
    elif args.action == "inspect":
        result = inspect_sources(Path(args.seal), Path(args.out) / "manual_review")
    else:
        result = evaluate_holdout(Path(args.seal), Path(args.gold), Path(args.out))
    reports_value = result.get("reports", [])
    print(json.dumps({key: result[key] for key in ("status", "company_count", "gold_cell_count", "strict_accuracy", "code_changed_since_seal", "failed_case_ids") if key in result} or {"out": args.out, "sealed_reports": len(reports_value) if isinstance(reports_value, list) else reports_value}, ensure_ascii=False))
    return 1 if result.get("status") == "failed_small_holdout" else 0


if __name__ == "__main__":
    raise SystemExit(main())
