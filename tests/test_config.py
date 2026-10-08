import json
from pathlib import Path

import pytest

from mingjing.config import (
    AppConfig,
    ConfigError,
    ConfigManager,
    RevisionConflict,
    SecretStore,
    validate_path,
)


def test_config_roundtrip_and_revision(tmp_path: Path):
    manager = ConfigManager(tmp_path / "config.json", SecretStore(tmp_path / "secrets"))
    assert manager.public_dict()["revision"] == 0
    manager.update_from_dict({"run": {"company": "示例公司"}}, expected_revision=0)
    assert manager.public_dict()["run"]["company"] == "示例公司"
    with pytest.raises(RevisionConflict):
        manager.update_from_dict({"run": {"company": "过期写入"}}, expected_revision=0)


def test_secret_is_not_in_public_config(tmp_path: Path):
    manager = ConfigManager(tmp_path / "config.json", SecretStore(tmp_path / "secrets"))
    manager.set_secret("llm_api_key", "test-token")
    manager.save(expected_revision=0)
    assert manager.get_secret("llm_api_key") == "test-token"
    assert "test-token" not in str(manager.public_dict())
    assert "test-token" not in (tmp_path / "config.json").read_text(encoding="utf-8")


def test_validate_path_root_and_kind(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("ok", encoding="utf-8")
    assert validate_path(source, allowed_roots=[tmp_path], must_exist=True, kind="file") == source.resolve()
    with pytest.raises(ConfigError):
        validate_path(tmp_path.parent / "outside.txt", allowed_roots=[tmp_path])


def test_invalid_patch_cannot_mutate_live_or_saved_config(tmp_path: Path):
    manager = ConfigManager(tmp_path / "config.json", SecretStore(tmp_path / "secrets"))
    manager.save(expected_revision=0)
    previous = manager.public_dict()
    saved = manager.path.read_bytes()
    with pytest.raises(ConfigError):
        manager.update_from_dict({"llm": {"thinking": "invalid"}, "run": {"company": "错误更新"}}, expected_revision=1)
    assert manager.public_dict() == previous
    assert manager.path.read_bytes() == saved


@pytest.mark.parametrize("version", [1, 2])
def test_missing_followup_token_limit_uses_default_without_overriding_other_limits(version):
    config = AppConfig.from_dict({"version": version, "agent": {"followup_budget": {
        "max_model_calls": 18, "max_tool_calls": 100, "max_runtime_s": 600,
    }}})
    assert config.to_dict()["agent"]["followup_budget"] == {
        "max_model_calls": 18, "max_tool_calls": 100,
        "max_total_tokens": 150000, "max_runtime_s": 600,
    }


@pytest.mark.parametrize("token_limit", [None, 24000, 128000])
def test_loading_v2_followup_budget_preserves_saved_bytes_and_explicit_limits(tmp_path, token_limit):
    target = tmp_path / "config.json"
    followup = {"max_model_calls": 18}
    if token_limit is not None:
        followup["max_total_tokens"] = token_limit
    target.write_text(json.dumps({"version": 2, "revision": 8,
        "agent": {"followup_budget": followup}}), encoding="utf-8")
    before = target.read_bytes()
    manager = ConfigManager(target, SecretStore(tmp_path / "secrets"))
    budget = manager.public_dict()["agent"]["followup_budget"]
    assert budget["max_total_tokens"] == (150000 if token_limit is None else token_limit)
    assert budget["max_model_calls"] == 18
    assert manager.public_dict()["revision"] == 8
    assert target.read_bytes() == before


def test_followup_budget_patch_persists_and_keeps_frozen_task_config(tmp_path):
    manager = ConfigManager(tmp_path / "config.json", SecretStore(tmp_path / "secrets"))
    frozen = manager.snapshot()
    previous = manager.public_dict()["agent"]["followup_budget"]
    manager.update_from_dict({"agent": {"followup_budget": {"max_total_tokens": 128000}}}, expected_revision=0)
    assert frozen["agent"]["followup_budget"]["max_total_tokens"] == 150000
    assert manager.public_dict()["agent"]["followup_budget"] == {
        **previous, "max_total_tokens": 128000,
    }
    restored = ConfigManager(manager.path, manager.secret_store)
    assert restored.public_dict()["agent"]["followup_budget"] == manager.public_dict()["agent"]["followup_budget"]
    assert restored.snapshot()["config_sha256"] != frozen["config_sha256"]
    with pytest.raises(RevisionConflict):
        restored.update_from_dict({"agent": {"followup_budget": {"max_total_tokens": 24000}}}, expected_revision=0)


def test_imported_explicit_followup_budget_and_unrelated_patch_remain_authoritative(tmp_path):
    manager = ConfigManager(tmp_path / "config.json", SecretStore(tmp_path / "secrets"))
    imported = AppConfig().to_dict()
    imported["agent"]["followup_budget"]["max_total_tokens"] = 24000
    manager.update_from_dict(imported, expected_revision=0)
    manager.update_from_dict({"run": {"company": "示例公司"}}, expected_revision=1)
    restored = ConfigManager(manager.path, manager.secret_store)
    assert restored.public_dict()["agent"]["followup_budget"]["max_total_tokens"] == 24000


def test_disabled_web_config_does_not_inherit_cli_token(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MINGJING_LLM_API_KEY", "inherited-secret")
    monkeypatch.setenv("MINGJING_LLM_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("MINGJING_LLM_MODEL", "inherited-model")
    manager = ConfigManager(tmp_path / "config.json", SecretStore(tmp_path / "secrets"))
    assert manager.public_dict()["llm"]["enabled"] is False
    assert manager.public_dict()["llm"]["model"] == ""


def test_external_symlink_is_rejected_before_resolution(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "report.txt").write_text("financial report", encoding="utf-8")
    linked = tmp_path / "link"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Windows profile cannot create symbolic links")
    with pytest.raises(ConfigError, match="symbolic links"):
        validate_path(linked / "report.txt", must_exist=True, kind="file")
    assert validate_path(linked / "report.txt", must_exist=True, kind="file", follow_symlinks=True) == target / "report.txt"


def test_legacy_v2_harness_paths_remain_inactive_and_unchanged_on_load(tmp_path):
    path = tmp_path / "config.json"
    original = {"version": 2, "revision": 8, "harness": {"enabled": True,
        "executable": "C:/old-runtime/DeepSeek Harness.exe", "profile": "sdk-minimal"}}
    path.write_text(json.dumps(original), encoding="utf-8")
    before = path.read_bytes()
    manager = ConfigManager(path, SecretStore(tmp_path / "secrets"))
    harness = manager.public_dict()["harness"]
    assert harness["runtime_mode"] == "embedded"
    assert harness["executable"] == original["harness"]["executable"]
    assert harness["profile"] == "sdk-minimal"
    assert path.read_bytes() == before


def test_external_harness_selection_is_explicit_and_validated(tmp_path):
    manager = ConfigManager(tmp_path / "config.json", SecretStore(tmp_path / "secrets"))
    manager.update_from_dict({"harness": {"enabled": True, "runtime_mode": "embedded"}})
    assert manager.public_dict()["harness"]["executable"] == ""
    with pytest.raises(ConfigError, match="external runtime"):
        manager.update_from_dict({"harness": {"runtime_mode": "external"}})
    manager.update_from_dict({"harness": {"runtime_mode": "external", "executable": "C:/advanced/dsh.exe"}})
    assert manager.public_dict()["harness"]["runtime_mode"] == "external"
    saved = manager.path.read_bytes()
    for invalid in ("desktop", [], 7):
        with pytest.raises(ConfigError, match="runtime_mode"):
            manager.update_from_dict({"harness": {"runtime_mode": invalid}})
        assert manager.path.read_bytes() == saved
