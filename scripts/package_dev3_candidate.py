"""Assemble the dev3 candidate from its exact build, smoke and full-source ZIP.

This command has no formal-release option. It leaves package_delivery and its
formal quality/environment gates unchanged. Runtime profiles, real model
caches and arbitrary acceptance directories are never copied.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tomllib
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_receipt import canonical_hash
from scripts.create_core_review import DENIED_NAMES, _canonical, scan_bytes
from scripts.package_delivery import sha256, verify_current_build_inputs
from scripts.package_stage import scan_embedded_exe, stream_scan

VERSION = "1.0.0.dev3"
REQUIRED_CHECKS = {
    "isolate_saved_profile", "identify_exact_build", "cold_start_port_conflict", "duplicate_launch",
    "homepage_upload_kernel_occurrences_report_png_export_replay", "invalid_scope_and_path_guards",
    "small_duplicate_occurrence_kernel_control", "real_saved_provider_native_and_material_message_replay",
    "normal_exit_restart_history", "credentials_and_original_profile",
}
DOCS = ("docs/USER_GUIDE.md", "docs/RUNBOOK.md", "docs/DELIVERY_GUIDE.md", "docs/EXE_DELIVERY.md",
        "docs/LICENSE_GUIDE.md", "docs/THIRD_PARTY.md", "docs/THIRD_PARTY_NOTICES.md")
SAMPLES = ("samples/smart/星河智造_2023年年度报告.pdf",
           "samples/smart/虚构证券_2024-04-25_星河智造深度报告.pdf")
NOT_EXECUTED = ("human_gold", "professional_rule_signoff", "D3_time_holdout", "independent_windows_vm",
                "full_report_financial_acceptance", "kimi_real_api", "openai_real_api")


def read_json(path):
    _canonical(path)
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Receipt must be a JSON object")
    return value


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def verify_candidate_receipts(root, exe, build_path, smoke_path, lock_path):
    version = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    require(version == VERSION, "This assembler accepts only the 1.0.0.dev3 development prerelease")
    for path in (exe, build_path, smoke_path, lock_path):
        _canonical(path)
    build, smoke, lock = read_json(build_path), read_json(smoke_path), read_json(lock_path)
    require(build.get("version") == smoke.get("version") == lock.get("version") == VERSION,
            "EXE build, smoke and source lock versions differ")
    require(build.get("mode") == lock.get("mode") == "onefile", "Candidate requires the onefile entry")
    require(build.get("sha256") == smoke.get("exe_sha256") == sha256(exe)
            and build.get("bytes") == exe.stat().st_size, "EXE hash/size differs from build or smoke")
    require(build.get("source_sha256") == smoke.get("source_sha256") == lock.get("source_sha256")
            == canonical_hash(lock.get("inputs")) and build.get("inputs") == lock.get("inputs"),
            "Build/smoke/source lock do not identify the same source epoch")
    require(build.get("runtime_manifest_sha256") and build.get("runtime_manifest_sha256")
            == smoke.get("runtime_manifest_sha256") == lock.get("runtime_manifest_sha256"),
            "Runtime lock differs between build, smoke and source lock")
    require(smoke.get("status") == "passed_scoped_dev3_candidate" and smoke.get("source_simulation") is False,
            "Passing exact-EXE dev3 smoke is required; source simulation cannot approve packaging")
    require(smoke.get("user_profile_modified") is False and smoke.get("real_responses_copied_to_output") is False,
            "Smoke did not preserve user configuration/credential or private-response isolation")
    require(all(smoke.get(name) == "not_executed" for name in ("human_gold", "D3_time_holdout", "independent_windows_vm")),
            "Scoped development evidence cannot promote pending formal gates")
    checks = smoke.get("checks")
    require(isinstance(checks, list) and len(checks) == len(REQUIRED_CHECKS), "Missing or duplicate dev3 smoke checks")
    by_name = {row.get("name"): row for row in checks}
    require(set(by_name) == REQUIRED_CHECKS and all(row.get("status") == "passed" for row in checks),
            "All declared dev3 smoke checks must actually pass")
    cold = by_name["cold_start_port_conflict"]
    require(cold.get("occupied_port_recovered") and cold.get("capabilities", {}).get("pdf") is True
            and cold.get("capabilities", {}).get("harness") is True, "PDF/native/port recovery missing")
    isolated = by_name["isolate_saved_profile"]
    require(all(isolated.get(key) is True for key in ("original_readonly", "dpapi_clone", "routes_and_budgets_unchanged")),
            "Saved profile was not isolated with its unchanged routes and budgets")
    credentials = by_name["credentials_and_original_profile"]
    require(credentials.get("user_profile_unchanged") is True and credentials.get("plaintext_credentials_found") == 0
            and credentials.get("dpapi_secret_files_excluded") is True, "Credential isolation evidence missing")
    kernel = by_name["homepage_upload_kernel_occurrences_report_png_export_replay"]
    replay = kernel.get("replay", {})
    require(replay.get("status") == "identical" and all(replay.get(key) is True for key in ("numeric_equal", "integrity_ok", "recomputed"))
            and replay.get("network_calls") == 0, "Pure kernel offline replay did not pass")
    require(kernel.get("model_requests") == 0 and kernel.get("source_addresses") is True and not kernel.get("page_errors")
            and kernel.get("report_readable") is True and kernel.get("png_bytes", 0) > 0
            and kernel.get("export", {}).get("frozen_bytes_equal") is True
            and all(kernel.get("states", {}).get(key, 0) > 0 for key in ("verified", "violated", "unverifiable")),
            "Current upload UI, three states, occurrences, report, PNG or frozen export not verified")
    native = by_name["real_saved_provider_native_and_material_message_replay"]
    probe = native.get("probe", {})
    require(probe.get("status") == "passed" and probe.get("native_runtime") is True and probe.get("tool_roundtrip") is True,
            "Saved-provider native dependency tool loop missing")
    usage = native.get("model_usage", {})
    require(usage.get("status") == "success" and usage.get("request_count", 0) > 0 and usage.get("mapped_claims", 0) > 0,
            "Small native task did not execute the saved real model")
    require(native.get("followup", {}).get("usage", {}).get("model_requests", 0) > 0
            and native.get("followup", {}).get("answer_not_copied") is True, "Real guarded follow-up evidence missing")
    for name, equal in (("agent_replay", ("numeric_equal", "bindings_equal", "report_equal", "tool_trace_equal", "execution_equal")),
                        ("message_replay", ("answer_equal", "references_equal", "tool_trace_equal"))):
        replay = native.get(name, {})
        require(replay.get("status") == "passed" and replay.get("real_harness_loop") is True
                and replay.get("network_calls") == 0 and all(replay.get(key) is True for key in equal),
                "Offline cached native " + name + " did not pass")
    for row in lock["inputs"]:
        relative = PurePosixPath(row["path"])
        require(not relative.is_absolute() and ".." not in relative.parts and ":" not in row["path"]
                and "\\" not in row["path"], "Unsafe source lock path")
        path = root.joinpath(*relative.parts)
        _canonical(path)
        require(path.stat().st_size == row["bytes"] and sha256(path) == row["sha256"], "Source changed since accepted build: " + row["path"])
    return build, smoke


def readonly_secrets():
    from mingjing.config import SecretStore, app_root
    profile = app_root()
    paths = [profile / name for name in ("config.json", "secrets.json", "secrets.key", "config.v1.bak.json")]
    snapshot = lambda: {str(path): sha256(path) if path.is_file() else None for path in paths}
    before = snapshot()
    if not (profile / "secrets.json").is_file():
        return [], before, snapshot
    require(read_json(profile / "secrets.json").get("backend") == "dpapi", "Migrate legacy secrets in the UI before readonly packaging")
    store = SecretStore(profile)
    values = [value.encode("utf-8") for ref in store.references() if (value := store.get(ref))]
    require(before == snapshot(), "Credential reads changed original profile")
    return values, before, snapshot


def full_source(path, build, values):
    receipt = read_json(path)
    require(receipt.get("status") == "packaged_and_hash_verified" and receipt.get("version") == VERSION,
            "Full-source packaging receipt missing or from another version")
    archive_path = Path(receipt["archive"]).resolve()
    _canonical(archive_path)
    require(archive_path.stat().st_size == receipt.get("archive_bytes") and sha256(archive_path) == receipt.get("archive_sha256"),
            "Full-source ZIP changed since packaging")
    with zipfile.ZipFile(archive_path) as archive:
        manifests = [name for name in archive.namelist() if name.endswith("/FULL_SOURCE_MANIFEST.json")]
        require(len(manifests) == 1, "Full-source manifest missing or ambiguous")
        manifest = json.loads(archive.read(manifests[0]))
        require(manifest.get("version") == VERSION and canonical_hash(manifest["files"]) == receipt.get("source_inventory_digest"),
                "Full-source inventory differs from its receipt")
        source_rows = {row["path"]: row for row in manifest["files"]}
        require(len(source_rows) == len(manifest["files"]), "Duplicate full-source paths")
        for row in build["inputs"]:
            require(source_rows.get(row["path"]) == row, "Full-source ZIP does not include the accepted build input: " + row["path"])
        prefix = manifests[0].removesuffix("FULL_SOURCE_MANIFEST.json")
        expected = {prefix + row["path"] for row in manifest["files"]} | {manifests[0]}
        require(len(archive.namelist()) == len(expected) and set(archive.namelist()) == expected, "Unexpected full-source ZIP member")
        for info in archive.infolist():
            rel = PurePosixPath(info.filename)
            require(not rel.is_absolute() and ".." not in rel.parts and ":" not in info.filename
                    and not stat.S_ISLNK(info.external_attr >> 16), "Unsafe full-source ZIP member")
            if info.filename.lower().endswith(".docx"):
                raw = archive.read(info)
                scan_bytes(raw, values, "full-source/" + info.filename)
                h = hashlib.sha256(raw)
            else:
                with archive.open(info) as stream:
                    h, previous = hashlib.sha256(), b""
                    overlap = max([len(value) for value in values] + [80])
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(block)
                        scan_bytes(previous + block, values, "full-source/" + info.filename)
                        previous = (previous + block)[-overlap:]
            if info.filename != manifests[0]:
                row = source_rows[info.filename[len(prefix):]]
                require(info.file_size == row["bytes"] and h.hexdigest() == row["sha256"], "Full-source member hash mismatch")
    return receipt, archive_path


def create_candidate(args):
    root, out = ROOT.resolve(), args.out.resolve()
    require(not out.is_relative_to(root), "Candidate output must stay outside the editable source tree")
    destinations = (out, out.with_name(out.name + ".zip"), out.with_name(out.name + ".receipt.json"), out.with_name(out.name + ".zip.sha256"))
    require(not any(path.exists() for path in destinations), "Refusing to overwrite an existing candidate")
    build, smoke = verify_candidate_receipts(root, args.exe, args.build_receipt, args.smoke_receipt, args.source_lock)
    verify_current_build_inputs(root, args.build_receipt)
    values, before, original_snapshot = readonly_secrets()
    embedded = scan_embedded_exe(args.exe, root, values)
    source_receipt, source_archive = full_source(args.full_source_receipt, build, values)
    files = [(args.exe, "MingJing.exe"), (args.build_receipt, "MingJing-build.json"),
             (args.source_lock, "build-source-lock.json"), (args.smoke_receipt, "验收记录/dev3-exe-smoke.json"),
             (args.full_source_receipt, "源码/full-source.receipt.json"),
             (source_archive, "源码/" + source_archive.name), (root / "LICENSE", "LICENSE")]
    files += [(root / name, name) for name in (*DOCS, *SAMPLES)]
    for current, directories, names in os.walk(root / "docs/licenses", followlinks=False):
        for name in directories:
            _canonical(Path(current) / name)
        files += [(Path(current) / name, (Path(current) / name).relative_to(root).as_posix()) for name in names]
    if args.documents:
        require(args.documents_receipt is not None, "Reviewed documents require their sealed receipt")
        reviewed = read_json(args.documents_receipt)
        require(reviewed.get("status") == "documents_staged_visual_review_passed_with_pdf_fallback", "Reviewed documents not sealed")
        indexed = {Path(row["path"]).name: row for row in reviewed["staged_documents"]}
        for name in ("明镜MingJing_项目计划书.docx", "明镜MingJing_项目计划书_初赛评审版.pdf",
                     "明镜_计划书打磨与开发任务手册.md", "交付说明.md"):
            path = args.documents / name
            require(name in indexed and sha256(path) == indexed[name]["sha256"], "Reviewed document changed: " + name)
            files.append((path, "文档/" + name))
        files.append((args.documents_receipt, "验收记录/docs-revision.json"))
    names = set()
    for path, member in files:
        _canonical(path)
        require(path.is_file() and path.name.casefold() not in DENIED_NAMES and not path.name.casefold().startswith(".env"),
                "Credential/configuration or non-file candidate input")
        require(member not in names, "Duplicate candidate member")
        names.add(member)
        if path.suffix.casefold() == ".docx":
            scan_bytes(path.read_bytes(), values, path.name)
        else:
            stream_scan(path, values)
    require(sha256(args.exe) == build["sha256"], "EXE changed during embedded scanning")
    rows = [{"path": member, "bytes": path.stat().st_size, "sha256": sha256(path)} for path, member in files]
    readme = (f"# 明镜 {VERSION} 阶段候选\n\n双击 MingJing.exe 打开上传首页，详情见 docs/USER_GUIDE.md。"
              "Token由当前Windows用户重新配置。samples/smart为虚构可分发样例。\n\n"
              "本包为stage_candidate，formal_release=false；开发宿主与少量真实API技术闭环只证明收据列出的范围。"
              "真人金标、未曝光D3和独立Windows仍未执行，未接受全文金融质量或正式发布。\n\n"
              f"对应完整源码在 源码/{source_archive.name}，其清单与EXE的构建输入逐文件核对；"
              "ZIP外部哈希见包外回执。项目AGPLv3与第三方原许可分别保留。\n")
    generated = {"README.md": readme.encode("utf-8")}
    for member, raw in generated.items():
        scan_bytes(raw, values, member)
        rows.append({"path": member, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = {"schema": "mingjing-dev3-candidate.v1", "product": "MingJing", "version": VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(), "entrypoint": "MingJing.exe",
                "delivery_kind": "stage_candidate", "formal_release": False,
                "sha256": build["sha256"], "source_sha256": build["source_sha256"],
                "runtime_manifest_sha256": build["runtime_manifest_sha256"], "acceptance_status": smoke["status"],
                "acceptance_scope": smoke["scope"], "source_inventory_digest": source_receipt["source_inventory_digest"],
                "source_commit": None, "full_source_archive_sha256": source_receipt["archive_sha256"],
                "release_gates": {gate: "not_certified_by_stage" for gate in ("G0", "G1", "G4", "G5", "G6", "G7")},
                "not_executed": list(NOT_EXECUTED),
                "credential_scan": {"status": "passed", "method": "readonly_exact_values_and_decompressed_EXE_PYZ_carrier_full_source",
                                    "references_checked": len(values), "embedded": embedded},
                "files": sorted(rows, key=lambda row: row["path"])}
    generated["MingJing-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    sums = {row["path"]: row["sha256"] for row in rows}
    sums["MingJing-manifest.json"] = hashlib.sha256(generated["MingJing-manifest.json"]).hexdigest()
    generated["SHA256SUMS.txt"] = "".join(h + "  " + member + "\n" for member, h in sorted(sums.items())).encode("utf-8")
    if args.check_only:
        require(before == original_snapshot(), "Original user profile changed during candidate validation")
        return {"status": "validated_scoped_candidate_inputs", "version": VERSION, "formal_release": False,
                "exe_sha256": build["sha256"], "source_sha256": build["source_sha256"], "files": len(rows)}
    staging = out.with_name(out.name + ".dev3-stage-" + uuid.uuid4().hex)
    temporary_zip = destinations[1].with_name(destinations[1].name + ".dev3-stage-" + uuid.uuid4().hex)
    staging.mkdir(parents=True)
    try:
        for path, member in files:
            target = staging / member
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        for member, raw in generated.items():
            scan_bytes(raw, values, member)
            (staging / member).write_bytes(raw)
        for row in rows:
            path = staging / row["path"]
            require(path.stat().st_size == row["bytes"] and sha256(path) == row["sha256"], "Candidate input changed while copying")
        verify_current_build_inputs(root, args.build_receipt)
        verify_candidate_receipts(root, staging / "MingJing.exe", staging / "MingJing-build.json",
                                  staging / "验收记录/dev3-exe-smoke.json", staging / "build-source-lock.json")
        with zipfile.ZipFile(temporary_zip, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(p for p in staging.rglob("*") if p.is_file()):
                archive.write(path, path.relative_to(staging).as_posix(),
                              compress_type=zipfile.ZIP_STORED if path.suffix == ".zip" else zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(temporary_zip) as archive:
            expected = {row["path"]: row["sha256"] for row in rows}
            expected.update({name: hashlib.sha256(raw).hexdigest() for name, raw in generated.items()})
            require(len(archive.namelist()) == len(expected) and set(archive.namelist()) == set(expected), "Candidate ZIP allowlist mismatch")
            for member, expected_hash in expected.items():
                h = hashlib.sha256()
                with archive.open(member) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(block)
                require(h.hexdigest() == expected_hash, "Candidate ZIP member changed")
        require(before == original_snapshot(), "Original user profile changed during packaging")
        require(not any(path.exists() for path in destinations), "Candidate destination appeared concurrently")
        staging.rename(out)
        temporary_zip.rename(destinations[1])
        archive_hash = sha256(destinations[1])
        receipt = {"schema": "mingjing-dev3-candidate-receipt.v1", "status": "packaged_and_hash_verified",
                   "version": VERSION, "formal_release": False, "delivery_kind": "stage_candidate", "directory": str(out),
                   "archive": str(destinations[1]), "archive_bytes": destinations[1].stat().st_size, "archive_sha256": archive_hash,
                   "exe_sha256": build["sha256"], "source_sha256": build["source_sha256"], "not_executed": list(NOT_EXECUTED),
                   "user_profile_unchanged": True, "files": len(expected), "credential_scan": manifest["credential_scan"]}
        with destinations[2].open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        with destinations[3].open("x", encoding="utf-8") as stream:
            stream.write(archive_hash + "  " + destinations[1].name + "\n")
        return receipt
    finally:
        if staging.exists():
            require(staging.parent == out.parent and staging.name.startswith(out.name + ".dev3-stage-"), "Cleanup target invalid")
            shutil.rmtree(staging)
        temporary_zip.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=ROOT / "dist/MingJing.exe")
    parser.add_argument("--build-receipt", type=Path, default=ROOT / "dist/MingJing-build.json")
    parser.add_argument("--source-lock", type=Path, default=ROOT / "dist/build-source-lock.json")
    parser.add_argument("--smoke-receipt", type=Path, required=True)
    parser.add_argument("--full-source-receipt", type=Path, required=True)
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--documents-receipt", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    for name in ("exe", "build_receipt", "source_lock", "smoke_receipt", "full_source_receipt", "documents", "documents_receipt"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    print(json.dumps(create_candidate(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
