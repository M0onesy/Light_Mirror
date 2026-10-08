"""Four standard, read-only MCP services over the financial dispatcher.

The host registers a frozen run at process launch. Model tool arguments contain
only opaque resource/binding identifiers, never filesystem paths or numbers.
The command defaults to the redistributable fictional demo for cold-start tests.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from .audit import digest
from .coverage import partition_document
from .facts import fact_from_dict
from .financial_tools import DESCRIPTIONS, TOOL_SCHEMAS, FinancialDispatcher
from .judge import Judge
from .mentions import extract_mentions
from .replay import _check_integrity, _claim_from_dict


SERVICES = {
    "mj-corpus": {"role": "question", "tools": ("mj_list_reports", "mj_list_source_gaps", "mj_search_blocks", "mj_read_context")},
    "mj-facts": {"role": "coordinator", "tools": ("mj_get_fact",)},
    "mj-rules": {"role": "question", "tools": ("mj_get_rule", 'mj_kb_list', 'mj_kb_search', 'mj_kb_read')},
    "mj-judge": {"role": "coordinator", "tools": ("mj_verify", "mj_get_verdicts", "mj_analyze")},
}


def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items() if key != "source_path"}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def _sample_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "samples"


def load_dispatcher(run: Path | None = None) -> FinancialDispatcher:
    """Load authorized immutable resources, refusing modified frozen runs."""
    knowledge_registry = None
    if run is None:
        samples = _sample_root()
        text = (samples / "demo_draft.md").read_text(encoding="utf-8")
        raw_facts = json.loads((samples / "corpus" / "facts.json").read_text(encoding="utf-8"))
        facts = [fact_from_dict(row) for row in raw_facts]
        company = facts[0].company if facts else ""
        claims = [replace(claim, binding_status="agreed") for claim in extract_mentions(text, company, "2024Y")]
        results = []
        run_id, resource_lock = "fictional-demo", digest({"text": text, "facts": raw_facts})
        blocks = [{"id": "demo-draft", "page": 1, "text": text}]
        reports = [{"report_id": "fictional-demo-report", "name": "虚构演示财报", "companies": [company], "periods": sorted({f.period for f in facts})}]
        report_date = "2025-03-31"
        task_company, task_period, analysis_scope = company, None, '合并'
        analysis_snapshot = None
        source_gaps = []
        registered = []
    else:
        root = Path(run).resolve(strict=True)
        manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
        if _check_integrity(root, manifest):
            raise ValueError("MCP refused a run whose frozen files or kernel lock changed")
        payload = json.loads((root / "results.json").read_text(encoding="utf-8"))
        facts = [fact_from_dict(row) for row in payload["facts"]]
        claims = [_claim_from_dict(row) for row in payload["claims"]]
        results = copy.deepcopy(payload["results"])
        run_id, resource_lock = payload["run_id"], payload.get("resource_lock") or digest(manifest)
        report_date = payload.get("report_date", "")
        analysis_snapshot = copy.deepcopy(payload.get('analysis'))
        source_gaps = copy.deepcopy(payload.get('source_gaps') or [])
        task_company = payload.get('company') or (analysis_snapshot or {}).get('company') or ''
        task_period = payload.get('period') or None
        analysis_scope = (analysis_snapshot or {}).get('scope') or payload.get('scope_declaration') or '合并'
        if analysis_scope not in {'合并', '母公司'} or (analysis_snapshot and analysis_snapshot.get('company') != task_company):
            raise ValueError('MCP refused a conflicting frozen company or analysis scope')
        blocks, reports = [], []
        lock_path = root / "agent_lock.json"
        if lock_path.is_file():
            if "agent_lock.json" not in {entry.get("path") for entry in manifest.get("outputs", [])}:
                raise ValueError("MCP refused an unregistered agent resource lock")
            registered = json.loads(lock_path.read_text(encoding="utf-8")).get("resources", [])
        else:
            registered = [{"id": "resource-" + digest(item["path"])[:20], **item} for item in manifest.get("inputs", [])]
        from .knowledge import restore_run_knowledge, knowledge_resources
        knowledge_registry = restore_run_knowledge(root, payload, resources=manifest.get('inputs', []))
        knowledge_paths = {row['path'] for row in knowledge_resources(knowledge_registry, root/'knowledge_lock.json')} if knowledge_registry else set()
        # Paths originate only in the host's launch-time resource registry.
        for resource in registered:
            source = Path(resource["path"])
            if str(source.resolve()) in knowledge_paths:
                continue
            if source.suffix.lower() not in {".pdf", ".md", ".txt", ".csv"}:
                continue
            from .evidence import sha256_file
            if not source.is_file() or sha256_file(source) != resource["sha256"]:
                raise ValueError("MCP resource lock changed")
            _, _, source_blocks = partition_document(source)
            for block in source_blocks:
                block["id"] = resource["id"] + ":" + block["id"]
                block["report_id"] = resource["id"]
            blocks.extend(source_blocks)
            dates = {f.publish_date for f in facts if f.source_path == str(source) and f.publish_date}
            reports.append({"report_id": resource["id"], "name": source.name,
                            "sha256": resource["sha256"], "companies": sorted({f.company for f in facts if f.source_path == str(source)}),
                            "periods": sorted({f.period for f in facts if f.source_path == str(source)}),
                            "publish_date": resource.get('publish_date') or (next(iter(dates)) if len(dates) == 1 else '')})
    dispatcher = FinancialDispatcher(run_id, resource_lock, claims, facts, blocks, reports, report_date,
                                     budget={"max_tool_calls": 1000, "max_runtime_s": 1800},
                                     task_company=task_company, task_period=task_period, source_resources=registered,
                                     knowledge_registry=knowledge_registry, source_gaps=source_gaps)
    dispatcher.analysis_scope = analysis_scope
    dispatcher.agreements = {claim.claim_id: {"claim_id": claim.claim_id, "agreed": claim.binding_status == "agreed"}
                             for claim in claims}
    if run is None:
        results = [Judge(dispatcher.store, report_date).verify(claim).to_dict() for claim in claims]
    dispatcher.freeze_results(results)
    if analysis_snapshot:
        dispatcher.freeze_analysis(analysis_snapshot)
    elif task_company:
        from .analysis import analyze_financials
        dispatcher.freeze_analysis(analyze_financials(dispatcher.store, task_company,
            sorted({f.period for f in dispatcher.store.facts}), report_date, analysis_scope))
    return dispatcher


class ReadOnlyMCP:
    """MCP presentation only: all financial execution uses one dispatcher."""

    def __init__(self, service: str, dispatcher: FinancialDispatcher):
        if service not in SERVICES:
            raise ValueError("unknown MingJing MCP service")
        self.service, self.dispatcher = service, dispatcher
        self.role = str(SERVICES[service]["role"])
        self.names = tuple(SERVICES[service]["tools"])
        self.session_id = "mcp-" + uuid.uuid4().hex
        dispatcher.register_session(self.session_id, self.role)
        if service == 'mj-judge':
            dispatcher.sessions[self.session_id]['frozen_analysis_reader'] = True
        self.base_uri = "mingjing://" + service + "/" + dispatcher.run_id

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Stamp trusted identity outside all model-provided arguments."""
        if name not in self.names:
            raise ValueError("tool is not exposed by this read-only service")
        return self.dispatcher.dispatch({"run_id": self.dispatcher.run_id,
            "resource_lock": self.dispatcher.resource_lock, "session_id": self.session_id,
            "role": self.role, "call_id": "mcp-call-" + uuid.uuid4().hex,
            "tool_name": name, "params": {} if arguments is None else arguments})

    def resources(self) -> dict[str, Any]:
        """Public frozen views exclude configuration, credentials and paths."""
        public_facts = [{key: value for key, value in fact.to_dict().items() if key != "source_path"}
                        for fact in self.dispatcher.store.facts]
        return {self.base_uri + "/summary": {"run_id": self.dispatcher.run_id,
            "resource_lock": self.dispatcher.resource_lock, "service": self.service,
            "claims": len(self.dispatcher.claims), "facts": len(public_facts),
            "bindings": [{"binding_id": key, "agreed": value["agreed"]}
                         for key, value in self.dispatcher.agreements.items()]},
            self.base_uri + "/data": self.dispatcher.reports if self.service == "mj-corpus" else
                public_facts if self.service == "mj-facts" else
                self.dispatcher.results if self.service == "mj-judge" else {"source": "bundled_versioned_rulepack"}}


def create_server(service: str, dispatcher: FinancialDispatcher):
    """Construct the official MCP 2.3 server with typed request handlers."""
    import mcp_types as types
    from mcp.server import Server
    facade = ReadOnlyMCP(service, dispatcher)

    async def list_tools(_context, _params):
        return types.ListToolsResult(tools=[types.Tool(name=name, description=DESCRIPTIONS[name],
            inputSchema=TOOL_SCHEMAS[name], annotations=types.ToolAnnotations(readOnlyHint=True,
                destructiveHint=False, idempotentHint=True, openWorldHint=False)) for name in facade.names])

    async def call_tool(_context, params):
        try:
            data = _public(facade.call(params.name, params.arguments))
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
                                        structuredContent={"result": data}, isError=False)
        except Exception:
            # JSON-schema diagnostics can quote model-supplied values. Return a
            # stable error and never echo an arbitrary value into logs/content.
            return types.CallToolResult(content=[types.TextContent(type="text", text="Tool input or frozen resource is unavailable or unauthorized")], isError=True)

    async def list_resources(_context, _params):
        return types.ListResourcesResult(resources=[types.Resource(uri=uri, name=uri.rsplit("/", 1)[-1],
            mimeType="application/json", description="Frozen MingJing read-only resource") for uri in facade.resources()])

    async def read_resource(_context, params):
        registered = facade.resources()
        uri = str(params.uri)
        if uri not in registered:
            raise ValueError("resource URI is not registered")
        return types.ReadResourceResult(contents=[types.TextResourceContents(uri=uri, mimeType="application/json",
            text=json.dumps(registered[uri], ensure_ascii=False))])

    server = Server(service, version="0.4.0", instructions="Read-only financial tools. Values and judgements originate in the deterministic kernel.",
                    on_list_tools=list_tools, on_call_tool=call_tool, on_list_resources=list_resources,
                    on_read_resource=read_resource, get_tool_input_schema=lambda name: TOOL_SCHEMAS.get(name) if name in facade.names else None)
    server.middleware.clear()  # No telemetry exporter or auto-instrumentation.
    return server


async def serve(service: str, run: Path | None = None) -> None:
    from mcp.server.stdio import stdio_server
    server = create_server(service, load_dispatcher(run))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MingJing read-only standard MCP stdio service")
    parser.add_argument("--service", required=True, choices=tuple(SERVICES))
    parser.add_argument("--run", type=Path, help="Host-authorized frozen run directory; never a tool argument")
    args = parser.parse_args(argv)
    try:
        asyncio.run(serve(args.service, args.run))
    except (ImportError, ModuleNotFoundError):
        print("MingJing standard MCP requires the bundled official mcp==2.3.0 runtime", file=sys.stderr)
        return 2
    except Exception as exc:
        print("MingJing MCP startup failed: " + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
