"""Freeze corpus identity/period/disclosure metadata and content hashes.

This is a developer acceptance command; the product's normal run hashes only
the reports it actually consumes. Large reports are referenced, never copied.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mingjing.evidence import sha256_file
from mingjing.parser import _indexed_files, _company_from_path, _document_period


def freeze(root: Path, output: Path, company: str = "") -> dict:
    root = root.resolve()
    indexed = _indexed_files(root, company)
    if indexed is None:
        paths = [root] if root.is_file() else sorted(root.rglob("*.pdf"))
        indexed = [(path, (_company_from_path(path) or ("", ""))[1], _document_period(path, ""), "") for path in paths]
    documents = []
    for path, issuer, period, date in indexed:
        documents.append({"path": str(path), "company": issuer,
                          "report_type": path.parent.name, "period": _document_period(path, period),
                          "disclosure_date": date or path.name[:10],
                          "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {"schema_version": 1, "root": str(root), "document_count": len(documents),
                "total_bytes": sum(row["size_bytes"] for row in documents), "documents": documents}
    index_dirs = [root.parent] if root.is_file() else [root]
    index_dirs += list(index_dirs[0].parents)
    index = next((folder / "index.csv" for folder in index_dirs if (folder / "index.csv").is_file()), None)
    if index:
        manifest["source_index"] = {"path": str(index), "sha256": sha256_file(index)}
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate a frozen corpus manifest without copying reports")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("runs/corpus_manifest.json"))
    parser.add_argument("--company", default="")
    args = parser.parse_args()
    result = freeze(args.corpus, args.out, args.company)
    print(json.dumps({"document_count": result["document_count"], "total_bytes": result["total_bytes"],
                      "manifest": str(args.out.resolve()), "sha256": sha256_file(args.out)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
