"""Inspect the locked Harness source and plan its finance-only runtime closure.

This command writes small metadata files only. It never deploys packages, copies
native assets, installs dependencies, builds an executable or changes the locked
upstream checkout. Its candidate remains a proposal until runtime tests pass.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UPSTREAM = ROOT / "vendor/harness"
CLI_RUNTIME_FILES = (
    "apps/cli/src/bin.ts", "apps/cli/src/args.ts",
    "apps/cli/src/startup-diagnostics.ts", "apps/cli/src/profile-boot.ts",
    "apps/cli/src/process-shutdown.ts",
)
IMPORT_SPECIFIER = re.compile(
    r"(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+)[\"']([^\"']+)[\"']"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_name(specifier: str) -> str | None:
    if specifier.startswith((".", "/", "node:", "file:", "#")):
        return None
    return "/".join(specifier.split("/")[:2]) if specifier.startswith("@") else specifier.split("/")[0]


def module_imports(path: Path) -> set[str]:
    return {name for specifier in IMPORT_SPECIFIER.findall(path.read_text(encoding="utf-8"))
            if (name := package_name(specifier)) is not None}


def profile_roots(path: Path) -> list[dict]:
    """Read literal plugin row names from the actual profile builder, never execute it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_prepare_profile":
            continue
        for statement in ast.walk(node):
            if not isinstance(statement, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == "rows" for target in statement.targets
            ):
                continue
            if not isinstance(statement.value, ast.List):
                raise ValueError("Financial profile rows are no longer a literal list; update closure discovery")
            for row in statement.value.elts:
                if not isinstance(row, ast.Dict):
                    raise ValueError("Financial profile row is not a literal dictionary")
                values = {key.value: value for key, value in zip(row.keys, row.values)
                          if isinstance(key, ast.Constant) and isinstance(key.value, str)}
                row_id, name = values.get("id"), values.get("name")
                if not isinstance(row_id, ast.Constant) or not isinstance(row_id.value, str):
                    raise ValueError("Financial profile row has no literal id")
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    rows.append({"id": row_id.value, "package": name.value})
                elif row_id.value == "mj-finance-rpc":
                    rows.append({"id": row_id.value, "package": None, "owned_plugin": True})
                else:
                    raise ValueError("Unexpected dynamic profile package; update closure discovery")
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Financial profile discovery was empty or duplicated")
    return rows


def supported(values: list[str] | None, target: str) -> bool:
    if not values:
        return True
    if f"!{target}" in values:
        return False
    positive = [item for item in values if not item.startswith("!")]
    return not positive or target in positive


class Registry:
    """Resolve source workspace manifests and the already installed npm metadata."""

    def __init__(self, upstream: Path, snapshot: dict):
        import yaml  # Development tooling only; no addition to the application runtime.

        self.upstream = upstream.resolve()
        self.workspaces: dict[str, tuple[Path, dict]] = {}
        self.snapshot = {entry["path"]: entry for entry in snapshot["files"]}
        for relative in sorted(self.snapshot):
            if not relative.endswith("/package.json") or relative.startswith(("python/", "snapshots/", "benchmarks/")):
                continue
            path = self.upstream / relative
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            name = data.get("name")
            if not name:
                continue
            if name in self.workspaces:
                raise ValueError(f"Duplicate workspace package in locked snapshot: {name}")
            self.check_source(path)
            self.workspaces[name] = (path, data)
        self.carrier = self.upstream / "python/sdk-runtime/src/deepseek_harness_runtime/runtime/node/node_modules"
        lock_path = self.upstream / "pnpm-lock.yaml"
        if digest(lock_path) != snapshot["pnpm_lock_sha256"]:
            raise ValueError("The pnpm lock differs from the pinned archive")
        locked = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
        self.lock_metadata: dict[str, list[tuple[str, dict]]] = {}
        for name_version, metadata in locked["packages"].items():
            name = name_version.rsplit("@", 1)[0]
            self.lock_metadata.setdefault(name, []).append((name_version, metadata))

    def check_source(self, path: Path) -> None:
        relative = path.relative_to(self.upstream).as_posix()
        expected = self.snapshot.get(relative)
        if expected is None or digest(path) != expected["sha256"]:
            raise ValueError(f"Source input differs from the pinned archive: {relative}")

    def resolve(self, name: str, owner: Path | None) -> tuple[Path, dict] | None:
        if name in self.workspaces:
            return self.workspaces[name]
        searches = []
        if owner is not None:
            # Node resolves a pnpm-linked package from its real package
            # directory. Looking up through the link's workspace ancestors
            # can silently select an incompatible native optional package.
            owner = owner.resolve(strict=True)
            searches.extend(parent / "node_modules" / name / "package.json" for parent in [owner.parent, *owner.parents])
        searches.extend([self.carrier / name / "package.json", self.upstream / "node_modules" / name / "package.json"])
        for candidate in searches:
            if candidate.is_file():
                return candidate, json.loads(candidate.read_text(encoding="utf-8"))
        return None

    def closure(self, roots: list[str], overrides: dict[str, dict] | None = None) -> dict:
        overrides = overrides or {}
        queue = deque((name, None, None, "root") for name in sorted(set(roots)))
        packages, edges, unresolved, target_exclusions = {}, [], [], []
        while queue:
            name, owner, parent_key, kind = queue.popleft()
            resolved = self.resolve(name, owner)
            if resolved is None:
                locked = self.lock_metadata.get(name, [])
                # A missing optional platform artifact is excluded only from
                # declared os/cpu fields in every locked version of that name.
                # Package-name suffixes do not establish platform compatibility.
                if kind == "optional" and locked and all(
                    not supported(metadata.get("os"), "win32") or not supported(metadata.get("cpu"), "x64")
                    for _, metadata in locked
                ):
                    target_exclusions.append({"package": name, "from": parent_key, "kind": kind,
                                              "metadata_source": "pinned_pnpm_lock",
                                              "locked_versions": [entry for entry, _ in locked]})
                    continue
                unresolved.append({"package": name, "from": parent_key, "kind": kind})
                continue
            path, data = resolved
            if not supported(data.get("os"), "win32") or not supported(data.get("cpu"), "x64"):
                target_exclusions.append({"package": name, "from": parent_key, "kind": kind,
                                          "os": data.get("os"), "cpu": data.get("cpu")})
                continue
            real = path.resolve()
            key = f"{data['name']}@{data.get('version', 'unknown')}:{real.relative_to(self.upstream).as_posix()}"
            if parent_key:
                edges.append({"from": parent_key, "to": key, "kind": kind, "requested_package": name})
            if key in packages:
                continue
            relative = path.relative_to(self.upstream).as_posix()
            packages[key] = {"name": data["name"], "version": data.get("version"), "manifest": relative,
                             "manifest_sha256": digest(path), "workspace": name in self.workspaces,
                             "license": data.get("license"), "root": parent_key is None}
            effective = {**data, **overrides.get(name, {})}
            optional = effective.get("optionalDependencies", {})
            deps = {**effective.get("dependencies", {}), **optional}
            for dependency in sorted(deps):
                queue.append((dependency, path, key, "optional" if dependency in optional else "dependency"))
            for dependency in sorted(effective.get("peerDependencies", {})):
                meta = effective.get("peerDependenciesMeta", {}).get(dependency, {})
                if not meta.get("optional"):
                    queue.append((dependency, path, key, "peer"))
        return {"packages": list(packages.values()), "edges": edges,
                "unresolved": unresolved, "target_exclusions": target_exclusions,
                "package_count": len(packages),
                "workspace_count": sum(item["workspace"] for item in packages.values())}


def validate_candidate_inventory(plan: dict, package_manifests: list[dict]) -> dict:
    """Check a future symlink-free carrier's manifests against the proposed graph.

    Runtime behavior, native entry dependencies and licenses require additional
    tests. Passing this inventory check is not executable acceptance.
    """
    expected = {(item["name"], item["version"]) for item in plan["candidate"]["packages"]}
    actual = {(item.get("name"), item.get("version")) for item in package_manifests}
    forbidden_patterns = ("libreoffice", "dsh-skill-office", "node-pty", "ripgrep", "dsh-web-frontend")
    forbidden = sorted(name for name, _ in actual if name and any(part in name for part in forbidden_patterns))
    missing, unexpected = sorted(expected - actual), sorted(actual - expected)
    return {"status": "passed" if not (missing or unexpected or forbidden) else "failed",
            "missing": missing, "unexpected": unexpected, "forbidden": forbidden,
            "runtime_acceptance": "not_executed"}


def create_plan(upstream: Path) -> dict:
    from scripts.upstream_source import verify_upstream
    verify_upstream(upstream)
    snapshot_path = ROOT / "runtime/harness/source_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    registry = Registry(upstream, snapshot)
    for relative in CLI_RUNTIME_FILES:
        registry.check_source(upstream / relative)
    registry.check_source(upstream / "python/sdk-runtime/package.json")
    profile_path = ROOT / "mingjing/harness_client.py"
    plugin_path = ROOT / "runtime/finance-plugin/index.mjs"
    bootstrap_path = ROOT / "runtime/finance-carrier/bootstrap.mjs"
    rows = profile_roots(profile_path)
    cli_imports = set().union(*(module_imports(upstream / relative) for relative in CLI_RUNTIME_FILES))
    plugin_imports = module_imports(plugin_path)
    roots = {row["package"] for row in rows if row["package"]} | plugin_imports | cli_imports | {"@deepseek-ai/dsh"}
    dsh_path, original_dsh = registry.workspaces["@deepseek-ai/dsh"]
    declared = original_dsh["dependencies"]
    # Keep the upstream boot modules byte-for-byte. Its other profile bundles
    # are dependency declarations, not financial runtime requirements.
    overlay_dependencies = {}
    # The official out-of-tree profile resolver builds its shared fallback
    # table from the installation anchor's declared dependencies. Finance
    # plugins must be present there, even when the CLI does not import them.
    for name in sorted(roots - {"@deepseek-ai/dsh"}):
        resolved = registry.resolve(name, dsh_path)
        if name in declared:
            overlay_dependencies[name] = declared[name]
        elif resolved is not None:
            overlay_dependencies[name] = "workspace:^" if name in registry.workspaces else resolved[1]["version"]
        else:
            raise ValueError(f"Required CLI package cannot be resolved: {name}")
    candidate = registry.closure(sorted(roots), {"@deepseek-ai/dsh": {"dependencies": overlay_dependencies}})
    official_root = json.loads((upstream / "python/sdk-runtime/package.json").read_text(encoding="utf-8"))
    official = registry.closure(list(official_root["dependencies"]))
    blockers = [{"code": "CANDIDATE_NOT_STAGED", "reason": "No packages or executable were copied or built"},
                {"code": "NATIVE_AND_DYNAMIC_IMPORT_TESTS_PENDING", "reason": "Manifest closure does not prove all runtime imports"},
                {"code": "CLEAN_WINDOWS_ACCEPTANCE_PENDING", "reason": "No clean Windows cold-start, resume or replay evidence yet"}]
    if candidate["unresolved"]:
        blockers.append({"code": "UNRESOLVED_DEPENDENCIES", "count": len(candidate["unresolved"])})
    forbidden = [item["name"] for item in candidate["packages"] if any(
        token in item["name"] for token in ("libreoffice", "dsh-skill-office", "node-pty", "ripgrep", "dsh-web-frontend"))]
    if forbidden:
        blockers.append({"code": "NON_FINANCIAL_DEPENDENCIES_RETAINED", "packages": sorted(forbidden)})
    baseline_path = ROOT / "runtime/harness/manifest.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    grouped = {}
    for item in baseline["files"]:
        name = "office" if "-office/" in item["path"] else "licenses" if "/licenses/" in item["path"] else "native_and_plugin"
        grouped[name] = grouped.get(name, 0) + item["bytes"]
    return {"format_version": 1, "status": "candidate_plan_not_released", "platform": "win32-x64",
            "upstream_version": snapshot["upstream_version"], "git_commit": snapshot.get("git_commit"),
            "source_sha256": snapshot["source_sha256"], "source_snapshot_sha256": digest(snapshot_path),
            "profile_builder_sha256": digest(profile_path), "finance_plugin_sha256": digest(plugin_path),
            "candidate_bootstrap_sha256": digest(bootstrap_path),
            "profile_rows": rows, "cli_runtime_sources": list(CLI_RUNTIME_FILES),
            "candidate_root_dependencies": sorted(roots),
            "dsh_packaging_overlay": {"upstream_manifest_sha256": digest(dsh_path),
                                      "dependencies": overlay_dependencies,
                                      "unmodified_runtime_code": True,
                                      "allowed_invocation": ["--profile", "mingjing"],
                                      "removed_dependency_declarations": sorted(set(declared) - set(overlay_dependencies))},
            "official": official, "candidate": candidate,
            "baseline_asset_bytes": grouped, "candidate_executable_bytes": None,
            "blockers": blockers,
            "required_runtime_checks": [
                "Reject alternate profiles, overlays, plugin installation and subprocess selector environments",
                "Launch initialize/runtime.describe and assert finance profile/forbidden services",
                "Open A/B isolated sessions and execute at least three dependent financial tools",
                "Exercise DeepSeek, Chat Completions, Responses and Anthropic adapter fixtures",
                "Cancel, crash/restart, resume and kernel/agent offline replay",
                "Verify every native/resource read resolves inside the deployed carrier",
                "Verify original dependency licenses and one Cordis service identity",
                "Cold start on clean Windows 11 without Python, Node or pnpm",
            ]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    parser.add_argument("--out", type=Path, default=ROOT / "evidence/runtime-closure-plan")
    args = parser.parse_args()
    plan = create_plan(args.upstream.resolve())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "dsh-packaging-overlay.json").write_text(json.dumps(plan["dsh_packaging_overlay"], indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": plan["status"], "official_packages": plan["official"]["package_count"],
                      "candidate_packages": plan["candidate"]["package_count"],
                      "candidate_unresolved": len(plan["candidate"]["unresolved"]),
                      "blockers": plan["blockers"], "out": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
