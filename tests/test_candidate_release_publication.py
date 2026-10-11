import hashlib
from pathlib import Path

import pytest

from scripts import publish_candidate_release as publication


def test_asset_inventory_requires_corresponding_source(tmp_path):
    (tmp_path / "program.exe").write_bytes(b"program")
    with pytest.raises(publication.PublicationError, match="corresponding_source"):
        publication.asset_inventory(tmp_path, (("executable", "program.exe"),))


@pytest.mark.parametrize("relative", ["../private.txt", "../../elsewhere", "C:/outside.txt", ""])
def test_asset_paths_refuse_escape(tmp_path, relative):
    with pytest.raises(publication.PublicationError):
        publication.checked_path(tmp_path, relative)


@pytest.mark.parametrize("url", ["http://github.com/file", "https://attacker.test/file",
                                 "https://github.com.attacker.test/file", "https://user:password@github.com/file",
                                 "https://github.com:444/file", "//github.com/file"])
def test_signed_download_redirect_refuses_credential_or_host_escape(url):
    with pytest.raises(publication.PublicationError, match="redirect_rejected"):
        publication.download_redirect(url)


@pytest.mark.parametrize("url", ["https://github.com/file", "https://release-assets.githubusercontent.com/file?sig=test"])
def test_signed_download_redirect_accepts_only_secure_github_hosts(url):
    assert publication.download_redirect(url) == url


@pytest.mark.parametrize("path", ["artifacts/program/MingJing.exe", "data/financial-report.pdf", "runs/cache.json",
                                  "evidence/private/run/claims.json", "runtime/finance-carrier/staged/node.exe",
                                  "vendor/harness/node_modules/library/index.js", ".env", "scripts/__pycache__/cached.pyc"])
def test_index_excludes_private_runtime_and_delivery_assets(path):
    assert publication.denied_index_path(path)


@pytest.mark.parametrize("path", ["mingjing/agent.py", "vendor/harness/src/example.ts", "tests/fixtures/example.json",
                                  "docs/project/plan.pdf", "runtime/harness/source_snapshot.json", ".env.example",
                                  "vendor/harness/apps/desktop/.env.windows.example"])
def test_index_keeps_required_public_source(path):
    assert not publication.denied_index_path(path)


class FakeGitHub:
    def __init__(self, commit, assets=(), bad_hash=False, existing=False, extra_asset=False):
        self.commit = commit
        self.bad_hash = bad_hash
        self.patches = []
        self.uploaded = []
        self.bytes_by_id = {}
        self.release = {"id": 10, "assets": list(assets), "draft": True, "prerelease": True,
                        "html_url": "https://github.com/M0onesy/Light_Mirror/releases/tag/test"}
        if extra_asset:
            self.release["assets"].append({"id": 99, "name": "unapproved.bin"})
        self.existing = existing

    def request(self, path, method="GET", data=None):
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": self.commit}}
        if "/git/ref/tags/" in path:
            return {"object": {"sha": self.commit, "type": "commit"}}
        if "?per_page=" in path:
            return [self.release] if self.existing else []
        if method == "POST":
            assert data["draft"] is True and data["prerelease"] is True
            self.release.update(data)
            return self.release
        if method == "PATCH":
            self.patches.append(data)
            self.release.update(data)
            return self.release
        raise AssertionError("Unexpected API call")

    def upload(self, release_id, path):
        identifier = len(self.uploaded) + 1
        self.bytes_by_id[identifier] = path.read_bytes()
        record = {"id": identifier, "name": path.name, "size": len(self.bytes_by_id[identifier]), "state": "uploaded"}
        self.uploaded.append(record)
        return record

    def download_hash(self, identifier):
        content = self.bytes_by_id[identifier]
        return len(content), "0" * 64 if self.bad_hash else hashlib.sha256(content).hexdigest()


def inputs(tmp_path):
    paths = []
    for name, value in (("MingJing.exe", b"program"), ("source.zip", b"source")):
        path = tmp_path / name
        path.write_bytes(value)
        paths.append(({"name": name, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}, path))
    manifest = {"asset_set_sha256": "a" * 64, "frozen_build": {"exe_sha256": paths[0][0]["sha256"]}}
    return manifest, paths


def test_publish_requires_downloaded_bytes_before_visibility(tmp_path):
    manifest, assets = inputs(tmp_path)
    client = FakeGitHub("a" * 40)
    result = publication.publish(client, "a" * 40, manifest, assets, lambda _: None)
    assert len(client.uploaded) == 2
    assert len(client.patches) == 1
    assert client.patches[0]["draft"] is False
    assert result["status"] == "published_prerelease_verified"
    assert all(asset["downloaded_sha256"] == asset["sha256"] for asset in result["assets"])


def test_failed_asset_hash_never_publishes_draft(tmp_path):
    manifest, assets = inputs(tmp_path)
    client = FakeGitHub("a" * 40, bad_hash=True)
    with pytest.raises(publication.PublicationError, match="downloaded_asset_hash_mismatch"):
        publication.publish(client, "a" * 40, manifest, assets, lambda _: None)
    assert client.patches == []


def test_wrong_remote_commit_prevents_release_creation(tmp_path):
    manifest, assets = inputs(tmp_path)
    client = FakeGitHub("b" * 40)
    with pytest.raises(publication.PublicationError, match="remote_main"):
        publication.publish(client, "a" * 40, manifest, assets, lambda _: None)
    assert client.uploaded == []
    assert client.patches == []


def test_existing_release_with_different_inputs_is_never_replaced(tmp_path):
    manifest, assets = inputs(tmp_path)
    client = FakeGitHub("a" * 40, existing=True)
    client.release.update(tag_name=publication.TAG, body="other inputs", target_commitish="a" * 40)
    with pytest.raises(publication.PublicationError, match="does_not_match_inputs"):
        publication.publish(client, "a" * 40, manifest, assets, lambda _: None)
    assert client.uploaded == []
    assert client.patches == []


def test_unexpected_draft_assets_prevent_publication(tmp_path):
    manifest, assets = inputs(tmp_path)
    client = FakeGitHub("a" * 40, extra_asset=True)
    with pytest.raises(publication.PublicationError, match="unexpected_assets"):
        publication.publish(client, "a" * 40, manifest, assets, lambda _: None)
    assert client.uploaded == []
    assert client.patches == []


def test_local_asset_change_prevents_upload(tmp_path):
    manifest, assets = inputs(tmp_path)
    assets[0][1].write_bytes(b"changed")
    client = FakeGitHub("a" * 40)
    with pytest.raises(publication.PublicationError, match="local_asset_changed"):
        publication.publish(client, "a" * 40, manifest, assets, lambda _: None)
    assert client.uploaded == []


def test_api_paths_cannot_send_credentials_to_another_host():
    client = publication.GitHub("test-value")
    with pytest.raises(publication.PublicationError, match="api_path_rejected"):
        client.request("//attacker.test/path")


def test_download_redirect_drops_authorization(monkeypatch):
    class Response:
        headers = {"Content-Type": "application/octet-stream"}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _):
            data, self.data = getattr(self, "data", b"test"), b""
            return data
    class Opener:
        def __init__(self): self.requests = []
        def open(self, request, timeout):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise publication.urllib.error.HTTPError(request.full_url, 302, "redirect",
                                                        {"Location": "https://release-assets.githubusercontent.com/file"}, None)
            return Response()
    client = publication.GitHub("sensitive")
    client.opener = Opener()
    size, sha = client.download_hash(1)
    assert size == 4 and sha == hashlib.sha256(b"test").hexdigest()
    assert client.opener.requests[0].get_header("Authorization") == "Bearer sensitive"
    assert client.opener.requests[1].get_header("Authorization") is None
