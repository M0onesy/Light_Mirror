"""Exercise real loopback redirects without using any model provider API."""
import io
import json
import threading
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from mingjing import http_transport, llm, provider_api


@pytest.fixture
def redirect_servers(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    state = {"hits": [], "location": ""}

    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            state["hits"].append((self.server.label, self.path, self.headers.get("Authorization")))
            if self.server.label == "source" and self.path == "/models":
                self.send_response(302)
                self.send_header("Location", state["location"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            raw = json.dumps({"data": [{"id": "fixture-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    servers, workers = [], []
    for label in ("source", "target"):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        server.label = label
        worker = threading.Thread(target=lambda srv=server: srv.serve_forever(poll_interval=0.02), daemon=True)
        worker.start()
        servers.append(server)
        workers.append(worker)
    yield state, *(f"http://127.0.0.1:{server.server_port}" for server in servers)
    for server, worker in zip(servers, workers):
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_same_origin_redirect_preserves_approved_credentials(redirect_servers):
    state, source, _ = redirect_servers
    state["location"] = source + "/followup"
    with http_transport.urlopen(Request(source + "/models", headers={"Authorization": "Bearer test-only-token"})) as response:
        assert json.load(response)["data"][0]["id"] == "fixture-model"
    assert state["hits"] == [("source", "/models", "Bearer test-only-token"),
                             ("source", "/followup", "Bearer test-only-token")]


def test_cross_origin_redirect_never_reaches_destination(redirect_servers):
    state, source, target = redirect_servers
    state["location"] = target + "/models"
    with pytest.raises(http_transport.HTTPPolicyError, match="redirect"):
        http_transport.urlopen(Request(source + "/models", headers={"Authorization": "Bearer test-only-token"}))
    assert len(state["hits"]) == 1
    assert state["hits"][0][0] == "source"


def test_provider_discovery_uses_guarded_transport(redirect_servers):
    state, source, target = redirect_servers
    state["location"] = target + "/models"
    result = provider_api.discover({"providers": {"fixture": {"base_url": source,
        "credential_ref": "fixture_key", "protocol": "openai-completions", "verify_tls": True}}},
        "fixture", lambda _: "test-only-token")
    assert not result["ok"]
    assert result["error"] == "HTTPPolicyError"
    assert len(state["hits"]) == 1


@pytest.mark.parametrize("target", ["http://provider.example/models", "https://other.example/models",
                                    "https://provider.example:444/models", "https://user:secret@provider.example/models"])
def test_redirect_policy_blocks_downgrade_host_port_and_embedded_credentials(target):
    handler = http_transport.SameOriginRedirectHandler("https://provider.example/models")
    with pytest.raises(http_transport.HTTPPolicyError):
        handler.redirect_request(Request("https://provider.example/models"), None, 302, "Found", Message(), target)


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_timeout_fails_before_network(timeout):
    with pytest.raises(http_transport.HTTPPolicyError):
        http_transport.urlopen("https://fixture.invalid", timeout=timeout)


def test_tls_cannot_be_disabled():
    with pytest.raises(http_transport.HTTPPolicyError, match="TLS"):
        http_transport.urlopen("https://fixture.invalid", verify_tls=False)


def test_legacy_provider_error_cannot_echo_key_into_cli_artifacts(monkeypatch):
    token = "test-only-fixture-key"
    def rejected(*_, **__):
        raise HTTPError("https://fixture.invalid/" + token, 401, "invalid credential " + token, {}, io.BytesIO())
    monkeypatch.setattr(llm, "urlopen", rejected)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    with pytest.raises(llm.LLMUnavailable) as failed:
        llm._request("https://fixture.invalid", token, {}, 1)
    assert token not in str(failed.value)
    assert "401" in str(failed.value)


def test_success_metadata_and_semantics_are_redacted(monkeypatch):
    token = "test-only-fixture-key"
    payload = {"model": token, "usage": {token: "echo " + token}, "choices": [{"finish_reason": "stop",
        "message": {"content": json.dumps([{"source": "echo " + token}])}}]}
    monkeypatch.setattr(llm, "urlopen", lambda *_, **__: io.BytesIO(json.dumps(payload).encode()))
    claims, metadata = llm._request("https://fixture.invalid", token, {}, 1)
    assert token not in json.dumps({"claims": claims, "metadata": metadata})
