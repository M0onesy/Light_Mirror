"""Configuration migration and credential isolation without provider calls."""

import json
from pathlib import Path

import pytest

from mingjing.config import AppConfig, ConfigError, ConfigManager, SecretStore
from mingjing.providers import ProviderError, freeze_agent_config, provider_presets, resolve_route
from mingjing.secrets import CredentialAccess


def provider(ref="test_key", protocol="openai-completions"):
    return {"base_url": "https://example.invalid/v1", "credential_ref": ref,
            "protocol": protocol, "models": [{"id": "test-model"}]}


def configured(tmp_path: Path):
    manager = ConfigManager(tmp_path / "config.json", SecretStore(tmp_path / "secrets"))
    manager.update_from_dict({"providers": {"first": provider(), "other": provider("other_key")},
                              "agent": {"enabled": True, "routing": {"default": {"provider": "first", "model": "test-model"}}}})
    return manager


def test_v1_migration_preserves_model_reference_revision_and_original(tmp_path):
    target = tmp_path / "config.json"
    original = {"version": 1, "revision": 7, "llm": {"enabled": True,
        "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
        "api_key_ref": "llm_api_key", "thinking": "disabled", "max_tokens": 2000}}
    target.write_text(json.dumps(original), encoding="utf-8")
    manager = ConfigManager(target, SecretStore(tmp_path / "secrets"))
    result = manager.public_dict()
    assert result["version"] == 2
    assert result["revision"] == 7
    assert result["agent"]["routing"]["default"] == {"provider": "legacy-default", "model": "deepseek-flash"}
    assert resolve_route(result)["credential_ref"] == "llm_api_key"
    assert resolve_route(result)["reasoning"] == "off"
    assert json.loads(target.with_name("config.v1.bak.json").read_text()) == original
    assert ConfigManager(target, manager.secret_store).public_dict() == result


def test_roles_inherit_default_and_snapshot_is_detached(tmp_path):
    manager = configured(tmp_path)
    snapshot = manager.snapshot()
    assert resolve_route(snapshot, "planner")["provider"] == "first"
    manager.update_from_dict({"agent": {"routing": {"planner": {"provider": "other", "model": "test-model"}}}})
    assert resolve_route(manager.get(), "planner")["provider"] == "other"
    assert resolve_route(snapshot, "planner")["provider"] == "first"
    assert snapshot["config_revision"] == 1
    assert len(snapshot["config_sha256"]) == 64
    assert snapshot["config_sha256"] != freeze_agent_config(manager.get())["config_sha256"]


@pytest.mark.parametrize("protocol", ["deepseek", "openai-completions", "openai-responses", "anthropic-messages"])
def test_supported_protocols_are_configured_without_claiming_api_success(tmp_path, protocol):
    manager = configured(tmp_path)
    manager.update_from_dict({"providers": {"first": {"protocol": protocol}}})
    route = resolve_route(manager.get())
    assert route["protocol"] == protocol
    assert "api_key" not in route
    assert all(row["capabilities"] == "not_tested" for row in provider_presets().values())


@pytest.mark.parametrize("protocol", ["deepseek", "openai-completions", "openai-responses", "anthropic-messages"])
def test_auto_reasoning_uses_native_deepseek_low_latency_mode_only(tmp_path, protocol):
    manager = configured(tmp_path)
    manager.update_from_dict({"providers": {"first": {"protocol": protocol}}})
    persisted_before = manager.path.read_bytes()
    snapshot = manager.snapshot()
    assert resolve_route(snapshot, "planner")["reasoning"] == ("off" if protocol == "deepseek" else "auto")
    assert snapshot["providers"]["first"]["models"][0]["reasoning"] == "auto"
    assert manager.path.read_bytes() == persisted_before


@pytest.mark.parametrize("effort", ["off", "low", "high", "max"])
def test_explicit_deepseek_reasoning_remains_authoritative(tmp_path, effort):
    manager = configured(tmp_path)
    manager.update_from_dict({"providers": {"first": {"protocol": "deepseek", "models": [{"id": "test-model", "reasoning": effort}]}}})
    assert resolve_route(manager.snapshot(), "reporter")["reasoning"] == effort


def test_auto_reasoning_uses_protocol_not_vendor_brand(tmp_path):
    manager = configured(tmp_path)
    manager.update_from_dict({"providers": {"first": {"vendor": "deepseek", "protocol": "openai-completions"},
                                            "other": {"vendor": "custom", "protocol": "deepseek"}},
                              "agent": {"routing": {"planner": {"provider": "other", "model": "test-model"}}}})
    assert resolve_route(manager.get())["reasoning"] == "auto"
    assert resolve_route(manager.get(), "planner")["reasoning"] == "off"


def test_rejected_provider_changes_keep_live_and_saved_config(tmp_path):
    manager = configured(tmp_path)
    previous = manager.public_dict()
    saved = manager.path.read_bytes()
    for patch in ({"providers": {"first": {"protocol": "bogus"}}},
                  {"providers": {"first": {"base_url": "https://user:secret@example.invalid"}}},
                  {"providers": {"first": {"verify_tls": False}}},
                  {"agent": {"routing": {"default": {"provider": "missing", "model": "test-model"}}}}):
        with pytest.raises(ConfigError):
            manager.update_from_dict(patch)
        assert manager.public_dict() == previous
        assert manager.path.read_bytes() == saved


def test_plaintext_credentials_are_rejected_in_nested_fields_without_echo(tmp_path):
    manager = configured(tmp_path)
    for patch in ({"providers": {"first": {"api_key": "sensitive-test-value"}}},
                  {"providers": {"first": {"models": [{"id": "test-model", "compat": {"Authorization": "sensitive-test-value"}}]}}},
                  {"agent": {"token": "sensitive-test-value"}}):
        with pytest.raises(ConfigError) as error:
            manager.update_from_dict(patch)
        assert "sensitive-test-value" not in str(error.value)
    assert "sensitive-test-value" not in str(manager.public_dict())


def test_legacy_updates_remain_compatible_with_provider_routes(tmp_path):
    manager = configured(tmp_path)
    manager.update_from_dict({"llm": {"enabled": True, "base_url": "https://api.deepseek.com", "model": "deepseek-flash"}})
    assert manager.public_dict()["providers"]["legacy-default"]["models"][0]["id"] == "deepseek-flash"
    assert resolve_route(manager.get())["provider"] == "first"


def test_credentials_are_limited_to_selected_routes_and_bound_run(tmp_path):
    manager = configured(tmp_path)
    manager.set_secret("test_key", "allowed-test-secret")
    manager.set_secret("other_key", "unselected-test-secret")
    access = CredentialAccess(manager.secret_store, manager.snapshot(), run_id="run-1")
    assert access.resolve("test_key", provider_id="first", run_id="run-1") == "allowed-test-secret"
    for ref, provider_id, run_id in (("other_key", "other", "run-1"), ("test_key", "other", "run-1"), ("test_key", "first", "run-2")):
        with pytest.raises(ConfigError):
            access.resolve(ref, provider_id=provider_id, run_id=run_id)
    assert "allowed-test-secret" not in repr(access)
    assert "allowed-test-secret" not in repr(manager.secret_store)
    assert access.redact({"error": "upstream echoed allowed-test-secret"}) == {"error": "upstream echoed [REDACTED]"}
    assert "allowed-test-secret" not in str(manager.public_dict())
    assert "allowed-test-secret" not in manager.path.read_text(encoding="utf-8")
    if manager.secret_store.path.exists():
        assert "allowed-test-secret" not in manager.secret_store.path.read_text(encoding="utf-8")


def test_cross_provider_fallback_is_opt_in(tmp_path):
    manager = configured(tmp_path)
    manager.set_secret("other_key", "test-other")
    manager.update_from_dict({"agent": {"fallback_routes": [{"provider": "other", "model": "test-model"}]}})
    access = CredentialAccess(manager.secret_store, manager.snapshot())
    with pytest.raises(ConfigError):
        access.resolve("other_key", provider_id="other")
    manager.update_from_dict({"agent": {"cross_provider_fallback": True}})
    assert CredentialAccess(manager.secret_store, manager.snapshot()).resolve("other_key", provider_id="other") == "test-other"


def test_material_permission_is_enforced_before_credentials(tmp_path):
    manager = configured(tmp_path)
    manager.update_from_dict({"providers": {"first": {"material_access": False}}})
    with pytest.raises(ProviderError, match="not authorized"):
        resolve_route(manager.get())


def test_windows_dpapi_failure_never_writes_file_key_fallback(tmp_path, monkeypatch):
    import mingjing.config as module
    monkeypatch.setattr(module, "_dpapi_available", lambda: True)
    monkeypatch.setattr(module, "_dpapi_protect", lambda data: (_ for _ in ()).throw(OSError("unavailable")))
    store = SecretStore(tmp_path)
    with pytest.raises(ConfigError, match="no fallback"):
        store.set("test_key", "sensitive-test-value")
    assert not store.path.exists()
    assert not store.key_path.exists()


def test_provider_deletion_requires_repairing_referencing_route(tmp_path):
    manager = configured(tmp_path)
    with pytest.raises(ConfigError):
        manager.update_from_dict({"providers": {"first": None}})
    manager.update_from_dict({"providers": {"first": None}, "agent": {"routing": {"default": {"provider": "other", "model": "test-model"}}}})
    assert "first" not in manager.public_dict()["providers"]


def test_user_budget_defaults():
    agent = AppConfig().to_dict()["agent"]
    assert agent["budget"] == {"max_model_calls": 128, "max_tool_calls": 1000, "max_total_tokens": 1000000, "max_runtime_s": 1800, "max_subagents": 2, "max_depth": 1, "binding_group_size": 16}
    assert agent["followup_budget"] == {"max_model_calls": 12, "max_tool_calls": 80, "max_total_tokens": 150000, "max_runtime_s": 180}


@pytest.mark.parametrize("patch", [
    {"providers": {"first": {"models": [{"id": []}]}}},
    {"providers": {"first": {"credential_ref": ["key"]}}},
    {"providers": {"first": {"base_url": 42}}},
    {"providers": {"first": {"verify_tls": "false"}}},
    {"providers": {"first": {"chat_path": "//different-host/chat"}}},
    {"providers": {"first": {"models": [{"id": "test-model", "reasoning": []}]}}},
    {"providers": {"first": {"models": [{"id": "test-model", "input": [[]]}]}}},
    {"agent": {"runtime": []}},
    {"agent": {"routing": {"default": {"provider": [], "model": "test-model"}}}},
    {"agent": {"budget": {"max_subagents": "two"}}},
])
def test_malformed_provider_types_reject_transactionally(tmp_path, patch):
    manager = configured(tmp_path)
    before = manager.path.read_bytes()
    revision = manager.get().revision
    with pytest.raises(ConfigError):
        manager.update_from_dict(patch)
    assert manager.path.read_bytes() == before
    assert manager.get().revision == revision


@pytest.mark.parametrize("name", ["apiKey", "APIKey", "api-key", "apikey", "apiToken", "accessToken", " Authorization "])
def test_compat_never_persists_plaintext_credential_aliases(tmp_path, name):
    manager = configured(tmp_path)
    before = manager.path.read_bytes()
    with pytest.raises(ConfigError, match="plaintext") as denied:
        manager.update_from_dict({"providers": {"first": {"models": [{"id": "test-model", "compat": {name: "must-stay-private"}}]}}})
    assert "must-stay-private" not in str(denied.value)
    assert manager.path.read_bytes() == before
