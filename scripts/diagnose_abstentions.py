"""Produce a read-only, occurrence-level diagnosis for a frozen run.

This tool deliberately does not alter claims, facts, results, or any stable
run.  It separates the policy denominator from candidates that are actually
executable by the deterministic kernel, and flags only *suspected* false
abstentions for manual review.  A matching amount alone is never sufficient.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mingjing.coverage_stats import PRIMARY_SCOPE, coverage_statistics
from mingjing.metrics import metric_spec


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _scope(entry: dict, claim: dict | None) -> str:
    ctx = (claim or {}).get("table_context") or {}
    return entry.get("evaluation_scope") or ctx.get("evaluation_scope") or PRIMARY_SCOPE


def _first_blocker(row: dict) -> dict:
    blockers = row.get("blockers") or []
    return blockers[0] if blockers else {
        "code": row.get("abstention_code"),
        "kind": "primary",
        "reason": row.get("reason"),
    }


def _fact_matches(claim: dict, facts: list[dict]) -> list[dict]:
    """Find exact, source-compatible facts without guessing missing dimensions."""
    dims = claim.get("source_dimensions") or (claim.get("table_context") or {}).get("source_dimensions") or {}
    company, account, period = claim.get("company"), claim.get("account"), claim.get("period")
    if not company or not account or account == "UNKNOWN" or not period:
        return []
    scope = dims.get("scope") or dims.get("scope_header")
    nr = "扣非" if account == "NP_PARENT_ADJ" or "扣非" in (claim.get("caliber_hint") or []) else "未扣非"
    dimension_kind = dims.get("dimension_kind", "total")
    dimension_id = dims.get("dimension_id", "")
    currency = dims.get("currency", "CNY")
    definition_id = dims.get("definition_id", "")
    out = []
    for fact in facts:
        if (fact.get("company"), fact.get("account"), fact.get("period")) != (company, account, period):
            continue
        if scope and fact.get("scope") != scope:
            continue
        if fact.get("nr") != nr:
            continue
        if (fact.get("dimension_kind", "total"), fact.get("dimension_id", ""),
                fact.get("currency", "CNY"), fact.get("definition_id", "")) != (
                    dimension_kind, dimension_id, currency, definition_id):
            continue
        out.append(fact)
    return out


def diagnose(run_root: Path) -> dict:
    payload = _json(run_root / "results.json")
    claims = payload.get("claims") or _json(run_root / "claims.json")
    facts = payload.get("facts") or _json(run_root / "facts.json")
    coverage = payload.get("coverage") or _json(run_root / "coverage.json")
    results = payload.get("results") or _json(run_root / "results.json").get("results", [])
    stats = coverage_statistics(coverage, claims, results)
    claim_map = {row["claim_id"]: row for row in claims}
    mention_map = {row.get("claim_id"): row for row in coverage.get("mentions") or [] if row.get("claim_id")}

    rows = []
    suspected = []
    for result in results:
        claim = claim_map.get(result.get("claim_id"))
        if not claim or result.get("status") != "abstained":
            continue
        mention = mention_map.get(result.get("claim_id"), {})
        scope = _scope(mention, claim)
        dims = result.get("source_dimensions") or claim.get("source_dimensions") or (claim.get("table_context") or {}).get("source_dimensions") or {}
        explicit = dims.get("explicit") or {}
        account = claim.get("account")
        definition_id = dims.get("definition_id") or ""
        registered_metric = metric_spec(account, definition_id) is not None
        matches = _fact_matches(claim, facts)
        first = _first_blocker(result)
        item = {
            "claim_id": result.get("claim_id"),
            "occurrence": {"start": claim.get("start"), "end": claim.get("end"), "text": claim.get("text")},
            "status": result.get("status"),
            "abstention_code": result.get("abstention_code"),
            "reason": result.get("reason"),
            "first_blocker": first,
            "all_blockers": result.get("blockers") or [],
            "evaluation_scope": scope,
            "dimensions": {
                "company": claim.get("company"), "account": account, "period": claim.get("period"),
                "scope": dims.get("scope") or dims.get("scope_header"),
                "unit": claim.get("unit"), "definition_id": definition_id,
                "dimension_kind": dims.get("dimension_kind"), "dimension_id": dims.get("dimension_id"),
                "currency": dims.get("currency"), "explicit": explicit,
            },
            "dimension_completeness": {
                key: bool(dims.get(key) or explicit.get(key))
                for key in ("company", "account", "period", "scope", "unit", "definition_id")
            },
            "registered_metric": registered_metric,
            "exact_fact_matches": [
                {"fact_id": f.get("fact_id"), "value": f.get("value"), "scope": f.get("scope"),
                 "publish_date": f.get("publish_date"), "evidence": f.get("evidence", [])[:3]}
                for f in matches
            ],
            "match_count": len(matches),
        }
        rows.append(item)

        # This is deliberately conservative: an item is only suspected when
        # every source dimension is explicit, the metric is registered (or is
        # a direct fact), and exactly one same-dimension fact exists.  It is
        # still a review candidate; it never changes the frozen verdict.
        complete = all(item["dimension_completeness"].values())
        direct = account not in {"UNKNOWN", ""} and account not in {
            "NET_MARGIN_UNSPECIFIED", "ROE_UNSPECIFIED", "EPS_BROKER_DILUTED_UNSPECIFIED",
            "EXP_PERIOD_RATIO_UNSPECIFIED", "INVESTMENTS_UNSPECIFIED", "ASSET_CASH_UNSPECIFIED",
            "EBITDA_UNSPECIFIED", "EPS_UNSPECIFIED", "ROIC_UNSPECIFIED", "NET_DEBT_RATIO_UNSPECIFIED",
        }
        if scope == PRIMARY_SCOPE and complete and direct and (registered_metric or account not in {
                "REVENUE_SHARE", "GROSS_MARGIN", "CURRENT_RATIO", "QUICK_RATIO", "ADMIN_EXPENSE_RATIO",
                "SELLING_EXPENSE_RATIO", "RD_EXPENSE_RATIO", "FINANCE_EXPENSE_RATIO", "PERIOD_EXPENSE_RATIO",
                "ASSET_TURNOVER", "INVENTORY_TURNOVER", "AR_TURNOVER", "NET_MARGIN", "DEBT_RATIO",
                "ROE", "CASH_CONTENT"}) and len(matches) == 1:
            suspected.append(item["claim_id"])

    by_scope = {}
    for scope, group in stats.get("groups", {}).items():
        by_scope[scope] = group
    executable = [row for row in rows if row["evaluation_scope"] == PRIMARY_SCOPE and all(row["dimension_completeness"].values())
                  and (row["registered_metric"] or row["dimensions"]["account"] not in {"UNKNOWN", ""})]
    counts = Counter((row["evaluation_scope"], row["abstention_code"],
                     (row["first_blocker"].get("code"), row["first_blocker"].get("kind"))) for row in rows)
    return {
        "schema_version": "abstention-stat-diagnosis.v1",
        "run_id": payload.get("run_id"),
        "run_root": str(run_root.resolve()),
        "frozen_input": True,
        "statistics_recomputed": stats,
        "statistics_recorded": payload.get("coverage_statistics"),
        "statistics_match": stats == payload.get("coverage_statistics"),
        "policies": {
            "policy_primary": "all issuer_historical_financial occurrences retained by the ledger",
            "executable_financial": "issuer historical financial occurrences with complete source dimensions and a registered/direct fact path",
            "all_occurrences": "all coverage ledger mentions, including non-financial and excluded scopes",
        },
        "counts": {
            "abstention_rows": len(rows),
            "executable_financial_rows": len(executable),
            "suspected_false_abstention_rows": len(suspected),
            "by_scope_code_first_blocker": {"|".join(map(str, key)): value for key, value in counts.items()},
        },
        "suspected_false_abstention_ids": suspected,
        "rows": rows,
        "notes": [
            "Blockers overlap and are not additive.",
            "A matching amount without period, scope, version, or source evidence is not a false abstention.",
            "This diagnosis does not alter the frozen run or claim/result status.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=Path("runs/source-real-stable-16-full"))
    parser.add_argument("--out", type=Path, default=Path("runs/abstention-stat-diagnosis-17/diagnosis.json"))
    args = parser.parse_args()
    result = diagnose(args.run)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run_id": result["run_id"], "statistics_match": result["statistics_match"], "counts": result["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
