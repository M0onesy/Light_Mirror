"""Inventory the single current proposal/manual without copying or modifying it.

This records file integrity only. Word rendering and financial acceptance remain
separate evidence; historical dev3 sealing receipts are never rewritten.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from scripts.create_core_review import _canonical, BASELINES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, default=PROJECT / "docs" / "project")
    parser.add_argument("--out", type=Path, required=True, help="New JSON receipt under evidence/documentation")
    args = parser.parse_args()
    documents = _canonical(args.documents)
    out = _canonical(args.out, exists=False)
    allowed = _canonical(PROJECT / "evidence" / "documentation", exists=False)
    if out == allowed or not out.is_relative_to(allowed):
        raise ValueError("Document receipt must stay under evidence/documentation")
    if out.exists():
        raise FileExistsError("Historical receipts cannot be overwritten")
    names = [*BASELINES, "明镜MingJing_项目计划书_初赛评审版.pdf", "金融人工智能竞赛要求.txt"]
    rows = []
    for name in names:
        path = _canonical(documents / name)
        content = path.read_bytes()
        rows.append({"path": path.relative_to(PROJECT).as_posix(),
                     "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    receipt = {"schema": "mingjing-current-documents-integrity.v1", "version": "1.0.0.dev3",
               "created_at": datetime.now(timezone.utc).isoformat(),
               "status": "hash_inventory_complete_render_and_acceptance_not_claimed", "files": rows,
               "current_documents_modified": False, "duplicate_document_tree_created": False}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": receipt["status"], "files": len(rows), "receipt": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
