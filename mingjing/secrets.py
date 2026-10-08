"""Task-scoped credentials for the private Harness broker.

Only the application host owns DPAPI persistence. The Harness asks this facade
for a credential reference admitted by the frozen task configuration; no model
tool can call it and no credential is placed in environment variables.
"""

from __future__ import annotations

import copy
import hmac
from typing import Any, Mapping

from .config import ConfigError, SecretStore
from .providers import ProviderError, resolve_route


class CredentialAccess:
    """Resolve selected task routes, with an optional mandatory run identity."""

    def __init__(self, store: SecretStore, snapshot: Mapping[str, Any], *, run_id: str = ""):
        self._store = store
        self._run_id = run_id or str(snapshot.get("run_id", ""))
        self._allowed: dict[str, set[str]] = {}
        self._known_values: set[str] = set()
        config = copy.deepcopy(dict(snapshot))
        for role in ("default", "planner", "verifier", "reporter"):
            try:
                route = resolve_route(config, role)
            except ProviderError:
                continue
            ref = str(route.get("credential_ref") or "")
            if ref:
                self._allowed.setdefault(route["provider"], set()).add(ref)
        if config.get("agent", {}).get("cross_provider_fallback"):
            for selected in config.get("agent", {}).get("fallback_routes", []):
                fallback = copy.deepcopy(config)
                fallback["agent"]["routing"]["default"] = selected
                try:
                    route = resolve_route(fallback)
                except ProviderError:
                    continue
                if route.get("credential_ref"):
                    self._allowed.setdefault(route["provider"], set()).add(route["credential_ref"])

    def __repr__(self) -> str:
        return f"CredentialAccess(routes={len(self._allowed)}, run_bound={bool(self._run_id)})"

    def resolve(self, credential_ref: str, *, provider_id: str, run_id: str = "") -> str:
        """Return a key only to the private broker for an admitted route."""
        if self._run_id and not hmac.compare_digest(self._run_id, run_id):
            raise ConfigError("credential request does not belong to this task")
        if credential_ref not in self._allowed.get(provider_id, set()):
            raise ConfigError("credential reference is outside this task's selected routes")
        value = self._store.get(credential_ref)
        if not value:
            raise ConfigError("selected provider credential is not configured")
        self._known_values.add(value)
        return value

    def describe(self, credential_ref: str, *, provider_id: str) -> dict[str, Any]:
        allowed = credential_ref in self._allowed.get(provider_id, set())
        return {"configured": bool(allowed and self._store.has(credential_ref)), "source": "mingjing-dpapi", "writable": False}

    def redact(self, value: Any) -> Any:
        """Remove this task's resolved keys from errors or safe diagnostic data."""
        if isinstance(value, str):
            for secret in sorted(self._known_values, key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {key: self.redact(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(child) for child in value]
        return value


__all__ = ["SecretStore", "CredentialAccess"]
