#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Analyse the enlarged OFF/ON baselines for the parallel-tech broker report.

Reads ``runs/full-off/results.json`` and ``runs/full-on/results.json`` and
prints:

1. overall totals (candidates / verified / violated / abstained / rate)
2. abstention-code distribution (X1..X6)
3. verdict zero-change check between OFF and ON on the triple
   ``(status, abstention_code, error_type)``
4. probe-status distribution in the ON run (found / missing / not_applicable)
5. salvageable claims (probe_status == "found")

Usage::

    py -3.12 scripts/analyze_full_baseline.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Tuple

PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFF_PATH: str = os.path.join(PROJECT_ROOT, "runs", "full-off", "results.json")
ON_PATH: str = os.path.join(PROJECT_ROOT, "runs", "full-on", "results.json")


def load_results(path: str) -> List[Dict[str, Any]]:
    """Load the ``results`` list from a run's results.json."""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return list(payload["results"])


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute headline statistics for a run."""
    total = len(results)
    status = Counter(x.get("status") for x in results)
    codes = Counter(x.get("abstention_code") for x in results)
    abstained = status.get("abstained", 0)
    rate = (abstained / total) if total else 0.0
    return {
        "total": total,
        "status": status,
        "codes": codes,
        "abstained": abstained,
        "abstention_rate": rate,
    }


def verdict_triple(x: Dict[str, Any]) -> Tuple[Any, Any, Any]:
    """The verdict triple that must not change between OFF and ON."""
    return (x.get("status"), x.get("abstention_code"), x.get("error_type"))


def probe_status_of(x: Dict[str, Any]) -> str:
    """Extract probe_status from the diagnostic_probe blocker, else ''."""
    for blocker in x.get("blockers", []) or []:
        if blocker.get("kind") == "diagnostic_probe":
            return blocker.get("probe_status", "") or ""
    # Some runs expose it at top level.
    return x.get("probe_status", "") or ""


def main() -> int:
    off = load_results(OFF_PATH)
    on = load_results(ON_PATH)

    off_sum = summarize(off)
    on_sum = summarize(on)

    print("===== OFF baseline =====")
    print("total=%d status=%s abstained=%d rate=%.4f"
          % (off_sum["total"], dict(off_sum["status"]),
             off_sum["abstained"], off_sum["abstention_rate"]))
    print("codes=%s" % dict(off_sum["codes"]))

    print("===== ON baseline =====")
    print("total=%d status=%s abstained=%d rate=%.4f"
          % (on_sum["total"], dict(on_sum["status"]),
             on_sum["abstained"], on_sum["abstention_rate"]))
    print("codes=%s" % dict(on_sum["codes"]))

    # --- verdict zero-change check -------------------------------------
    off_by_id = {x.get("claim_id"): x for x in off}
    on_by_id = {x.get("claim_id"): x for x in on}
    diffs: List[Tuple[str, Tuple, Tuple]] = []
    for claim_id, off_x in off_by_id.items():
        on_x = on_by_id.get(claim_id)
        if on_x is None:
            diffs.append((claim_id, verdict_triple(off_x), ("MISSING",)))
            continue
        t_off = verdict_triple(off_x)
        t_on = verdict_triple(on_x)
        if t_off != t_on:
            diffs.append((claim_id, t_off, t_on))
    print("===== verdict zero-change (OFF vs ON) =====")
    print("compared=%d differences=%d" % (len(off_by_id), len(diffs)))
    for claim_id, t_off, t_on in diffs[:20]:
        print("  DIFF %s  OFF=%s  ON=%s" % (claim_id, t_off, t_on))
    print("ZERO_CHANGE=%s" % ("YES" if not diffs else "NO"))

    # --- probe distribution --------------------------------------------
    probe_dist = Counter(probe_status_of(x) for x in on)
    print("===== probe distribution (ON) =====")
    print(dict(probe_dist))

    # --- salvageable claims --------------------------------------------
    salvageable = [x for x in on if probe_status_of(x) == "found"]
    print("===== salvageable claims (probe_status=found) =====")
    print("count=%d" % len(salvageable))
    for x in salvageable:
        probe = next((b for b in x.get("blockers", [])
                      if b.get("kind") == "diagnostic_probe"), {})
        print("  %s  code=%s  dim=%s  fact=%s" % (
            x.get("claim_id"),
            x.get("abstention_code"),
            (x.get("source_dimensions") or {}).get("account", ""),
            probe.get("probe_fact_id", ""),
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
