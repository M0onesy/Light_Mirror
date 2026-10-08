"""Provider-neutral configuration and immutable financial-agent routing.

This module performs no network I/O and never resolves credentials. Capability
discovery and Agent tool tests are delegated to the configured Harness runtime.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Mapping
from urllib.parse import urlsplit


PROTOCOLS = ("deepseek", "openai-completions", "openai-responses", "anthropic-messages")
ROLES = ("default", "planner", "verifier", "reporter")
PROVIDER_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
SECRET_REF = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
FORBIDDEN_KEYS = frozenset({"api_key", "api_token", "token", "secret", "authorization", "password"})


class ProviderError(ValueError):
    """Configuration or route failure whose message contains no credential."""


@dataclass
class ModelConfig:
    id: str = ""
    display_name: str = ""
    context_window: int = 65536
    max_tokens: int = 4096
    input: list[str] = field(default_factory=lambda: ["text"])
    reasoning: str = "auto"
    temperature: float | None = None
    compat: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderConfig:
    enabled: bool = True
    display_name: str = ""
    vendor: str = "custom"
    protocol: str = "openai-completions"
    base_url: str = ""
    chat_path: str = "/chat/completions"
    credential_ref: str = ""
    models: list[ModelConfig] = field(default_factory=list)
    timeout_s: int = 120
    retries: int = 2
    verify_tls: bool = True
    # False means material may not be sent to this endpoint, even if configured.
    material_access: bool = True


@dataclass
class RouteConfig:
    provider: str = ""
    model: str = ""


@dataclass
class RoutingConfig:
    default: RouteConfig = field(default_factory=RouteConfig)
    planner: RouteConfig | None = None
    verifier: RouteConfig | None = None
    reporter: RouteConfig | None = None


@dataclass
class AgentBudget:
    max_model_calls: int = 128
    max_tool_calls: int = 1000
    max_total_tokens: int = 1000000
    max_runtime_s: int = 1800
    max_subagents: int = 2
    max_depth: int = 1


@dataclass
class FollowupBudget:
    max_model_calls: int = 12
    max_tool_calls: int = 80
    max_total_tokens: int = 150000
    max_runtime_s: int = 180


@dataclass
class AgentConfig:
    enabled: bool = False
    runtime: str = "harness"
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    budget: AgentBudget = field(default_factory=AgentBudget)
    followup_budget: FollowupBudget = field(default_factory=FollowupBudget)
    cross_provider_fallback: bool = False
    fallback_routes: list[RouteConfig] = field(default_factory=list)


def reject_plaintext_credentials(value: Any, path: str = "configuration") -> None:
    """Reject credential fields recursively without quoting their values."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", str(key).strip())
            normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower().replace("-", "_")
            if normalized in FORBIDDEN_KEYS or normalized in {"apikey", "apitoken", "access_key"} or normalized.endswith(("_api_key", "_token")):
                raise ProviderError("configuration: plaintext credentials belong in SecretStore")
            reject_plaintext_credentials(child, path)
    elif isinstance(value, (list, tuple)):
        for child in value:
            reject_plaintext_credentials(child, path)


def _record(typ: type, raw: Any, label: str):
    if not isinstance(raw, Mapping):
        raise ProviderError(f"{label} must be an object")
    reject_plaintext_credentials(raw, label)
    allowed = {f.name for f in fields(typ)}
    unknown = set(raw) - allowed
    if unknown:
        # Values and unknown field names can themselves contain a pasted key.
        raise ProviderError(f"{label} contains unsupported fields")
    return typ(**copy.deepcopy(dict(raw)))


def parse_providers(raw: Any) -> dict[str, ProviderConfig]:
    if not isinstance(raw, Mapping):
        raise ProviderError("providers must be an object keyed by provider ID")
    result: dict[str, ProviderConfig] = {}
    for identifier, value in raw.items():
        if not isinstance(identifier, str) or not PROVIDER_ID.fullmatch(identifier):
            raise ProviderError("provider ID must use lowercase letters, digits and hyphens")
        config = _record(ProviderConfig, value, f"providers.{identifier}")
        if not isinstance(config.models, list):
            raise ProviderError(f"providers.{identifier}.models must be an array")
        config.models = [_record(ModelConfig, model, f"providers.{identifier}.models") for model in config.models]
        result[identifier] = config
    return result


def parse_agent(raw: Any) -> AgentConfig:
    config = _record(AgentConfig, raw, "agent")
    config.routing = _record(RoutingConfig, raw.get("routing", {}), "agent.routing")
    config.routing.default = _record(RouteConfig, raw.get("routing", {}).get("default", {}), "agent.routing.default")
    for role in ROLES[1:]:
        value = getattr(config.routing, role)
        if value is not None:
            setattr(config.routing, role, _record(RouteConfig, value, f"agent.routing.{role}"))
    config.budget = _record(AgentBudget, raw.get("budget", {}), "agent.budget")
    config.followup_budget = _record(FollowupBudget, raw.get("followup_budget", {}), "agent.followup_budget")
    if not isinstance(config.fallback_routes, list):
        raise ProviderError("agent.fallback_routes must be an array")
    config.fallback_routes = [_record(RouteConfig, route, "agent.fallback_routes") for route in config.fallback_routes]
    return config


def provider_presets() -> dict[str, dict[str, Any]]:
    """Offer endpoints and protocols without claiming untested model support."""
    return {
        "deepseek": {"display_name": "DeepSeek", "vendor": "deepseek", "protocol": "deepseek", "base_url": "https://api.deepseek.com", "models": [], "capabilities": "not_tested"},
        "kimi": {"display_name": "Kimi / Moonshot", "vendor": "moonshotai", "protocol": "openai-completions", "base_url": "https://api.moonshot.cn/v1", "models": [], "capabilities": "not_tested"},
        "openai": {"display_name": "OpenAI GPT", "vendor": "openai", "protocol": "openai-responses", "base_url": "https://api.openai.com/v1", "models": [], "capabilities": "not_tested"},
        "custom": {"display_name": "自定义 API", "vendor": "custom", "protocol": "openai-completions", "base_url": "", "models": [], "capabilities": "not_tested"},
    }


def validate_providers(providers: Mapping[str, ProviderConfig], agent: AgentConfig) -> list[str]:
    errors: list[str] = []
    model_ids: dict[str, set[str]] = {}
    for identifier, provider in providers.items():
        label = f"providers.{identifier}"
        if provider.protocol not in PROTOCOLS:
            errors.append(f"{label}.protocol is unsupported")
        if any(not isinstance(value, bool) for value in (provider.enabled, provider.material_access, provider.verify_tls)):
            errors.append(f"{label}.enabled/material_access/verify_tls must be boolean")
        if not isinstance(provider.base_url, str):
            errors.append(f"{label}.base_url must be a string")
        elif provider.base_url:
            try:
                url = urlsplit(provider.base_url)
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                    raise ValueError()
                loopback = url.hostname.lower() in {"127.0.0.1", "localhost", "::1"}
                if url.scheme == "http" and not loopback:
                    errors.append(f"{label}.base_url requires HTTPS outside loopback")
                if provider.verify_tls is not True:
                    errors.append(f"{label}.verify_tls must remain enabled")
            except (ValueError, TypeError):
                errors.append(f"{label}.base_url must be an HTTP(S) URL without embedded credentials")
        elif provider.enabled:
            errors.append(f"{label}.base_url is required")
        if not isinstance(provider.credential_ref, str) or provider.credential_ref and not SECRET_REF.fullmatch(provider.credential_ref):
            errors.append(f"{label}.credential_ref is invalid")
        if not isinstance(provider.chat_path, str) or not provider.chat_path.startswith("/") or provider.chat_path.startswith("//") or "?" in provider.chat_path or "#" in provider.chat_path or "://" in provider.chat_path or "\\" in provider.chat_path or any(ord(ch) < 32 for ch in provider.chat_path):
            errors.append(f"{label}.chat_path must be an absolute API path")
        for field_name, minimum, maximum in (("timeout_s", 1, 1800), ("retries", 0, 5)):
            value = getattr(provider, field_name)
            if type(value) is not int or not minimum <= value <= maximum:
                errors.append(f"{label}.{field_name} must be between {minimum} and {maximum}")
        seen: set[str] = set()
        for model in provider.models:
            if not isinstance(model.id, str) or not model.id.strip() or model.id in seen:
                errors.append(f"{label}.models IDs must be non-empty and unique")
            else:
                seen.add(model.id)
            if type(model.context_window) is not int or model.context_window <= 0 or type(model.max_tokens) is not int or model.max_tokens <= 0:
                errors.append(f"{label}.models token capacities must be positive integers")
            if model.temperature is not None and (isinstance(model.temperature, bool) or not isinstance(model.temperature, (int, float)) or not 0 <= model.temperature <= 2):
                errors.append(f"{label}.models.temperature must be null or between 0 and 2")
            if not isinstance(model.reasoning, str) or model.reasoning not in {"auto", "off", "low", "medium", "high", "max", "xhigh"}:
                errors.append(f"{label}.models.reasoning is unsupported")
            if not isinstance(model.input, list) or not model.input or any(not isinstance(value, str) or value not in {"text", "image"} for value in model.input):
                errors.append(f"{label}.models.input must select text or image")
            if not isinstance(model.compat, dict):
                errors.append(f"{label}.models.compat must be an object")
        model_ids[identifier] = seen
    if not isinstance(agent.runtime, str) or agent.runtime not in {"harness", "kernel"}:
        errors.append("agent.runtime must be harness or kernel")
    if not isinstance(agent.enabled, bool) or not isinstance(agent.cross_provider_fallback, bool):
        errors.append("agent.enabled/cross_provider_fallback must be boolean")
    for label, budget in (("agent.budget", agent.budget), ("agent.followup_budget", agent.followup_budget)):
        for name, value in asdict(budget).items():
            if type(value) is not int or value < 1:
                errors.append(f"{label}.{name} must be a positive integer")
    if type(agent.budget.max_subagents) is int and agent.budget.max_subagents > 2 or type(agent.budget.max_depth) is int and agent.budget.max_depth > 1:
        errors.append("agent descendants are limited to two children and one level")
    for role in ROLES:
        route = getattr(agent.routing, role)
        if route is None or not route.provider and not route.model:
            continue
        if not isinstance(route.provider, str) or not isinstance(route.model, str) or route.model not in model_ids.get(route.provider, set()):
            errors.append(f"agent.routing.{role} refers to an unavailable provider/model")
    for route in agent.fallback_routes:
        if not isinstance(route.provider, str) or not isinstance(route.model, str) or route.model not in model_ids.get(route.provider, set()):
            errors.append("agent.fallback_routes refers to an unavailable provider/model")
    return errors


def _dict(config: Any) -> dict[str, Any]:
    return copy.deepcopy(config.to_dict() if hasattr(config, "to_dict") else dict(config))


def resolve_route(config: Any, role: str = "default") -> dict[str, Any]:
    """Resolve one role's endpoint and model settings; never include a key."""
    raw = _dict(config)
    if role not in ROLES:
        raise ProviderError("unknown financial Agent role")
    routing = raw.get("agent", {}).get("routing", {})
    route = routing.get(role) or routing.get("default") or {}
    provider_id = route.get("provider", "")
    provider = raw.get("providers", {}).get(provider_id)
    if not provider or not provider.get("enabled", True):
        raise ProviderError("selected provider is unavailable or disabled")
    if not provider.get("material_access", True):
        raise ProviderError("selected provider is not authorized to receive task material")
    model = next((item for item in provider.get("models", []) if item.get("id") == route.get("model")), None)
    if model is None:
        raise ProviderError("selected model is not configured")
    reasoning = model.get("reasoning", "auto")
    # DeepSeek's native adapter uses `off`, not `none`, to serialize
    # thinking:{type:"disabled"}. Financial source binding defaults to that
    # low-latency mode; an explicit user effort always remains authoritative.
    # Keep the persisted model and other providers' defaults untouched.
    if provider.get("protocol") == "deepseek" and reasoning == "auto":
        reasoning = "off"
    return {"provider": provider_id, "model": model["id"], "vendor": provider.get("vendor", "custom"),
            "protocol": provider.get("protocol", "openai-completions"), "base_url": provider.get("base_url", ""),
            "chat_path": provider.get("chat_path", "/chat/completions"), "credential_ref": provider.get("credential_ref", ""),
            "timeout_s": provider.get("timeout_s", 120), "retries": provider.get("retries", 2),
            "verify_tls": provider.get("verify_tls", True), "max_tokens": model.get("max_tokens", 4096),
            "context_window": model.get("context_window", 65536), "reasoning": reasoning,
            "temperature": model.get("temperature"), "input": copy.deepcopy(model.get("input", ["text"])),
            "compat": copy.deepcopy(model.get("compat", {}))}


def freeze_agent_config(config: Any) -> dict[str, Any]:
    """Detach a full task configuration and include its reproducible digest."""
    snapshot = _dict(config)
    reject_plaintext_credentials(snapshot)
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    snapshot["config_sha256"] = hashlib.sha256(canonical).hexdigest()
    snapshot["config_revision"] = snapshot.get("revision", 0)
    return snapshot


__all__ = ["PROTOCOLS", "ROLES", "ProviderError", "ProviderConfig", "ModelConfig", "RouteConfig", "RoutingConfig", "AgentBudget", "FollowupBudget", "AgentConfig", "parse_providers", "parse_agent", "provider_presets", "validate_providers", "resolve_route", "freeze_agent_config", "reject_plaintext_credentials"]
