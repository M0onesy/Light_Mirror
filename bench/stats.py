"""Small, deterministic statistical primitives used by CRRV-Bench.

No NumPy/SciPy dependency is required.  Wilson intervals are preferable to
normal-approximation intervals for the small benchmark sets used by MingJing.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Mapping, Sequence


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Return ``numerator / denominator`` or ``default`` for a zero denominator."""

    if denominator == 0:
        return float(default)
    return float(numerator) / float(denominator)


def wilson_interval(
    successes: int,
    trials: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Return a Wilson score interval for a binomial proportion.

    ``(0.0, 0.0)`` is returned when there are no trials.  The confidence
    level must be strictly between zero and one, and successes must lie in
    ``[0, trials]``.
    """

    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("successes and trials must satisfy 0 <= successes <= trials")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if trials == 0:
        return (0.0, 0.0)

    # statistics.NormalDist is part of the standard library (Python 3.8+).
    z = statistics.NormalDist().inv_cdf(0.5 + confidence / 2.0)
    n = float(trials)
    p = float(successes) / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denominator
    radius = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / denominator
    return (max(0.0, centre - radius), min(1.0, centre + radius))


def confusion_counts(
    y_true: Iterable[Any],
    y_pred: Iterable[Any],
    positive_label: Any = True,
) -> dict[str, int]:
    """Count TP/FP/FN/TN for two equally-sized binary label iterables."""

    tp = fp = fn = tn = 0
    for expected, predicted in zip(y_true, y_pred):
        expected_positive = expected == positive_label
        predicted_positive = predicted == positive_label
        if expected_positive and predicted_positive:
            tp += 1
        elif not expected_positive and predicted_positive:
            fp += 1
        elif expected_positive:
            fn += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "total": tp + fp + fn + tn}


def precision_recall_f1(
    tp: int,
    fp: int,
    fn: int,
    tn: int = 0,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Build precision/recall/F1 and supporting counts from a confusion matrix."""

    precision = safe_divide(tp, tp + fp)
    recall = safe_divide(tp, tp + fn)
    f1 = safe_divide(2.0 * precision * recall, precision + recall)
    total = tp + fp + fn + tn
    accuracy = safe_divide(tp + tn, total)
    return {
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "tn": int(tn),
        "support": int(tp + fn),
        "negative_support": int(fp + tn),
        "total": int(total),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "precision_interval": list(wilson_interval(tp, tp + fp, confidence)),
        "recall_interval": list(wilson_interval(tp, tp + fn, confidence)),
        "confidence": confidence,
    }


def binary_metrics(
    y_true: Sequence[Any],
    y_pred: Sequence[Any],
    positive_label: Any = True,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Return confusion counts and metrics for binary labels.

    ``ValueError`` is raised for different-length sequences.  This catches a
    malformed benchmark instead of silently dropping records through ``zip``.
    """

    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have the same length")
    counts = confusion_counts(y_true, y_pred, positive_label=positive_label)
    return precision_recall_f1(
        counts["tp"], counts["fp"], counts["fn"], counts["tn"], confidence=confidence
    )


def rate_with_interval(
    successes: int,
    trials: int,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Represent a rate and its Wilson interval in JSON-friendly form."""

    return {
        "successes": int(successes),
        "trials": int(trials),
        "rate": safe_divide(successes, trials),
        "interval": list(wilson_interval(successes, trials, confidence)),
        "confidence": confidence,
    }


def aggregate_binary_metrics(
    groups: Mapping[str, tuple[Iterable[Any], Iterable[Any]]],
    positive_label: Any = True,
    confidence: float = 0.95,
) -> dict[str, dict[str, Any]]:
    """Convenience helper for evaluating multiple named subsets."""

    output: dict[str, dict[str, Any]] = {}
    for name, (expected, predicted) in groups.items():
        output[str(name)] = binary_metrics(
            list(expected), list(predicted), positive_label=positive_label, confidence=confidence
        )
    return output
