import json
import subprocess
from pathlib import Path

import pytest

from scripts.plan_finance_runtime import Registry, package_name, profile_roots, supported, validate_candidate_inventory


def test_profile_discovery_preserves_financial_rows_and_owned_plugin():
    root = Path(__file__).resolve().parents[1]
    rows = profile_roots(root / "mingjing/harness_client.py")
    assert len(rows) == 17
    assert rows[-1] == {"id": "mj-finance-rpc", "package": None, "owned_plugin": True}
    assert not any("office" in (row["package"] or "") for row in rows)


def test_profile_discovery_refuses_new_dynamic_packages(tmp_path):
    source = tmp_path / "profile.py"
    source.write_text("def _prepare_profile():\n    rows = [{'id':'unknown', 'name':get_package()}]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dynamic profile package"):
        profile_roots(source)


def test_target_rules_use_declared_platform_metadata():
    assert supported(["win32", "!linux"], "win32")
    assert not supported(["win32", "!win32"], "win32")
    assert not supported(["arm64"], "x64")
    assert package_name("@deepseek-ai/dsh/lib/bin.js") == "@deepseek-ai/dsh"
    assert package_name("node:fs") is None


def registry_fixture(tmp_path, packages):
    registry = Registry.__new__(Registry)
    registry.upstream = tmp_path
    registry.carrier = tmp_path / "carrier/node_modules"
    registry.workspaces = {}
    registry.lock_metadata = {}
    for name, data in packages.items():
        path = tmp_path / name / "package.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"name": name, "version": "1.0.0", **data}), encoding="utf-8")
        registry.workspaces[name] = (path, json.loads(path.read_text(encoding="utf-8")))
    return registry


def test_closure_keeps_required_peers_and_filters_only_locked_platform_packages(tmp_path):
    registry = registry_fixture(tmp_path, {
        "root": {"dependencies": {"runtime": "1.0.0"}, "optionalDependencies": {"native-other": "1.0.0", "missing": "1.0.0"}},
        "runtime": {"peerDependencies": {"cordis": "1.0.0", "optional-provider": "1.0.0"},
                    "peerDependenciesMeta": {"optional-provider": {"optional": True}}},
        "cordis": {},
    })
    registry.lock_metadata = {"native-other": [("native-other@1.0.0", {"os": ["linux"], "cpu": ["x64"]})]}
    result = registry.closure(["root"])
    assert {package["name"] for package in result["packages"]} == {"root", "runtime", "cordis"}
    assert result["target_exclusions"][0]["package"] == "native-other"
    assert [entry["package"] for entry in result["unresolved"]] == ["missing"]


def test_graph_overlay_does_not_change_source_manifest(tmp_path):
    registry = registry_fixture(tmp_path, {"dsh": {"dependencies": {"office": "1.0.0", "boot": "1.0.0"}}, "office": {}, "boot": {}})
    path = registry.workspaces["dsh"][0]
    original = path.read_bytes()
    plan = registry.closure(["dsh"], {"dsh": {"dependencies": {"boot": "1.0.0"}}})
    assert {package["name"] for package in plan["packages"]} == {"dsh", "boot"}
    assert path.read_bytes() == original


def test_inventory_refuses_missing_and_forbidden_packages():
    plan = {"candidate": {"packages": [{"name": "core", "version": "1.0.0"}]}}
    result = validate_candidate_inventory(plan, [{"name": "@deepseek-ai/libreoffice-kit", "version": "1.0.0"}])
    assert result["status"] == "failed"
    assert result["missing"] == [("core", "1.0.0")]
    assert result["forbidden"] == ["@deepseek-ai/libreoffice-kit"]
    assert result["runtime_acceptance"] == "not_executed"


@pytest.mark.parametrize("args,selector", [(["--profile", "web"], None), (["--profile", "mingjing", "--patch", "x.yml"], None),
                                          (["plugin", "--profile", "mingjing", "add", "evil"], None),
                                          (["--profile", "mingjing"], "DSH_SUBPROCESS_RUNNER")])
def test_finance_carrier_rejects_unapproved_entry_before_loading_dependencies(args, selector):
    import os
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("Development Node unavailable")
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "DSH_HOME": str(root / "runs/unused-carrier-test")}
    for name in ("DSH_SUBPROCESS_RUNNER", "DSH_PTC_RUNTIME_NODE"):
        env.pop(name, None)
    if selector:
        env[selector] = "1"
    result = subprocess.run([node, str(root / "runtime/finance-carrier/bootstrap.mjs"), *args],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "MingJing finance carrier" in result.stderr
    assert "ERR_MODULE_NOT_FOUND" not in result.stderr
