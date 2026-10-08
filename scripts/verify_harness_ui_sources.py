"""Verify vendored UI bytes and the reproducible upstream frame adaptation.

Read-only: this does not run the vendor sync, overwrite UI files or build assets.
The receipt binds an older per-file vendor manifest to the runtime snapshot too.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath

from scripts.vendor_harness_ui import ROOT, UPSTREAM, DESTINATION, adapt_frame


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_resource(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if not parts.parts or parts.is_absolute() or any(part in ("", ".", "..") or ":" in part or "\\" in part for part in parts.parts):
        raise ValueError("Unsafe vendor source path")
    path = root / Path(*parts.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Vendor source escaped its resource root")
    current = path
    while current != root.parent:
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked vendor source is not allowed")
        current = current.parent
    return path


def verify() -> dict:
    manifest_path = DESTINATION / "manifest.json"
    snapshot_path = ROOT / "runtime/harness/source_snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    locked = {entry["path"]: entry for entry in snapshot["files"]}
    errors, entries, seen = [], [], set()
    if manifest.get("version") != snapshot["upstream_version"] or manifest.get("upstream_commit") is not None:
        errors.append("UI source archive version/commit metadata differs")
    for entry in manifest["files"]:
        relative = entry["path"]
        if relative in seen:
            errors.append(f"Duplicated source: {relative}")
            continue
        seen.add(relative)
        vendored = safe_resource(DESTINATION, relative)
        upstream = safe_resource(UPSTREAM, relative)
        actual = {"path": relative, "bytes": vendored.stat().st_size, "sha256": sha(vendored)}
        source = {"path": relative, "bytes": upstream.stat().st_size, "sha256": sha(upstream)}
        if actual != entry or source != entry or locked.get(relative) != entry:
            errors.append(f"Source bytes differ from manifest/archive: {relative}")
        entries.append(actual)
    actual_files = {path.relative_to(DESTINATION).as_posix() for path in DESTINATION.rglob("*") if path.is_file()}
    extras = sorted(actual_files - seen - {"manifest.json"})
    if extras:
        errors.append(f"Unregistered vendor files: {extras}")
    adapter_path = ROOT / "frontend/src/HarnessFrame.tsx"
    original_path = DESTINATION / "packages/client/ui-layout/src/client/AppFrame.tsx"
    expected_adapter = adapt_frame(original_path.read_text(encoding="utf-8"))
    actual_adapter = adapter_path.read_text(encoding="utf-8")
    adapter_match = actual_adapter == expected_adapter
    if not adapter_match:
        errors.append("HarnessFrame differs from the declared adapt_frame transformation")
    references = []
    for relative, source, tokens in (
        ("frontend/src/Inspector.tsx", "packages/client/ui-dockkit/src/index.ts", ("DockController", "DockSurface", "FloatLayer")),
        ("frontend/src/Conversation.tsx", "packages/client/ui-primitives/src/markdown/MarkdownText.tsx", ("MarkdownText",)),
    ):
        consumer = ROOT / relative
        text = consumer.read_text(encoding="utf-8")
        imports = re.findall(r"import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]", text)
        symbols = set()
        for names, specifier in imports:
            target = f"../vendor/harness/{source.rsplit('.', 1)[0]}"
            if specifier in (target, target + '.ts', target + '.tsx'):
                symbols.update(name.strip().split(' as ')[0] for name in names.split(','))
        references.append({"consumer": relative, "consumer_sha256": sha(consumer), "upstream_module": source,
                           "declared_symbols": list(tokens), "symbols_present": set(tokens).issubset(symbols)})
    if not all(reference["symbols_present"] for reference in references):
        errors.append("Declared upstream UI component use was not found in a consumer")
    return {"status": "passed_source_and_adapter_lock" if not errors else "failed_source_lock",
            "upstream_version": snapshot["upstream_version"], "upstream_commit": None,
            "source_archive_sha256": snapshot["source_sha256"], "source_snapshot_sha256": sha(snapshot_path),
            "vendor_manifest_sha256": sha(manifest_path), "vendor_files": len(entries),
            "vendor_bytes": sum(entry["bytes"] for entry in entries),
            "adapter": {"path": "frontend/src/HarnessFrame.tsx", "bytes": adapter_path.stat().st_size,
                        "sha256": sha(adapter_path), "normalized_generated_text_sha256": hashlib.sha256(expected_adapter.encode()).hexdigest(),
                        "generated_text_match": adapter_match, "original_bytes": original_path.stat().st_size,
                        "original_sha256": sha(original_path), "method_script_sha256": sha(ROOT / "scripts/vendor_harness_ui.py")},
            "component_references": references, "source_files": entries, "errors": errors,
            "browser_acceptance": "not_executed_by_source_verifier"}


def main() -> None:
    global UPSTREAM
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "runs/harness-ui-source-review")
    parser.add_argument("--upstream", type=Path, default=UPSTREAM)
    args = parser.parse_args()
    UPSTREAM = args.upstream.resolve(strict=True)
    from scripts.upstream_source import verify_upstream
    verify_upstream(UPSTREAM)
    result = verify()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "vendor_files", "vendor_bytes", "adapter", "errors")}, ensure_ascii=False))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
