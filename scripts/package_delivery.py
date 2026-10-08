"""Create the Windows delivery from an explicitly verified frozen executable.

Only allowlisted source, documentation, receipts and fictional demo resources
enter the archive. User profiles, real PDFs and external corpora are excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from packaging.version import Version


SOURCE_SUFFIXES = {".py", ".json", ".md", ".css", ".js", ".mjs", ".html", ".ps1", ".txt", ".rst", ".toml", ".yaml", ".yml", ".ts", ".tsx", ".lock", ".csv", ".svg", ".woff", ".woff2", ".ttf"}
EXCLUDED_NAMES = {"config.json", "secrets.json", "credentials.json", ".env"}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".git", "node_modules", ".baseline", "baseline", "runtime-cache", "tmp", ".temp", ".codex"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project_path(root: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"Required receipt is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_receipts(exe: Path, receipt_path: Path, build_path: Path, version: str, release: bool = False) -> tuple[dict, str]:
    if not exe.is_file():
        raise SystemExit(f"EXE not found: {exe}. Run scripts/build_exe.ps1 first.")
    digest = sha256(exe)
    receipt, build = read_json(receipt_path), read_json(build_path)
    if receipt.get("exe_sha256") != digest or build.get("sha256") != digest:
        raise SystemExit("EXE SHA-256 does not match acceptance/build receipts; rebuild and reaccept the exact delivery binary.")
    if receipt.get("version") != version or build.get("version") != version:
        raise SystemExit("Project, acceptance and build versions must match.")
    if not build.get("source_sha256") or receipt.get("source_sha256") != build["source_sha256"]:
        raise SystemExit("Acceptance and build source hashes must identify the same frozen source epoch.")
    if build.get("bytes") != exe.stat().st_size:
        raise SystemExit("Build receipt executable size differs from the delivery binary.")
    if receipt.get("status") != "passed_scoped_native_agent_and_exe":
        raise SystemExit("A passing native Harness Agent, real-API and exact frozen-EXE receipt is required; legacy prompt-only receipts cannot approve this delivery.")
    checks = receipt.get("checks", {})
    if (not receipt.get("clean_path_no_python_node") or not receipt.get("profile_isolated")
            or checks.get("secret_scan") != "passed" or not checks.get("user_profile_unchanged")
            or checks.get("capabilities", {}).get("pdf") is not True
            or checks.get("capabilities", {}).get("harness") is not True
            or checks.get("real_api", {}).get("model", {}).get("status") != "success"):
        raise SystemExit("Acceptance receipt lacks required cold-start, PDF, API or credential-isolation checks.")
    native = checks.get("native_agent", {})
    if native.get("status") != "passed" or native.get("native_runtime") is not True or native.get("tool_roundtrip") is not True:
        raise SystemExit("Acceptance receipt must demonstrate the real native Harness tool feedback loop.")
    replays = checks.get("replays", [])
    if len(replays) < 3 or not all(
        r.get("status") == "passed" and r.get("recomputed") and r.get("integrity_ok")
        and r.get("numeric_equal") and r.get("network_calls") == 0 for r in replays
    ):
        raise SystemExit("Acceptance receipt must contain three successful offline kernel recomputations.")
    if release or not Version(version).is_prerelease:
        if release and Version(version).is_prerelease:
            raise SystemExit("Formal release requires a non-prerelease version after all gates pass.")
        gates = receipt.get("release_gates", {})
        if any(gates.get(gate) != "passed" for gate in ("G0", "G1", "G4", "G5", "G6", "G7")):
            raise SystemExit("Formal release requires all declared G0/G1/G4/G5/G6/G7 gates; an RC cannot silently become a full acceptance claim.")
        if any(checks.get(name) != "passed" for name in ("human_gold", "D3_time_holdout", "clean_windows_vm")):
            raise SystemExit("Formal release requires human financial gold, independent time holdout and clean Windows VM acceptance.")
        providers = checks.get("real_providers", {})
        if any(providers.get(name, {}).get("status") != "passed" for name in ("deepseek", "kimi", "openai")):
            raise SystemExit("Formal multi-provider release requires actual DeepSeek/Kimi/OpenAI tool-loop receipts; fixture-only tests cannot substitute.")
    return receipt, digest


def verify_smoke_receipt(path: Path, exe_digest: str) -> dict:
    smoke = read_json(path)
    if smoke.get("status") != "passed" or smoke.get("package", {}).get("exe_sha256") != exe_digest:
        raise SystemExit("EXE smoke receipt must pass and match the exact delivery executable SHA-256.")
    if smoke.get("package", {}).get("mode") != "exe_only":
        raise SystemExit("Only the EXE-only smoke receipt belongs inside the package; final ZIP verification stays external.")
    checks = smoke.get("checks", [])
    if not checks or not all(check.get("status") == "passed" for check in checks):
        raise SystemExit("EXE smoke receipt contains incomplete or failed checks.")
    if smoke.get("user_profile_modified") is not False:
        raise SystemExit("EXE smoke receipt must confirm that the user profile was not modified.")
    return smoke


def verify_current_build_inputs(root: Path, build_path: Path) -> None:
    """The packaged application source must belong to the accepted EXE epoch."""
    if __package__:
        from .build_receipt import build_inputs, canonical_hash, runtime_assets
    else:
        from build_receipt import build_inputs, canonical_hash, runtime_assets

    build = read_json(build_path)
    _, production = runtime_assets(root)
    current = build_inputs(root, production)
    if build.get("inputs") != current or build.get("source_sha256") != canonical_hash(current):
        raise SystemExit("Current application source differs from the accepted build inputs; rebuild and reaccept before packaging.")


def delivery_files(root: Path, exe: Path, receipt_path: Path, build_path: Path, smoke_path: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []

    def add(path: Path, destination: str, production_asset: bool = False) -> None:
        if not path.is_file():
            raise SystemExit(f"Required delivery resource is missing: {path}")
        if path.is_symlink():
            raise SystemExit(f"Symbolic links are not allowed in the delivery: {path}")
        parts = path.relative_to(root).parts if path.is_relative_to(root) else path.parts
        if not production_asset and (path.name.lower() in EXCLUDED_NAMES or any(part.lower() in EXCLUDED_PARTS for part in parts)):
            raise SystemExit(f"Excluded user/configuration or cache resource: {path}")
        files.append((path, destination.replace("\\", "/")))

    add(exe, "MingJing.exe")
    add(receipt_path, "acceptance/exe_acceptance.json")
    add(smoke_path, "acceptance/delivery_smoke.json")
    add(build_path, "MingJing-build.json")
    for relative in (
        "README.md", "docs/README.md", "docs/EXE_DELIVERY.md", "docs/RUNBOOK.md", "docs/THIRD_PARTY.md",
        "docs/THIRD_PARTY_NOTICES.md", "docs/AI_USAGE.md", "docs/REAL_ACCEPTANCE.md",
        "docs/NATIVE_AGENT_ACCEPTANCE.md", "docs/NATIVE_AGENT_FINANCE.md", "docs/FINANCIAL_V2_ACCEPTANCE.md",
        "samples/demo_draft.md", "samples/demo_draft_defined.md", "samples/corpus/facts.json", "samples/corpus/demo_annual.pdf",
    ):
        add(root / relative, relative)
    # Select application source only; build/MingJing is PyInstaller output.
    for directory in ("mingjing", "kernel", "build/mingjing_build", "legacy_tools", "bench", "scripts", "tests", "rulepack", "web", "docs", "frontend"):
        for path in sorted((root / directory).rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            if any(part.lower() in EXCLUDED_PARTS for part in path.relative_to(root).parts):
                continue
            if not path.resolve().is_relative_to(root):
                raise SystemExit(f"Source resource escapes project root: {path}")
            add(path, (Path("source") / path.relative_to(root)).as_posix())
    for relative in ("MingJing.spec", "app_entry.py", "__init__.py", "pyproject.toml", "README.md", "requirements-build.lock", "requirements-build.json"):
        add(root / relative, (Path("source") / relative).as_posix())
    add(root / "scripts/REBUILD_SOURCE.md", "source/REBUILD.md")
    # Keep embedded samples available when rebuilding the corresponding source.
    for relative in ("samples/demo_draft.md", "samples/demo_draft_defined.md", "samples/corpus/facts.json", "samples/corpus/demo_annual.pdf"):
        add(root / relative, (Path("source") / relative).as_posix())
    # Rebuilding the application needs the exact prebuilt official runtime.
    # Use its complete manifest, including required Office node_modules;
    # runtime/tooling and the upstream development dependency tree stay out.
    if __package__:
        from .build_receipt import runtime_assets
    else:
        from build_receipt import runtime_assets
    _, production = runtime_assets(root)
    for path in production:
        add(path, (Path("source") / path.relative_to(root)).as_posix(), production_asset=True)
    for path in sorted((root / "docs/licenses").rglob("*")):
        if path.is_file():
            add(path, path.relative_to(root).as_posix())
    names = [destination for _, destination in files]
    if len(names) != len(set(names)):
        raise SystemExit("Duplicate archive members in delivery allowlist.")
    if set(name for name in names if name.lower().endswith(".pdf")) != {
        "samples/corpus/demo_annual.pdf", "source/samples/corpus/demo_annual.pdf"
    }:
        raise SystemExit("Only the fictional demonstration PDF may enter the delivery.")
    return files


def configured_secret_values(root: Path) -> list[bytes]:
    """Read existing DPAPI credentials for exact-value checks without exposing them."""
    sys.path.insert(0, str(root))
    from mingjing.config import SecretStore

    store = SecretStore()
    return [value.encode("utf-8") for reference in store.references()
            if (value := store.get(reference))]


def scan_for_secrets(path: Path, values: list[bytes]) -> None:
    if not values:
        return
    overlap = max(len(value) for value in values) - 1
    previous = b""
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            data = previous + block
            if any(value in data for value in values):
                raise SystemExit(f"A configured plaintext credential appears in a delivery resource: {path.name}")
            previous = data[-overlap:] if overlap else b""


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", default="dist/MingJing.exe")
    parser.add_argument("--receipt", default="runs/exe-native-acceptance-rc1/acceptance.json")
    parser.add_argument("--build-receipt", default="dist/MingJing-build.json")
    parser.add_argument("--smoke-receipt", default="runs/exe-native-acceptance-rc1/smoke.json")
    parser.add_argument("--out", help="Archive destination; defaults to a timestamped ZIP beside the project.")
    parser.add_argument("--check-only", action="store_true", help="Validate receipts and the exact allowlist without writing an archive.")
    parser.add_argument("--release", action="store_true", help="Require formal release gates; defaults to an explicitly labeled prerelease candidate.")
    args = parser.parse_args()
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    exe, receipt_path, build_path = (project_path(root, value) for value in (args.exe, args.receipt, args.build_receipt))
    receipt, digest = verify_receipts(exe, receipt_path, build_path, version, args.release)
    verify_current_build_inputs(root, build_path)
    smoke_path = project_path(root, args.smoke_receipt)
    verify_smoke_receipt(smoke_path, digest)
    files = delivery_files(root, exe, receipt_path, build_path, smoke_path)
    secret_values = configured_secret_values(root)
    for path, _ in files:
        scan_for_secrets(path, secret_values)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = project_path(root, args.out) if args.out else root.parent / f"MingJing-delivery-{stamp}.zip"
    manifest = {
        "product": "MingJing", "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entrypoint": "MingJing.exe", "sha256": digest,
        "acceptance_status": receipt["status"],
        "acceptance_scope": receipt.get("real_report_scope", ""),
        "delivery_kind": "formal_release" if args.release or not Version(version).is_prerelease else "release_candidate",
        "release_gates": receipt.get("release_gates", {}),
        "not_executed": [name for name in ("human_gold", "D3_time_holdout", "clean_windows_vm") if receipt.get("checks", {}).get(name) != "passed"],
        "exe_smoke_status": "passed",
        "credential_scan": {"status": "passed", "method": "allowlist_and_exact_configured_plaintext_values", "references_checked": len(secret_values)},
        "notes": "External corpora, real PDFs and user profiles are excluded. RC scope must not be represented as full CRRV-Bench, independent gold, clean-VM or unexecuted provider acceptance.",
        "files": [{"path": name, "bytes": path.stat().st_size, "sha256": sha256(path)} for path, name in files],
    }
    if args.check_only:
        print(json.dumps({"status": "validated", "version": version, "exe_sha256": digest, "files": len(files)}, ensure_ascii=False))
        return 0
    if target.exists():
        raise SystemExit(f"Refusing to overwrite an existing delivery: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, name in files:
                archive.write(path, name)
            archive.writestr("MingJing-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        # Recheck each member against the manifest after compression.
        with zipfile.ZipFile(temporary) as archive:
            for entry in manifest["files"]:
                data = archive.read(entry["path"])
                if any(value in data for value in secret_values):
                    raise SystemExit(f"A configured plaintext credential appears in archive member: {entry['path']}")
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise SystemExit(f"Delivery resource changed while packaging: {entry['path']}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print(target)
    print(json.dumps({"version": version, "exe_sha256": digest, "archive_sha256": sha256(target), "files": len(files)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
