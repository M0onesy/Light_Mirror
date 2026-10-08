"""CRRV-Bench evaluation helpers.

The benchmark package intentionally depends only on the Python standard
library.  It can therefore be used in the offline/kernel-only deployment.
"""

from .stats import (
    binary_metrics,
    confusion_counts,
    precision_recall_f1,
    safe_divide,
    wilson_interval,
)

__all__ = [
    "binary_metrics",
    "confusion_counts",
    "evaluate_records",
    "load_samples",
    "precision_recall_f1",
    "safe_divide",
    "scan_working_points",
    "wilson_interval",
]


def __getattr__(name: str):
    # Keep ``python -m mingjing.bench.evaluate`` free of runpy's pre-import
    # warning while retaining convenient package-level imports.
    if name in {"evaluate_records", "load_samples", "scan_working_points"}:
        from . import evaluate

        return getattr(evaluate, name)
    raise AttributeError(name)
