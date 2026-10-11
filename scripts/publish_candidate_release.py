"""Validate a candidate publication, then explicitly publish verified assets.

The default command is local validation only. --publish creates/resumes a draft,
uploads assets, downloads and hashes every asset, and only then publishes it.
Credentials stay in process memory and are never copied into a receipt.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "M0onesy/Light_Mirror"
TAG = "mingjing-1.0.0.dev3"
VERSION = "1.0.0.dev3"
SHA = re.compile(r"^[0-9a-f]{40}$")
USER_AGENT = "MingJing-candidate-publication/1"
ASSETS = (
    ("executable", "artifacts/program/MingJing.exe"),
    ("candidate-package", "artifacts/packages/MingJing-1.0.0.dev3-candidate.zip"),
    ("corresponding-source", "artifacts/source/mingjing-1.0.0.dev3-full-20261010T085922Z.zip"),
    ("core-review", "artifacts/core-review/MingJing-core-review.zip"),
    ("project-plan-docx", "docs/project/明镜MingJing_项目计划书.docx"),
    ("project-plan-pdf", "docs/project/明镜MingJing_项目计划书_初赛评审版.pdf"),
    ("demonstration", "artifacts/demo/明镜新版_实际操作演示.mp4"),
    ("third-party-agpl-source", "artifacts/source/third-party/MingJing-1.0.0.dev3-AGPL-third-party-sources.zip"),
)


class PublicationError(RuntimeError):
    """A safe error code, never a transport body or credential."""


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def checked_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise PublicationError("asset_path_must_be_relative")
    path = root / relative
    if ".." in Path(relative).parts or not path.is_relative_to(root):
        raise PublicationError("asset_path_escape")
    for part in [path, *path.parents]:
        if part == root.parent:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise PublicationError("asset_link_rejected")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise PublicationError("asset_missing_or_outside_root")
    return path


def git(root, *arguments, input_data=None):
    result = subprocess.run(["git", *arguments], cwd=root, input=input_data,
                            capture_output=True, check=False)
    if result.returncode:
        raise PublicationError("git_operation_failed")
    return result.stdout


def load_credential(root):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    result = subprocess.run(["git", "credential", "fill"], cwd=root,
                            input=b"protocol=https\nhost=github.com\n\n",
                            capture_output=True, timeout=30, env=environment)
    if result.returncode:
        raise PublicationError("github_credential_unavailable")
    attributes = dict(line.split("=", 1) for line in result.stdout.decode("utf8").splitlines()
                      if "=" in line)
    token = attributes.get("password", "")
    if not token or not token.isascii() or any(ord(c) < 33 for c in token):
        raise PublicationError("github_credential_invalid")
    return token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_redirect(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in {None, 443}
            or not (host == "github.com" or host.endswith(".githubusercontent.com"))):
        raise PublicationError("asset_download_redirect_rejected")
    return url


class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, method="GET", data=None):
        if not path.startswith("/") or path.startswith("//"):
            raise PublicationError("github_api_path_rejected")
        body = json.dumps(data, ensure_ascii=False).encode("utf8") if data is not None else None
        request = urllib.request.Request("https://api.github.com" + path, data=body, method=method,
                                        headers={"User-Agent": USER_AGENT,
                                                 "Authorization": "Bearer " + self.token,
                                                 "Accept": "application/vnd.github+json",
                                                 "X-GitHub-Api-Version": "2022-11-28",
                                                 "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise PublicationError("github_api_http_" + str(error.code)) from None
        except Exception:
            raise PublicationError("github_api_transport_failed") from None

    def upload(self, release_id, path):
        query = urllib.parse.urlencode({"name": path.name})
        endpoint = "/repos/" + REPOSITORY + "/releases/" + str(release_id) + "/assets?" + query
        connection = http.client.HTTPSConnection("uploads.github.com", timeout=120)
        try:
            connection.putrequest("POST", endpoint)
            connection.putheader("Authorization", "Bearer " + self.token)
            connection.putheader("User-Agent", USER_AGENT)
            connection.putheader("Accept", "application/vnd.github+json")
            connection.putheader("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            connection.putheader("Content-Length", str(path.stat().st_size))
            connection.endheaders()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    connection.send(block)
            response = connection.getresponse()
            if response.status != 201:
                raise PublicationError("github_upload_http_" + str(response.status))
            return json.loads(response.read())
        except PublicationError:
            raise
        except Exception:
            raise PublicationError("github_upload_transport_failed") from None
        finally:
            connection.close()

    def download_hash(self, asset_id):
        url = "https://api.github.com/repos/" + REPOSITORY + "/releases/assets/" + str(asset_id)
        headers = {"Authorization": "Bearer " + self.token, "User-Agent": USER_AGENT,
                   "Accept": "application/octet-stream", "X-GitHub-Api-Version": "2022-11-28"}
        for _ in range(5):
            try:
                response = self.opener.open(urllib.request.Request(url, headers=headers), timeout=120)
                break
            except urllib.error.HTTPError as error:
                if error.code in {301, 302, 303, 307, 308}:
                    url = download_redirect(urllib.parse.urljoin(url, error.headers.get("Location", "")))
                    # Signed object URLs must never receive the GitHub credential.
                    headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
                else:
                    raise PublicationError("github_download_http_" + str(error.code)) from None
            except Exception:
                raise PublicationError("github_download_transport_failed") from None
        else:
            raise PublicationError("github_download_redirect_limit")
        result, size = hashlib.sha256(), 0
        try:
            with response:
                if "application/json" in response.headers.get("Content-Type", ""):
                    raise PublicationError("github_download_returned_metadata")
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    result.update(block)
                    size += len(block)
        except PublicationError:
            raise
        except Exception:
            raise PublicationError("github_download_transport_failed") from None
        return size, result.hexdigest()


def configured_values(root):
    sys.path.insert(0, str(root))
    from mingjing.config import app_root
    profile = app_root()
    protected = [profile / name for name in ("config.json", "secrets.json", "secrets.key", "config.v1.bak.json")]
    before = {p.name: digest(p) if p.is_file() else None for p in protected}
    if (profile / "secrets.json").is_file():
        if json.loads((profile / "secrets.json").read_text("utf8")).get("backend") != "dpapi":
            raise PublicationError("read_only_scan_requires_dpapi")
    from scripts.create_core_review import configured_secret_values
    values = configured_secret_values(root)
    after = {p.name: digest(p) if p.is_file() else None for p in protected}
    if after != before:
        raise PublicationError("credential_scan_changed_profile")
    return values


def denied_index_path(path):
    parts = tuple(part.casefold() for part in Path(path).parts)
    return (not parts or parts[0] in {"artifacts", "data", "runs", "dist", "build"}
            or "node_modules" in parts or "__pycache__" in parts
            or parts[:3] == ("runtime", "finance-carrier", "staged")
            or parts[:2] == ("evidence", "private")
            or path.casefold().endswith((".exe", ".dll", ".pyc", ".zip"))
            or (parts[-1].startswith(".env") and not parts[-1].endswith((".example", ".template"))))


def scan_git(root, extra_values=()):
    from scripts.create_core_review import scan_bytes
    values = [*configured_values(root), *extra_values]
    records = git(root, "ls-files", "--stage", "-z").split(b"\0")
    staged, paths = [], {}
    for record in records:
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        name = path.decode("utf8")
        if stage != b"0" or mode == b"120000" or denied_index_path(name):
            raise PublicationError("index_contains_private_generated_link_or_unmerged_path")
        staged.append(oid.decode("ascii"))
        paths[oid.decode("ascii")] = name
    history = git(root, "-c", "core.quotepath=false", "rev-list", "--objects", "--all").decode("utf8").splitlines()
    for entry in history:
        oid, _, path = entry.partition(" ")
        paths.setdefault(oid, path)
    meta = git(root, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
               input_data=("\n".join(paths) + "\n").encode("ascii")).decode("ascii").splitlines()
    blobs = [(parts[0], int(parts[2])) for line in meta if (parts := line.split())[1] == "blob"]
    if any(size > 100_000_000 for _, size in blobs):
        raise PublicationError("git_history_or_index_blob_exceeds_100mb")
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=root,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for oid, size in blobs:
            process.stdin.write((oid + "\n").encode("ascii"))
            process.stdin.flush()
            actual = process.stdout.readline().split()
            if len(actual) != 3 or int(actual[2]) != size:
                raise PublicationError("git_blob_read_mismatch")
            data = process.stdout.read(size)
            process.stdout.read(1)
            try:
                scan_bytes(data, values, paths[oid])
            except ValueError:
                raise PublicationError("git_plaintext_credential_or_private_key_detected") from None
    finally:
        process.stdin.close()
        process.wait(timeout=30)
    return {"status": "passed", "index_files": len(staged), "history_and_index_blobs": len(blobs),
            "scanned_bytes": sum(size for _, size in blobs), "largest_blob_bytes": max((size for _, size in blobs), default=0),
            "configured_values_checked": len(values), "profile_unchanged": True,
            "method": "exact_credentials_private_keys_decoded_docx_and_index_path_policy"}


def asset_inventory(root, specifications=ASSETS):
    records, names = [], set()
    for role, relative in specifications:
        path = checked_path(root, relative)
        if path.name in names:
            raise PublicationError("duplicate_asset_name")
        names.add(path.name)
        records.append({"role": role, "path": relative, "name": path.name,
                        "bytes": path.stat().st_size, "sha256": digest(path)})
    if not any(r["role"] == "corresponding-source" for r in records):
        raise PublicationError("agpl_corresponding_source_required")
    if any(r["bytes"] <= 0 or r["bytes"] > 2_000_000_000 for r in records):
        raise PublicationError("asset_size_invalid")
    return records


def verify_frozen_receipts(root, records, build):
    indexed = {item["role"]: item for item in records}
    package = json.loads(checked_path(root, "artifacts/packages/MingJing-1.0.0.dev3-candidate.receipt.json").read_text("utf8"))
    source = json.loads(checked_path(root, "artifacts/source/mingjing-1.0.0.dev3-full-20261010T085922Z.receipt.json").read_text("utf8"))
    if (build.get("version") != VERSION or build.get("sha256") != indexed["executable"]["sha256"]
            or build.get("bytes") != indexed["executable"]["bytes"]
            or package.get("archive_sha256") != indexed["candidate-package"]["sha256"]
            or package.get("archive_bytes") != indexed["candidate-package"]["bytes"]
            or package.get("exe_sha256") != indexed["executable"]["sha256"]
            or package.get("source_sha256") != build.get("source_sha256")
            or source.get("archive_sha256") != indexed["corresponding-source"]["sha256"]
            or source.get("archive_bytes") != indexed["corresponding-source"]["bytes"]
            or package.get("formal_release") is not False
            or package.get("credential_scan", {}).get("status") != "passed"
            or source.get("credential_scan", {}).get("status") != "passed"
            or not source.get("credential_scan", {}).get("user_profile_unchanged")):
        raise PublicationError("frozen_build_package_or_corresponding_source_mismatch")


def scan_assets(root, records, extra_values=()):
    from scripts.create_core_review import scan_bytes
    values = [*configured_values(root), *extra_values]
    # Frozen EXE/package receipts already cover their embedded archives. The
    # current upload bytes additionally receive an exact-value streaming scan;
    # ZIP members and DOCX XML are read independently, so compression is not a
    # way to evade the current credential check.
    overlap = max([len(value) for value in values] + [80])
    checked_members = 0
    for record in records:
        path = checked_path(root, record["path"])
        previous = b""
        try:
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    combined = previous + block
                    scan_bytes(combined, values, record["name"] + "::raw-stream")
                    previous = combined[-overlap:]
            if path.suffix.casefold() == ".docx":
                scan_bytes(path.read_bytes(), values, path.name)
            elif path.suffix.casefold() == ".zip":
                with zipfile.ZipFile(path) as archive:
                    if sum(item.file_size for item in archive.infolist()) > 4_000_000_000:
                        raise PublicationError("archive_expansion_limit")
                    for item in archive.infolist():
                        if item.is_dir():
                            continue
                        if item.file_size > 1_000_000_000:
                            raise PublicationError("archive_member_limit")
                        previous = b""
                        with archive.open(item) as stream:
                            for block in iter(lambda: stream.read(1024 * 1024), b""):
                                combined = previous + block
                                scan_bytes(combined, values, item.filename + "::decoded-stream")
                                previous = combined[-overlap:]
                        checked_members += 1
                        if item.filename.casefold().endswith(".docx"):
                            scan_bytes(archive.read(item), values, item.filename)
            elif path.name.endswith(".tar.gz"):
                with tarfile.open(path, "r:gz") as archive:
                    for item in archive:
                        if not item.isfile():
                            continue
                        if item.size > 1_000_000_000:
                            raise PublicationError("archive_member_limit")
                        previous = b""
                        with archive.extractfile(item) as stream:
                            for block in iter(lambda: stream.read(1024 * 1024), b""):
                                combined = previous + block
                                scan_bytes(combined, values, item.name + "::decoded-tar-stream")
                                previous = combined[-overlap:]
                        checked_members += 1
        except ValueError:
            raise PublicationError("asset_plaintext_credential_or_private_key_detected") from None
    return {"status": "passed", "assets_checked": len(records), "zip_members_checked": checked_members,
            "method": "current_exact_credentials_streaming_and_decoded_zip_docx_plus_frozen_embedded_scan"}


def publish(client, commit, manifest, assets, progress):
    remote = client.request("/repos/" + REPOSITORY + "/git/ref/heads/main")
    if remote.get("object", {}).get("sha") != commit:
        raise PublicationError("remote_main_does_not_match_publication_commit")
    marker = "MingJing-publication-inputs: " + manifest["asset_set_sha256"]
    existing = client.request("/repos/" + REPOSITORY + "/releases?per_page=100")
    releases = [item for item in existing if item.get("tag_name") == TAG]
    if releases:
        release = releases[0]
        if marker not in release.get("body", "") or release.get("target_commitish") != commit:
            raise PublicationError("existing_release_does_not_match_inputs")
    else:
        body = ("明镜 " + VERSION + " 阶段候选，非正式 v1.0。\n\n"
                "唯一用户启动入口为 MingJing.exe；完整程序包包含同版对应源码及许可。\n"
                "核心审阅包仅用于审阅，不是独立可运行的原生 Agent。\n\n"
                "真实全文仍为 partial，金融质量、独立真人金标、D3、多供应商及独立 Windows 验收尚待完成。\n"
                "程序构建时的源码锁与本次目录整理提交分别登记；原构建、源码归档和历史记录保持原字节。\n\n"
                "第三方源码附包包含已校验PyMuPDF 1.28.0源码及部分MuPDF子模块。MuPDF主源码未完整内嵌，获取清单明确登记官方tag、18个子模块准确提交和URL；不宣称该附包已经包含完整MuPDF原生源码。\n"
                "MuPDF官方源码：https://mupdf.com/downloads/archive/mupdf-1.28.0-source.tar.gz\n"
                "官方GitHub标签：https://github.com/ArtifexSoftware/mupdf/tree/1.28.0\n\n"
                "Publication commit: `" + commit + "`\nFrozen EXE SHA-256: `" + manifest["frozen_build"]["exe_sha256"]
                + "`\n\n" + marker)
        release = client.request("/repos/" + REPOSITORY + "/releases", "POST",
                                 {"tag_name": TAG, "target_commitish": commit,
                                  "name": "明镜 " + VERSION + " 阶段候选", "body": body,
                                  "draft": True, "prerelease": True, "make_latest": "false"})
    release_id = release["id"]
    progress({"event": "draft_ready", "release_id": release_id})
    remote_assets = {item["name"]: item for item in release.get("assets", [])}
    if set(remote_assets) - {record["name"] for record, _ in assets}:
        raise PublicationError("draft_has_unexpected_assets")
    verified = []
    for record, path in assets:
        if digest(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
            raise PublicationError("local_asset_changed_after_snapshot")
        remote_asset = remote_assets.get(record["name"])
        if remote_asset is None:
            if not release.get("draft"):
                raise PublicationError("published_release_missing_required_asset")
            progress({"event": "uploading", "name": record["name"], "bytes": record["bytes"]})
            remote_asset = client.upload(release_id, path)
        if (remote_asset.get("name") != record["name"] or remote_asset.get("size") != record["bytes"]
                or remote_asset.get("state") != "uploaded"):
            raise PublicationError("uploaded_asset_metadata_mismatch")
        progress({"event": "downloading_for_hash", "name": record["name"]})
        downloaded_size, downloaded_sha = client.download_hash(remote_asset["id"])
        if (downloaded_size, downloaded_sha) != (record["bytes"], record["sha256"]):
            raise PublicationError("downloaded_asset_hash_mismatch")
        verified.append({**record, "asset_id": remote_asset["id"], "downloaded_sha256": downloaded_sha,
                         "downloaded_bytes": downloaded_size, "status": "verified"})
    if release.get("draft"):
        release = client.request("/repos/" + REPOSITORY + "/releases/" + str(release_id), "PATCH",
                                 {"draft": False, "prerelease": True, "make_latest": "false"})
    if release.get("draft") or release.get("prerelease") is not True:
        raise PublicationError("release_not_public_prerelease")
    tag = client.request("/repos/" + REPOSITORY + "/git/ref/tags/" + TAG)
    tag_object = tag.get("object", {})
    if tag_object.get("type") == "tag":
        tag_object = client.request("/repos/" + REPOSITORY + "/git/tags/" + tag_object["sha"]).get("object", {})
    if tag_object.get("type") != "commit" or tag_object.get("sha") != commit:
        raise PublicationError("published_release_tag_commit_mismatch")
    return {"status": "published_prerelease_verified", "release_id": release_id,
            "release_url": release["html_url"], "tag": TAG, "publication_commit": commit,
            "formal_release": False, "assets": verified}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--commit", help="Exact published main commit; required for --publish")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--scan-git-only", action="store_true")
    parser.add_argument("--publish", action="store_true")
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    sys.path.insert(0, str(root))
    commit = arguments.commit or git(root, "rev-parse", "HEAD").decode("ascii").strip()
    if not SHA.fullmatch(commit) or (arguments.publish and not arguments.commit):
        raise PublicationError("exact_publication_commit_required")
    output = arguments.out or root / "artifacts/receipts/github-publication"
    if not output.resolve().is_relative_to(root / "artifacts"):
        raise PublicationError("publication_receipts_must_stay_under_artifacts")
    output.mkdir(parents=True, exist_ok=True)
    token = load_credential(root) if arguments.publish else None
    scan = scan_git(root, [token.encode("utf8")] if token else [])
    write_json(output / "git-publication-scan.json", scan)
    if arguments.scan_git_only:
        print(json.dumps(scan))
        return 0
    records = asset_inventory(root)
    build = json.loads((root / "artifacts/program/MingJing-build.json").read_text("utf8"))
    verify_frozen_receipts(root, records, build)
    asset_scan = scan_assets(root, records, [token.encode("utf8")] if token else [])
    executable = next(item for item in records if item["role"] == "executable")
    serialized = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")
    manifest = {"schema_version": 1, "version": VERSION, "stage_candidate": True, "formal_release": False,
                "repository": "https://github.com/" + REPOSITORY, "publication_commit": commit, "release_tag": TAG,
                "frozen_build": {"exe_sha256": executable["sha256"],
                                 "source_sha256": build.get("source_sha256", build.get("source_sha")),
                                 "build_receipt_sha256": digest(root / "artifacts/program/MingJing-build.json")},
                "asset_set_sha256": hashlib.sha256(serialized).hexdigest(), "assets": records,
                "quality_status": "stage_candidate_full_report_partial_formal_acceptance_pending",
                "third_party_native_source_status": "partial_official_mupdf_sources_not_fully_embedded",
                "source_relationship": "publication commit includes directory consolidation; corresponding-source asset retains original frozen build inputs"}
    manifest_path = output / "candidate-release-manifest.json"
    write_json(manifest_path, manifest)
    manifest_record = {"role": "release-manifest", "path": manifest_path.relative_to(root).as_posix(),
                       "name": manifest_path.name, "bytes": manifest_path.stat().st_size, "sha256": digest(manifest_path)}
    sums_path = output / "SHA256SUMS"
    sums_path.write_text("".join(item["sha256"] + "  " + item["name"] + "\n" for item in [*records, manifest_record]), encoding="utf8")
    sums_record = {"role": "checksums", "path": sums_path.relative_to(root).as_posix(), "name": sums_path.name,
                   "bytes": sums_path.stat().st_size, "sha256": digest(sums_path)}
    all_records = [*records, manifest_record, sums_record]
    receipt = {"timestamp_utc": timestamp(), "status": "local_validation_passed", "remote_mutated": False,
               "publication_commit": commit, "git_scan": scan, "asset_scan": asset_scan, "assets": all_records}
    if arguments.publish:
        receipt["status"] = "publication_in_progress"
        write_json(output / "github-publication-receipt.json", receipt)
        def progress(item):
            print(json.dumps(item, ensure_ascii=False), flush=True)
            receipt["last_event"] = item
            receipt["remote_mutated"] = True
            write_json(output / "github-publication-receipt.json", receipt)
        try:
            result = publish(GitHub(token), commit, manifest,
                             [(record, checked_path(root, record["path"])) for record in all_records], progress)
        except PublicationError as error:
            receipt["status"] = "failed_publication_not_certified"
            receipt["error_code"] = str(error)
            write_json(output / "github-publication-receipt.json", receipt)
            raise
        receipt.update(result)
        receipt["remote_mutated"] = True
    write_json(output / "github-publication-receipt.json", receipt)
    print(json.dumps({"status": receipt["status"], "assets": len(all_records),
                      "receipt": str(output / "github-publication-receipt.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublicationError as error:
        print(json.dumps({"status": "failed", "error_code": str(error)}), file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        # No raw exception message: transports can reflect credentials or URLs.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
