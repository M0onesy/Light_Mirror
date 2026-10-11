"""Create the full dev3 source/runtime package only after the owner freezes it.

Unlike the <=30MB review package, this includes locked vendor source and the
entire staged production carrier, including its production node_modules.
No packaging is performed without --freeze-receipt and --frozen.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT
DEFAULT_OUT = ROOT / "artifacts" / "source"
VERSION = "1.0.0.dev3"
SOURCE_DIRECTORIES = {
    "bench", "docs", "frontend", "kernel", "legacy_tools", "mingjing",
    "requirements", "rulepack", "runtime", "samples", "scripts", "tests", "vendor", "web",
}
SOURCE_FILES = {
    ".gitattributes", ".gitignore", "app_entry.py", "LICENSE", "MingJing.spec",
    "PROJECT_README.md", "pyproject.toml", "pytest.ini", "README.md",
    "requirements-build.json", "requirements-build.lock", "__init__.py",
}
EXCLUDED_DIRECTORIES = {
    ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".tox",
    ".venv", "venv", "__pycache__", ".tmp_sample_render", ".aud_finance", ".audit_finance", "runs", "profile",
    "profiles", "cache", "caches", "logs", "build", "dist", "artifacts", "evidence", "data",
}
EXCLUDED_ROOT_FILES = {"core-review-manifest.json"}
CARRIER_PREFIX = ("runtime", "finance-carrier", "staged")
SOURCE_CONTEXTS = {"src", "tests", "test", "snapshots", "fixtures", "examples", "packages"}
ALWAYS_GENERATED = {".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".tox",
                    ".venv", "venv", "__pycache__", ".tmp_sample_render", ".aud_finance", ".audit_finance", "node_modules"}


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def is_carrier(relative):
    return relative.parts[:len(CARRIER_PREFIX)] == CARRIER_PREFIX


def include(relative, directory=False):
    """Keep production carrier complete; filter developer artifacts elsewhere."""
    if is_carrier(relative):
        return True, "production carrier: complete frozen closure"
    parts = tuple(x.casefold() for x in relative.parts)
    for index, part in enumerate(parts):
        # A source namespace/fixture named profiles, cache, build, or dist is
        # retained. It is not a user's mutable profile or developer output.
        source_context = bool(set(parts[:index]) & SOURCE_CONTEXTS)
        if part in ALWAYS_GENERATED or (part in EXCLUDED_DIRECTORIES and not source_context):
            return False, "developer state/cache/dependency directory"
    name = relative.name.casefold()
    if not directory and (name.startswith("~$") or name.endswith((".pyc", ".pyo"))):
        return False, "temporary or compiled Python artifact"
    if name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.template"}):
        return False, "local environment configuration"
    return True, "whitelisted project source/resource"


def inventory(source):
    files, exclusions = [], []
    unknown = []
    for entry in source.iterdir():
        if entry.is_dir():
            if entry.name.casefold() in EXCLUDED_DIRECTORIES:
                exclusions.append({"path": entry.name + "/", "reason": "root developer state/cache/acceptance output"})
            elif entry.name not in SOURCE_DIRECTORIES:
                unknown.append(entry.name)
        elif entry.name in EXCLUDED_ROOT_FILES:
            exclusions.append({"path": entry.name, "reason": "generated review manifest from a different package scope"})
        elif entry.name not in SOURCE_FILES:
            if not entry.name.startswith("~$") and not entry.name.endswith((".pyc", ".pyo")):
                unknown.append(entry.name)
    if unknown:
        raise ValueError("Unknown root paths require an explicit whitelist decision: " + ", ".join(sorted(unknown)))
    for root_file in sorted(SOURCE_FILES):
        path = source / root_file
        if path.exists():
            if is_link(path):
                raise ValueError(f"Root source file is a link: {path}")
            files.append(path)
    for directory in sorted(SOURCE_DIRECTORIES):
        base = source / directory
        if not base.exists():
            continue
        if is_link(base):
            raise ValueError(f"Source root directory is a link: {base}")
        for current, directories, filenames in os.walk(base, followlinks=False):
            current = Path(current)
            kept = []
            for dirname in sorted(directories):
                path = current / dirname
                relative = path.relative_to(source)
                allowed, reason = include(relative, directory=True)
                if not allowed:
                    exclusions.append({"path": relative.as_posix() + "/", "reason": reason})
                elif is_link(path):
                    raise ValueError(f"Materialize source/runtime directory link before freezing: {relative}")
                else:
                    kept.append(dirname)
            directories[:] = kept
            for filename in sorted(filenames):
                path = current / filename
                relative = path.relative_to(source)
                allowed, reason = include(relative)
                if not allowed:
                    exclusions.append({"path": relative.as_posix(), "reason": reason})
                    continue
                if is_link(path):
                    raise ValueError(f"Materialize source/runtime file link before freezing: {relative}")
                files.append(path)
    records = [{"path": path.relative_to(source).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)}
               for path in sorted(files)]
    return records, exclusions


def digest_records(records):
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def credential_scan(source, records):
    """Scan every included file without exposing or migrating user secrets."""
    sys.path.insert(0, str(source))
    from scripts.create_core_review import configured_secret_values, scan_bytes
    from mingjing.config import app_root
    profile = app_root()
    protected = [profile / name for name in ("config.json", "secrets.json", "secrets.key", "config.v1.bak.json")]
    state = lambda: {str(path): sha(path) if path.is_file() else None for path in protected}
    before = state()
    if (profile / "secrets.json").is_file():
        backend = json.loads((profile / "secrets.json").read_text("utf-8")).get("backend")
        if backend != "dpapi":
            raise ValueError("Migrate saved secrets in the UI before read-only source packaging")
    values = configured_secret_values(source)
    if before != state():
        raise ValueError("Credential scan changed original user profile")
    overlap = max([len(value) for value in values] + [80])
    for record in records:
        path = source / record["path"]
        if path.suffix.casefold() == ".docx":
            # DOCX must be scanned as one ZIP so compressed/split XML is decoded.
            scan_bytes(path.read_bytes(), values, record["path"])
            continue
        previous = b""
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                scan_bytes(previous + block, values, record["path"])
                previous = (previous + block)[-overlap:]
    if before != state():
        raise ValueError("Original user profile changed during source scan")
    return values, {"status": "passed", "files_scanned": len(records),
                    "references_checked": len(values), "user_profile_unchanged": True,
                    "method": "allowlist_exact_configured_values_private_key_blocks_and_decoded_DOCX"}


def verify_carrier(source, records):
    carrier = source.joinpath(*CARRIER_PREFIX)
    manifest_path = carrier / "carrier_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Full native package requires the staged carrier manifest")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    record_map = {record["path"]: record for record in records}
    for record in manifest.get("files", []):
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe carrier manifest path")
        package_key = "/".join(CARRIER_PREFIX + relative.parts)
        observed = record_map.get(package_key)
        if observed is None or observed["sha256"] != record["sha256"] or observed["bytes"] != record["bytes"]:
            raise ValueError("Carrier closure differs from its frozen manifest: " + package_key)
    if not manifest.get("files") or not any(x["path"].startswith("/".join(CARRIER_PREFIX) + "/node_modules/") for x in records):
        raise ValueError("Carrier production dependencies are absent")
    if not (source / "vendor" / "harness").is_dir():
        raise ValueError("Locked Harness source is absent")
    return {"path": manifest_path.relative_to(source).as_posix(), "sha256": sha(manifest_path),
            "declared_status": manifest.get("status"), "manifest_files_verified": len(manifest["files"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--freeze-receipt", type=Path, required=True,
                        help="Owner-created final source freeze receipt; copied only as a hash reference")
    parser.add_argument("--expected-source-digest", help="Optional digest_records value from a prior freeze inventory")
    parser.add_argument("--frozen", action="store_true", help="Owner has frozen code/docs/resources and authorized packaging")
    args = parser.parse_args()
    if not args.frozen:
        parser.error("Packaging is disabled until the owner freezes the source and supplies --frozen")
    source, out = args.source.resolve(), args.out.resolve()
    freeze = args.freeze_receipt.resolve()
    if not source.is_dir() or not freeze.is_file():
        raise ValueError("Source and final freeze receipt must exist")
    # Require readable JSON and record its bytes without editing its claims.
    json.loads(freeze.read_text("utf-8"))
    artifact_root = source / "artifacts" / "source"
    managed_output = out == artifact_root or artifact_root in out.parents
    if (source == out or source in out.parents) and not managed_output:
        raise ValueError("Output inside the source tree must stay under artifacts/source")
    records, exclusions = inventory(source)
    source_digest = digest_records(records)
    if args.expected_source_digest and args.expected_source_digest != source_digest:
        raise ValueError("Source differs from the owner-provided freeze digest")
    carrier = verify_carrier(source, records)
    values, scan = credential_scan(source, records)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package_name = f"mingjing-{VERSION}-full-{stamp}"
    package_root = out / package_name
    package_root.mkdir(parents=True, exist_ok=False)
    for record in records:
        destination = package_root / record["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / record["path"], destination)
        if sha(destination) != record["sha256"]:
            raise ValueError("Copy verification failed: " + record["path"])
    final_records, _ = inventory(source)
    if final_records != records:
        raise ValueError("Source changed during packaging; candidate retained for inspection")
    manifest = {
        "schema": "mingjing-full-source-package.v1", "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "package_scope_complete_execution_not_claimed",
        "source": str(source), "freeze_receipt": str(freeze), "freeze_receipt_sha256": sha(freeze),
        "source_inventory_digest": source_digest, "files": records,
        "file_count": len(records), "source_bytes": sum(x["bytes"] for x in records),
        "carrier": carrier, "whitelist_directories": sorted(SOURCE_DIRECTORIES),
        "credential_scan": scan,
        "whitelist_root_files": sorted(SOURCE_FILES), "excluded_paths": exclusions,
        "excluded_root_generated_manifest": sorted(EXCLUDED_ROOT_FILES),
        "scope": "project source/docs/tests/resources + locked vendor + staged carrier production closure",
        "professional_acceptance": "see separate receipts", "release_acceptance": "see separate receipts",
        "official_submission": "not_performed",
    }
    (package_root / "FULL_SOURCE_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_path = out / (package_name + ".zip")
    with zipfile.ZipFile(archive_path, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(package_root.rglob("*")):
            if path.is_file():
                archive.write(path, package_name + "/" + path.relative_to(package_root).as_posix())
    with zipfile.ZipFile(archive_path) as archive:
        from scripts.create_core_review import scan_bytes
        overlap = max([len(value) for value in values] + [80])
        for record in records:
            member = package_name + "/" + record["path"]
            digest = hashlib.sha256()
            with archive.open(member) as stream:
                previous = b""
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    if not member.lower().endswith(".docx"):
                        scan_bytes(previous + chunk, values, member)
                        previous = (previous + chunk)[-overlap:]
            if member.lower().endswith(".docx"):
                scan_bytes(archive.read(member), values, member)
            if digest.hexdigest() != record["sha256"]:
                raise ValueError("ZIP verification failed: " + record["path"])
    archive_hash = sha(archive_path)
    (out / (package_name + ".zip.sha256")).write_text(archive_hash + "  " + archive_path.name + "\n", encoding="utf-8")
    receipt = {"schema": "mingjing-full-source-package-receipt.v1", "version": VERSION,
               "status": "packaged_and_hash_verified", "source_inventory_digest": source_digest,
               "package_directory": str(package_root), "archive": str(archive_path),
               "archive_sha256": archive_hash, "archive_bytes": archive_path.stat().st_size,
               "file_count": len(records), "source_bytes": manifest["source_bytes"],
               "carrier_files_verified": carrier["manifest_files_verified"],
               "credential_scan": scan,
               "zip_hash_stored_outside_archive": True, "execution_acceptance": "not_claimed"}
    (out / (package_name + ".receipt.json")).write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
