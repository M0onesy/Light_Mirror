"""Reject mixed historical/current application packages before startup."""
from __future__ import annotations

import importlib.util
from pathlib import Path


APPLICATION_MODULES = ("mingjing", "mingjing.launcher", "mingjing.cli", "mingjing.server",
                       "mingjing.agent", "mingjing.judge", "mingjing.config")


def assert_application_origin(root: str | Path) -> dict[str, str]:
    expected = Path(root).resolve() / "mingjing"
    origins: dict[str, str] = {}
    for name in APPLICATION_MODULES:
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.origin:
            raise RuntimeError(f"Required application module is unavailable: {name}")
        origin = Path(spec.origin).resolve()
        if not origin.is_relative_to(expected):
            raise RuntimeError(f"Application module comes from a different checkout: {name}")
        origins[name] = str(origin)
    return origins
