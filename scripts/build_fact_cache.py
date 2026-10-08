"""Build a new, reproducible financial fact-cache generation.

The builder deliberately never edits a frozen corpus.  PDF materials and
metadata are staged into a new generation (hard links by default), then the
current parser is run against that generation.  A generation is published
atomically only after the source tree, extractor modules, and all derived
records have been checked.  This is a build-stage utility; it is not an EXE
packager and it does not fetch material from the network.

Examples::

    python scripts/build_fact_cache.py \
      --materials runs/financial-materials-stable-15 \
      --out runs/financial-materials-cachegen-16

Use ``--link-mode copy`` only when the destination is on another filesystem.
Copying is explicit because a normal build must not silently duplicate the
hundreds of megabytes of source PDFs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mingjing.evidence import sha256_file
from mingjing.facts import FactStore
from mingjing.parser import extract_text, load_or_extract_facts
from mingjing.source_cache import CACHE_SCHEMA, extractor_source_modules
from mingjing.source_gap_catalog import extract_source_gaps, validate_source_gap


_MATERIAL_DIRS = ("reports", "records")
_SOURCE_CATEGORIES = {"招股书", "问询回复"}


def _sha(path: Path) -> str:
    return sha256_file(path)


def _tree_hashes(root: Path) -> dict[str, str]:
    """Hash every byte in the frozen source directory, including old facts."""
    paths: list[Path] = []
    for p in root.rglob("*"):
        if p.is_symlink() or getattr(os.path, "isjunction", lambda _: False)(p):
            raise ValueError(f"冻结语料包含不允许的符号链接或 junction：{p}")
        if p.is_file():
            paths.append(p)
    return {p.relative_to(root).as_posix(): _sha(p) for p in sorted(paths)}


def _ensure_inside(path: Path, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        raise ValueError(f"{label} 不存在：{path}") from None
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"{label} 越过语料根目录：{path}")
    return resolved


def _copy_or_link(source: Path, target: Path, mode: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink() or getattr(os.path, "isjunction", lambda _: False)(source):
        raise ValueError(f"冻结语料包含不允许的符号链接或 junction：{source}")
    if mode == "copy":
        shutil.copy2(source, target)
        return
    try:
        os.link(source, target)
    except OSError as exc:
        # EXDEV is the useful case here, but a clear explicit-mode hint is
        # safer for other Windows hard-link failures as well.
        raise ValueError(
            f"无法对材料建立硬链接（可能跨卷）：{source} -> {target}；"
            "如需复制请显式指定 --link-mode copy"
        ) from exc


def _stage_materials(materials: Path, stage: Path, *, link_mode: str) -> dict[str, str]:
    """Stage only immutable source material trees; never stage old facts.json."""
    for name in _MATERIAL_DIRS:
        source_dir = materials / name
        if not source_dir.is_dir():
            continue
        for source in sorted(source_dir.rglob("*")):
            if source.is_symlink() or getattr(os.path, "isjunction", lambda _: False)(source):
                raise ValueError(f"冻结语料包含不允许的符号链接或 junction：{source}")
            if source.is_dir():
                continue
            relative = source.relative_to(materials)
            _copy_or_link(source, stage / relative, link_mode)
    # Keep the registered index and manifest byte-identical.  In the default
    # hardlink mode they are linked as requested; copy mode is explicit for
    # cross-volume builds.
    for name in ("index.csv", "material_manifest.json", "source_materials_receipt.json"):
        source = materials / name
        if source.is_file():
            _copy_or_link(source, stage / name, link_mode)
    return _tree_hashes(materials)


def _load_manifest(materials: Path) -> tuple[dict, list[dict], list[dict]]:
    manifest_path = materials / "material_manifest.json"
    index_path = materials / "index.csv"
    if not manifest_path.is_file() or not index_path.is_file():
        raise ValueError("冻结语料必须同时提供 index.csv 和 material_manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"material_manifest.json 无法读取：{exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("documents"), list):
        raise ValueError("material_manifest.json 缺少 documents 列表")
    cutoff = manifest.get("cutoff")
    try:
        valid_cutoff = isinstance(cutoff, str) and date.fromisoformat(cutoff).isoformat() == cutoff
    except ValueError:
        valid_cutoff = False
    if not valid_cutoff:
        raise ValueError("冻结语料缺少有效 cutoff")
    with index_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("index.csv 为空")
    return manifest, rows, manifest["documents"]


def _validate_rows(materials: Path, rows: list[dict], cutoff: str) -> None:
    seen: set[str] = set()
    for number, row in enumerate(rows, 2):
        name = (row.get("file") or "").strip()
        if not name or name in seen:
            raise ValueError(f"index.csv 第 {number} 行文件为空或重复")
        seen.add(name)
        path = _ensure_inside(materials / name, materials, "index.csv 文件")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"事实建库只允许登记 PDF：{name}")
        if row.get("size_bytes") and int(row["size_bytes"]) != path.stat().st_size:
            raise ValueError(f"语料文件大小与 index.csv 不一致：{name}")
        expected = (row.get("sha256") or "").lower()
        if expected and expected != _sha(path):
            raise ValueError(f"语料文件 SHA-256 与 index.csv 不一致：{name}")
        date = (row.get("date") or "").strip()
        # An unknown date remains visible as an unresolved source.  It cannot
        # be used for an as-of query, but rejecting it here would erase a
        # legitimate material gap from the generation.  Future-dated material
        # is unsafe and is rejected before parsing.
        if date and date > cutoff:
            raise ValueError(f"语料披露日期超过 cutoff：{name}")


def _relative_source(path: str | Path, stage: Path) -> str:
    if not str(path or "").strip():
        return ""
    source = Path(path)
    try:
        resolved = source.resolve()
        relative = resolved.relative_to(stage.resolve())
    except (OSError, ValueError):
        # A parser implementation may already return a relative source path.
        relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"派生事实来源路径越出新 generation：{path}")
    return relative.as_posix()


def _derive_source_gaps(stage: Path, rows: list[dict], *, progress: Callable[[str], None] | None,
                        undated: list[str] | None = None) -> list[dict]:
    gaps: list[dict] = []
    for number, row in enumerate(rows, 1):
        if row.get("category", "") not in _SOURCE_CATEGORIES:
            continue
        if not (row.get("date") or "").strip():
            continue
        path = _ensure_inside(stage / row["file"], stage, "新 generation 来源")
        digest = (row.get("sha256") or _sha(path)).lower()
        pages = extract_text(path)
        found = extract_source_gaps(
            pages, path=path, company=(row.get("name") or ""),
            doc_sha=digest, disclosure_date=(row.get("date") or ""),
        )
        for gap in found:
            gap = dict(gap)
            gap["source_path"] = _relative_source(gap.get("source_path", path), stage)
            validate_source_gap(gap)
            gaps.append(gap)
        if progress:
            progress(f"source_gaps {number}/{len(rows)} {len(found)}")
    return list({item["resource_id"]: item for item in gaps}.values())


def build_generation(materials: str | Path, out: str | Path, *, link_mode: str = "hardlink",
                     progress: Callable[[str], None] | None = None) -> Path:
    """Build and atomically publish a fresh cache generation.

    ``out`` must not exist.  This makes accidental replacement of a frozen
    snapshot impossible and is also useful for reproducible CI fixtures.
    """
    if link_mode not in {"hardlink", "copy"}:
        raise ValueError("link_mode 必须为 hardlink 或 copy")
    material_root = Path(materials).resolve(strict=True)
    if not material_root.is_dir():
        raise ValueError("materials 必须是目录")
    output = Path(out).resolve()
    if output.exists():
        raise ValueError(f"拒绝覆盖已存在的 generation：{output}")
    if output == material_root or output.is_relative_to(material_root):
        raise ValueError("generation 输出目录不能位于旧冻结语料内部")
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.parent / f".{output.name}.pending-{os.getpid()}"
    if pending.exists():
        raise ValueError(f"存在未完成的 generation 暂存目录，请人工检查后处理：{pending}")

    manifest, rows, documents = _load_manifest(material_root)
    _validate_rows(material_root, rows, manifest["cutoff"])
    index_files = {row.get("file", "") for row in rows}
    manifest_files = {doc.get("file", "") for doc in documents if isinstance(doc, dict)}
    if index_files != manifest_files or "" in manifest_files:
        raise ValueError("index.csv 与 material_manifest.json 的文件登记不一致")
    by_file = {row.get("file"): row for row in rows}
    for document in documents:
        row = by_file[document["file"]]
        for key in ("sha256", "size_bytes", "date"):
            # Do not silently alter metadata or use a different hash from the
            # one used to select the source PDF.
            if document.get(key) not in (None, "") and row.get(key) not in (None, "") and str(document[key]) != str(row[key]):
                raise ValueError(f"index.csv 与 material_manifest.json 的 {key} 不一致：{document['file']}")
    parent_hashes = _tree_hashes(material_root)
    pending.mkdir()
    try:
        _stage_materials(material_root, pending, link_mode=link_mode)
        # The index is copied above; assert it still has exactly the same
        # registered file rows before parsing anything.
        staged_manifest, staged_rows, _ = _load_manifest(pending)
        if staged_manifest.get("documents") != documents or staged_rows != rows:
            raise ValueError("新 generation 改写了冻结 index/manifest 字段")

        if progress:
            progress("facts reparse")
        modules_before = extractor_source_modules()
        source_gap_module_before = _sha(ROOT / "mingjing" / "source_gap_catalog.py")
        # Indexed documents carry their own disclosure date.  Keep the
        # fallback empty so an unknown date remains unknown instead of being
        # silently stamped with the as-of cutoff.
        facts = load_or_extract_facts(pending, company="", period="", publish_date="")
        modules = extractor_source_modules()
        if modules != modules_before:
            raise ValueError("事实提取器源码在建库期间发生变化，拒绝发布 generation")
        # source_path is an immutable relative resource reference in a cache,
        # never a path belonging to the temporary staging directory.
        facts = [replace(fact, source_path=_relative_source(fact.source_path, pending))
                 for fact in facts]
        store = FactStore(facts)
        undated_documents: list[str] = [
            row.get("file", "") for row in rows if not (row.get("date") or "").strip()
        ]
        gaps = _derive_source_gaps(pending, rows, progress=progress, undated=undated_documents)
        source_gap_module = _sha(ROOT / "mingjing" / "source_gap_catalog.py")
        if source_gap_module != source_gap_module_before:
            raise ValueError("来源缺口提取器源码在建库期间发生变化，拒绝发布 generation")
        parent_manifest_sha = _sha(material_root / "material_manifest.json")
        parent_index_sha = _sha(material_root / "index.csv")
        generation_basis = json.dumps(
            {"parent_manifest": parent_manifest_sha, "parent_index": parent_index_sha,
             "modules": modules, "source_gap_module": source_gap_module,
             "schema": CACHE_SCHEMA}, ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")
        generation_id = "cachegen-" + hashlib.sha256(generation_basis).hexdigest()[:16]
        payload = {
            "facts": [fact.to_dict() for fact in store.facts],
            "source_gaps": gaps,
            "undated_documents": undated_documents,
            "source_gap_catalog": {
                "schema_version": "mingjing-source-gaps.v1",
                "cutoff": manifest["cutoff"],
                "material_manifest_sha256": parent_manifest_sha,
                "extractor_sha256": source_gap_module,
                "source_gap_count": len(gaps),
                "policy": "read_only_not_facts_no_candidate_value_matching",
            },
            "source_fact_cache": {
                "schema_version": CACHE_SCHEMA,
                "generation_id": generation_id,
                "modules": modules,
                "source_gap_module": source_gap_module,
                "parent_material_manifest_sha256": parent_manifest_sha,
                "parent_index_sha256": parent_index_sha,
                "link_mode": link_mode,
                "rebuild_policy": "current_parser_read_only_reparse_no_old_facts",
            },
        }
        # Facts are written only after all extraction succeeded.  A failed
        # build therefore leaves no apparently usable partial cache.
        pending_facts = pending / "facts.pending.json"
        with pending_facts.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending_facts, pending / "facts.json")
        receipt = {
            "schema_version": "mingjing-source-cache-receipt.v3",
            "status": "cache_generation_built_source_acceptance_not_claimed",
            "generation_id": generation_id,
            "cutoff": manifest["cutoff"],
            "parent_materials": str(material_root),
            "parent_material_manifest_sha256": parent_manifest_sha,
            "parent_index_sha256": parent_index_sha,
            "facts_sha256": _sha(pending / "facts.json"),
            "facts_count": len(store.facts),
            "source_gap_count": len(gaps),
            "undated_documents": undated_documents,
            "modules": modules,
            "source_gap_module": source_gap_module,
            "link_mode": link_mode,
            "old_snapshot_tree_sha256": hashlib.sha256(json.dumps(parent_hashes, sort_keys=True).encode()).hexdigest(),
            "source_materials_preserved": True,
        }
        with (pending / "cache_generation_receipt.json").open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Detect an external writer changing the frozen source while parsing.
        if _tree_hashes(material_root) != parent_hashes:
            raise ValueError("旧冻结语料在建库期间发生字节变化，拒绝发布 generation")
        os.replace(pending, output)
        return output
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials", type=Path, required=True, help="已冻结的原始语料目录")
    parser.add_argument("--out", type=Path, required=True, help="不存在的新 generation 目录")
    parser.add_argument("--link-mode", choices=("hardlink", "copy"), default="hardlink",
                        help="材料 staging 方式，默认 hardlink；跨卷请显式使用 copy")
    options = parser.parse_args(argv)
    result = build_generation(options.materials, options.out, link_mode=options.link_mode,
                              progress=lambda message: print(message, flush=True))
    print(f"PUBLISHED {result}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
