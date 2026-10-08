"""Locate and verify the immutable Harness source archive used by this project."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import stat

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UPSTREAM = ROOT / "vendor/harness"


def io_path(path: Path) -> Path:
    value = str(path.absolute())
    return Path("\\\\?\\" + value) if os.name == "nt" and not value.startswith("\\\\?\\") else path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with io_path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def locked_source(root: Path = ROOT) -> dict:
    return json.loads((root / "runtime/harness/source_snapshot.json").read_text(encoding="utf-8"))


def regular_source(base: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(part in ("", ".", "..") or ":" in part or "\\" in part for part in parts.parts):
        raise ValueError("Source resource has unsafe path components")
    path = base / relative
    if not path.absolute().is_relative_to(base.absolute()):
        raise ValueError("Source resource escaped its archive")
    for item in (path, *path.parents):
        info = io_path(item).lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Source archives cannot traverse links or junctions")
        if item == base:
            break
    if not stat.S_ISREG(io_path(path).stat().st_mode):
        raise ValueError("Source resource is not a regular file")
    return path


def verify_upstream(upstream: Path = DEFAULT_UPSTREAM, root: Path = ROOT) -> dict:
    upstream = upstream.absolute()
    lock = locked_source(root)
    files = lock["files"]
    encoded = json.dumps(files, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != lock["source_sha256"]:
        raise ValueError("Harness source inventory hash is invalid")
    for item in files:
        path = regular_source(upstream, item["path"])
        if io_path(path).stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise ValueError("Harness source differs from lock: " + item["path"])
    return {"status": "passed", "upstream": str(upstream), "files": len(files),
            "bytes": sum(item["bytes"] for item in files), "source_sha256": lock["source_sha256"],
            "version": lock["upstream_version"], "git_commit": lock.get("git_commit")}


def vendor_source(source: Path, destination: Path = DEFAULT_UPSTREAM) -> dict:
    receipt = verify_upstream(source)
    destination = destination.absolute()
    if destination.exists():
        return verify_upstream(destination)
    if not destination.is_relative_to(ROOT):
        raise ValueError("Vendoring destination must stay inside this project")
    for parent in (destination.parent, *destination.parents):
        if parent.exists():
            info = io_path(parent).lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Vendoring destination cannot traverse linked directories")
        if parent == ROOT:
            break
    for item in locked_source()["files"]:
        target = destination / item["path"]
        io_path(target.parent).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(io_path(source / item["path"]), io_path(target))
    receipt = verify_upstream(destination)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    parser.add_argument("--vendor-from", type=Path)
    args = parser.parse_args()
    receipt = vendor_source(args.vendor_from, args.upstream) if args.vendor_from else verify_upstream(args.upstream)
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
