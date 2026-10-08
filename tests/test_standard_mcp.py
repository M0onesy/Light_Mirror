"""Official MCP client acceptance with independent Windows stdio processes."""

import json
import os
import sys
from pathlib import Path

import anyio
import pytest

from mingjing.mcp_services import ReadOnlyMCP, SERVICES, load_dispatcher


ROOT = Path(__file__).resolve().parents[1]


async def _cold_start(service, run=None):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = os.environ.copy()
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    # Services are read-only and receive no provider credentials.
    env = {key: value for key, value in env.items() if not any(
        needle in key.upper() for needle in ("API_KEY", "API_TOKEN", "AUTH_TOKEN"))}
    args = ["-m", "mingjing.mcp_services", "--service", service]
    if run:
        args += ["--run", str(run)]
    parameters = StdioServerParameters(command=sys.executable, args=args, cwd=str(ROOT), env=env)
    with anyio.fail_after(25):
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=8) as client:
                initialized = await client.initialize()
                assert initialized.server_info.name == service
                tools = await client.list_tools()
                assert {tool.name for tool in tools.tools} == set(SERVICES[service]["tools"])
                for tool in tools.tools:
                    assert tool.annotations.read_only_hint is True
                    assert tool.annotations.destructive_hint is False
                    assert tool.input_schema["additionalProperties"] is False
                    assert not {"candidate_value", "path", "formula", "role"} & set(tool.input_schema["properties"])
                resources = await client.list_resources()
                assert len(resources.resources) == 2
                summary_uri = next(str(r.uri) for r in resources.resources if str(r.uri).endswith("/summary"))
                summary = json.loads((await client.read_resource(summary_uri)).contents[0].text)
                assert summary["service"] == service
                for resource in resources.resources:
                    json.loads((await client.read_resource(str(resource.uri))).contents[0].text)
                if service == "mj-facts":
                    binding = next(item["binding_id"] for item in summary["bindings"] if item["agreed"])
                    name, params = "mj_get_fact", {"binding_id": binding}
                elif service == "mj-rules":
                    name, params = "mj_get_rule", {"rule_id": "E1"}
                elif service == "mj-judge":
                    name, params = "mj_get_verdicts", {}
                else:
                    name, params = "mj_list_reports", {}
                result = await client.call_tool(name, params)
                assert result.is_error is False
                public = json.loads(result.content[0].text)
                assert "source_path" not in json.dumps(public)
                if service == "mj-rules":
                    assert public.get("found") is not False
                elif service == "mj-facts":
                    assert "fact" in public
                elif service == "mj-judge":
                    assert public["verdicts"]
                else:
                    assert public

                # Reject attempts to submit model-produced facts or identity.
                marker = "sensitive-model-input-must-not-echo"
                for extra in ("candidate_value", "path", "formula", "role"):
                    try:
                        denied = await client.call_tool(name, {**params, extra: marker})
                    except Exception as exc:
                        assert marker not in str(exc)
                    else:
                        assert denied.is_error is True
                        assert marker not in json.dumps(denied.model_dump(), ensure_ascii=False)
                for mutation in ("mj_submit_binding", "mj_submit_report"):
                    try:
                        denied = await client.call_tool(mutation, {})
                    except Exception:
                        pass
                    else:
                        assert denied.is_error is True
                with pytest.raises(Exception):
                    await client.read_resource("file:///unregistered-financial-document")


@pytest.mark.parametrize("service", ["mj-corpus", "mj-facts", "mj-rules", "mj-judge",
                                    "mj-corpus", "mj-facts", "mj-rules", "mj-judge",
                                    "mj-corpus", "mj-facts"])
def test_ten_independent_stdio_cold_starts(service):
    pytest.importorskip("mcp")
    anyio.run(_cold_start, service)


def test_standard_mcp_shared_dispatcher_guards_identity_and_inputs():
    dispatcher = load_dispatcher()
    facade = ReadOnlyMCP("mj-corpus", dispatcher)
    assert facade.call("mj_list_reports", {})
    assert dispatcher.trace[-1]["role"] == "question"
    with pytest.raises(Exception):
        facade.call("mj_list_reports", {"candidate_value": 999999})
    with pytest.raises(Exception):
        facade.call("mj_list_reports", [])
    with pytest.raises(Exception):
        facade.call("mj_submit_binding", {})


def test_frozen_run_readonly_and_tampering_rejected(tmp_path):
    from mingjing.cli import main
    from mingjing.evidence import sha256_file
    out = tmp_path / "frozen"
    assert main(["check", "--draft", str(ROOT / "samples" / "demo_draft.md"),
                 "--corpus", str(ROOT / "samples" / "corpus"),
                 "--report-date", "2025-03-31", "--mode", "kernel", "--out", str(out)]) == 0
    before = {p.name: sha256_file(p) for p in out.iterdir() if p.is_file()}
    facade = ReadOnlyMCP("mj-judge", load_dispatcher(out))
    assert facade.call("mj_get_verdicts", {})["verdicts"]
    assert before == {p.name: sha256_file(p) for p in out.iterdir() if p.is_file()}
    payload = out / "results.json"
    payload.write_text(payload.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen"):
        load_dispatcher(out)
