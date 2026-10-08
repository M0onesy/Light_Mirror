"""Security settings must keep their JSON types through import and startup."""
import json
from types import SimpleNamespace

import pytest

from mingjing import launcher
from mingjing.config import (AppConfig, ConfigError, ConfigManager, SecretStore,
                             ServerConfig, load_config, validate_config, validate_server_binding)


@pytest.mark.parametrize("section,field", [
    ("server", "bearer_token_required"), ("server", "auto_open_browser"),
    ("security", "allow_external_paths"), ("security", "follow_symlinks"),
    ("llm", "enabled"), ("llm", "verify_tls"), ("harness", "disable_shell"),
    ("harness", "disable_network"), ("logging", "redact_secrets"),
])
@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_import_rejects_truthiness_coercion(section, field, value):
    with pytest.raises(ConfigError, match="boolean"):
        AppConfig.from_dict({section: {field: value}})


@pytest.mark.parametrize("section,field,value", [
    ("server", "port", True), ("server", "port", 8080.0),
    ("run", "timeout_s", "120"), ("security", "max_upload_mb", False),
    ("logging", "retain_days", 90.0), ("llm", "max_tokens", 4096.0),
    ("llm", "temperature", True), ("llm", "temperature", float("nan")),
    ("llm", "temperature", float("inf")),
])
def test_import_rejects_non_integer_limits_and_nonfinite_values(section, field, value):
    with pytest.raises(ConfigError):
        AppConfig.from_dict({section: {field: value}})


@pytest.mark.parametrize("version", [True, 2.0, "2", None])
def test_schema_version_is_not_coerced(version):
    with pytest.raises(ConfigError):
        AppConfig.from_dict({"version": version})


def test_invalid_import_keeps_revision_and_saved_configuration(tmp_path):
    manager = ConfigManager(tmp_path / "config.json", SecretStore(tmp_path / "secrets"))
    manager.save()
    previous, original = manager.public_dict(), manager.path.read_bytes()
    for patch in ({"server": {"host": "0.0.0.0", "bearer_token_required": 0}},
                  {"security": {"allow_external_paths": "false"}}, {"version": 3}, {"revision": True}):
        with pytest.raises(ConfigError):
            manager.update_from_dict(patch, expected_revision=1)
        assert manager.public_dict() == previous
        assert manager.path.read_bytes() == original
    with pytest.raises(ConfigError):
        manager.update_from_dict({}, expected_revision=1.0)


def test_loading_invalid_saved_security_fails_closed(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"server": {"host": "0.0.0.0", "bearer_token_required": False}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="non-loopback"):
        load_config(path)


def test_direct_config_validation_also_rejects_mutated_boolean():
    config = AppConfig()
    config.server.host = "0.0.0.0"
    config.server.bearer_token_required = 0
    assert any("boolean" in issue for issue in validate_config(config))


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1"])
def test_effective_nonloopback_bind_cannot_override_authentication(host):
    with pytest.raises(ConfigError, match="non-loopback"):
        validate_server_binding(host, ServerConfig(), lambda _: "")


@pytest.mark.parametrize("token", [None, "", " leading", "trailing ", "内部口令", "has\nnewline"])
def test_enabled_authentication_requires_usable_header_token(token):
    with pytest.raises(ConfigError):
        validate_server_binding("127.0.0.1", ServerConfig(bearer_token_required=True), lambda _: token)


def test_nonloopback_bind_with_explicit_authentication_is_supported():
    validate_server_binding("0.0.0.0", ServerConfig(bearer_token_required=True), lambda _: "test-only-access-token", port=8080)


def test_launcher_rejects_nonloopback_override_before_backend_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("MINGJING_NO_BROWSER", "1")
    invoked = []
    monkeypatch.setattr(launcher, "_server_factory", lambda: invoked.append(True))
    assert launcher.run(home=str(tmp_path), host="0.0.0.0", open_browser=False) == 1
    assert not invoked
    assert "non-loopback" in (tmp_path / "logs" / "launcher.log").read_text(encoding="utf-8")
    assert not (tmp_path / "workspace" / "server.json").exists()


def test_tls_false_is_rejected_before_native_or_python_adapter_runs():
    cfg = AppConfig.from_dict({"providers": {"local": {"base_url": "http://127.0.0.1:8080", "verify_tls": False}}})
    assert any("verify_tls" in issue for issue in validate_config(cfg))


def test_nonfinite_compat_parameters_cannot_enter_configuration():
    with pytest.raises(ConfigError, match="non-finite"):
        AppConfig.from_dict({"providers": {"local": {"models": [{"id": "fixture", "compat": {"topP": float("nan")}}]}}})
