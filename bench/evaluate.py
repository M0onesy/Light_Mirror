"""CRRV-Bench sample loading, evaluation and working-point scanning.

The evaluator accepts either a single JSON array/object or newline-delimited
JSON.  It deliberately treats labels as data and never computes a fact or
judgement itself; this module only scores records emitted by the MingJing
kernel/agent pipeline.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .stats import binary_metrics, safe_divide, wilson_interval


POSITIVE = "violated"
NEGATIVE = "verified"
ABSTAINED = "abstained"

_ID_KEYS = ("id", "case_id", "sample_id", "claim_id", "uid")
_GOLD_KEYS = ("gold", "expected", "truth", "label", "ground_truth", "gold_label")
_PRED_KEYS = ("prediction", "predicted", "pred", "actual", "result", "status", "decision")
_TYPE_KEYS = ("error_type", "error", "category", "type", "rule_id")


def _first(record: Mapping[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return default


def normalize_label(value: Any) -> str:
    """Normalize common benchmark labels to ``violated/verified/abstained``."""

    if isinstance(value, bool):
        return POSITIVE if value else NEGATIVE
    if value is None:
        return ABSTAINED
    text = str(value).strip().lower()
    if text in {"violated", "violation", "wrong", "error", "incorrect", "false", "1", "positive", "fail", "failed"}:
        return POSITIVE
    if text in {"verified", "verify", "correct", "true", "0", "negative", "pass", "passed", "valid", "safe", "clean"}:
        return NEGATIVE
    if text in {"abstained", "abstain", "unknown", "uncertain", "unverifiable", "na", "n/a", "null", "none", ""}:
        return ABSTAINED
    # Keep unknown labels visible; callers may use arbitrary status names.
    return text


def _as_records(value: Any, source: str = "") -> list[dict[str, Any]]:
    """Convert supported JSON shapes into a list of mapping records."""

    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        for key in ("records", "samples", "items", "data", "cases", "results"):
            nested = value.get(key)
            if isinstance(nested, (list, Mapping)):
                return _as_records(nested, source=source)
        # A mapping of case id -> object is also accepted.
        if value and all(isinstance(item, Mapping) for item in value.values()):
            records = []
            for case_id, item in value.items():
                record = dict(item)
                record.setdefault("id", case_id)
                records.append(record)
            return records
        return [dict(value)] if value else []
    raise ValueError(f"Unsupported JSON sample shape in {source or 'input'}")


def load_samples(path: str | Path) -> list[dict[str, Any]]:
    """Load JSON or JSONL samples from a file (or all files in a directory)."""

    target = Path(path)
    if target.is_dir():
        records: list[dict[str, Any]] = []
        for child in sorted(target.iterdir()):
            if child.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
                records.extend(load_samples(child))
        return records
    raw = target.read_text(encoding="utf-8-sig")
    if target.suffix.lower() in {".jsonl", ".ndjson"}:
        records = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                records.extend(_as_records(json.loads(line), source=f"{target}:{line_no}"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {target}:{line_no}: {exc}") from exc
        return records
    try:
        return _as_records(json.loads(raw), source=str(target))
    except json.JSONDecodeError:
        # Be forgiving when a JSONL file was saved with a .txt extension.
        records = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                records.extend(_as_records(json.loads(line), source=f"{target}:{line_no}"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {target} (line {line_no}): {exc}") from exc
        return records


def _canonicalize(record: Mapping[str, Any], index: int) -> dict[str, Any]:
    gold = normalize_label(_first(record, _GOLD_KEYS))
    predicted = normalize_label(_first(record, _PRED_KEYS))
    item = dict(record)
    item["id"] = str(_first(record, _ID_KEYS, default=index + 1))
    item["gold_label"] = gold
    item["predicted_label"] = predicted
    item["error_type"] = str(_first(record, _TYPE_KEYS, default="unknown"))
    item["is_trap"] = bool(_first(record, ("is_trap", "trap", "trap_case"), default=False))
    item["evidence_hit"] = _truth_value(_first(record, ("evidence_hit", "evidence_ok", "evidence_match"), default=None))
    item["repair_pass"] = _truth_value(_first(record, ("repair_pass", "fixed", "passes_after_repair"), default=None))
    return item


def _truth_value(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "hit", "pass", "passed", "ok"}


def _metrics_for(records: Sequence[Mapping[str, Any]], confidence: float) -> dict[str, Any]:
    expected = [item["gold_label"] == POSITIVE for item in records]
    predicted = [item["predicted_label"] == POSITIVE for item in records]
    result = binary_metrics(expected, predicted, positive_label=True, confidence=confidence)
    result["abstained_predictions"] = sum(item["predicted_label"] == ABSTAINED for item in records)
    result["coverage"] = safe_divide(len(records) - result["abstained_predictions"], len(records))
    return result


def evaluate_records(
    records: Iterable[Mapping[str, Any]],
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Evaluate normalized or raw records and return a JSON-serializable report."""

    canonical = [_canonicalize(record, index) for index, record in enumerate(records)]
    overall = _metrics_for(canonical, confidence)
    traps = [item for item in canonical if item["is_trap"]]
    trap_fp = sum(
        item["gold_label"] != POSITIVE and item["predicted_label"] == POSITIVE for item in traps
    )
    trap_positive = sum(item["gold_label"] != POSITIVE for item in traps)

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in canonical:
        by_type[item["error_type"]].append(item)
    by_error_type = {
        error_type: _metrics_for(group, confidence)
        for error_type, group in sorted(by_type.items())
    }

    evidence_items = [item for item in canonical if item["evidence_hit"] is not None]
    repair_items = [item for item in canonical if item["repair_pass"] is not None]
    report: dict[str, Any] = {
        "schema_version": "crrv-bench.v1",
        "total": len(canonical),
        "metrics": overall,
        "by_error_type": by_error_type,
        "trap": {
            "total": len(traps),
            "false_positives": trap_fp,
            "false_positive_rate": safe_divide(trap_fp, trap_positive),
            "false_positive_interval": list(wilson_interval(trap_fp, trap_positive, confidence)),
        },
        "evidence": {
            "evaluated": len(evidence_items),
            "hits": sum(bool(item["evidence_hit"]) for item in evidence_items),
            "hit_rate": safe_divide(sum(bool(item["evidence_hit"]) for item in evidence_items), len(evidence_items)),
        },
        "repair": {
            "evaluated": len(repair_items),
            "passes": sum(bool(item["repair_pass"]) for item in repair_items),
            "pass_rate": safe_divide(sum(bool(item["repair_pass"]) for item in repair_items), len(repair_items)),
        },
        "records": canonical,
    }
    return report


def _parse_thresholds(values: str | None) -> list[float] | None:
    if not values:
        return None
    thresholds = []
    for token in values.split(","):
        value = float(token.strip())
        if not math.isfinite(value):
            raise ValueError(f"Invalid threshold: {token}")
        thresholds.append(value)
    return sorted(set(thresholds))


def scan_working_points(
    records: Iterable[Mapping[str, Any]],
    thresholds: Iterable[float] | None = None,
    score_field: str = "score",
    confidence: float = 0.95,
) -> list[dict[str, Any]]:
    """Scan score thresholds and return metrics/trap FPR at every working point.

    A prediction is positive when ``score >= threshold``. Records without a
    numeric score are skipped; if no thresholds are supplied, unique observed
    scores plus zero are scanned.
    """

    canonical = [_canonicalize(record, index) for index, record in enumerate(records)]
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in canonical:
        value = item.get(score_field)
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(score):
            scored.append((score, item))
    if thresholds is None:
        values = sorted({score for score, _ in scored})
        thresholds_list = sorted(set([0.0, *values]))
    else:
        thresholds_list = sorted(set(float(value) for value in thresholds))
    output = []
    for threshold in thresholds_list:
        subset: list[dict[str, Any]] = []
        for score, item in scored:
            copy = dict(item)
            copy["predicted_label"] = POSITIVE if score >= threshold else NEGATIVE
            subset.append(copy)
        metrics = _metrics_for(subset, confidence)
        traps = [item for item in subset if item["is_trap"] and item["gold_label"] != POSITIVE]
        fp = sum(item["predicted_label"] == POSITIVE for item in traps)
        output.append(
            {
                "threshold": threshold,
                "evaluated": len(subset),
                "metrics": metrics,
                "trap_false_positives": fp,
                "trap_total": len(traps),
                "trap_false_positive_rate": safe_divide(fp, len(traps)),
            }
        )
    return output


def _load_combined(gold_path: str | None, prediction_path: str | None, samples_path: str | None) -> list[dict[str, Any]]:
    if samples_path:
        records = load_samples(samples_path)
        return records
    if not gold_path or not prediction_path:
        raise ValueError("Provide --samples, or both --gold and --predictions")
    gold = load_samples(gold_path)
    predictions = load_samples(prediction_path)
    pred_by_id = {str(_first(item, _ID_KEYS, default=index + 1)): item for index, item in enumerate(predictions)}
    combined = []
    for index, expected in enumerate(gold):
        case_id = str(_first(expected, _ID_KEYS, default=index + 1))
        predicted = pred_by_id.get(case_id, predictions[index] if index < len(predictions) else {})
        record = dict(expected)
        for key in _PRED_KEYS:
            if key in predicted:
                record["prediction"] = predicted[key]
                break
        for key in ("score", "is_trap", "evidence_hit", "repair_pass"):
            if key in predicted:
                record[key] = predicted[key]
        record.setdefault("id", case_id)
        combined.append(record)
    return combined


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate MingJing CRRV-Bench JSON/JSONL samples")
    parser.add_argument("--samples", help="Combined JSON/JSONL records with gold and prediction fields")
    parser.add_argument("--gold", help="Gold JSON/JSONL records (use with --predictions)")
    parser.add_argument("--predictions", help="Prediction JSON/JSONL records (use with --gold)")
    parser.add_argument("--out", help="Write report JSON to this path; stdout when omitted")
    parser.add_argument("--thresholds", help="Comma-separated score thresholds for working-point scan")
    parser.add_argument("--score-field", default="score")
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--no-records", action="store_true", help="Omit canonical records from output")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        records = _load_combined(args.gold, args.predictions, args.samples)
        report = evaluate_records(records, confidence=args.confidence)
        points = scan_working_points(
            records,
            thresholds=_parse_thresholds(args.thresholds),
            score_field=args.score_field,
            confidence=args.confidence,
        )
        if points:
            report["working_points"] = points
        if args.no_records:
            report.pop("records", None)
        serialized = json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True)
        if args.out:
            destination = Path(args.out)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(serialized + "\n", encoding="utf-8")
        else:
            print(serialized)
        return 0
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
