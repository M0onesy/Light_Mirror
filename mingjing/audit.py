from __future__ import annotations

import hashlib
import json
import platform
import sys
import importlib
import types
import threading
from dataclasses import MISSING, fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any
from .file_io import atomic_write_text, io_path, record_path


def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class AuditLog:
    def __init__(self, out: str | Path, run_id: str):
        self.path = Path(out) / "audit.jsonl"
        self.session_path = Path(out) / "session.jsonl"
        self.run_id = run_id
        self.counter = 0
        self.prev_hash = ""
        self._lock = threading.RLock()
        io_path(self.path.parent).mkdir(parents=True, exist_ok=True)
        if io_path(self.path).is_file() and io_path(self.path).stat().st_size:
            previous = ''
            for line in io_path(self.path).read_text(encoding='utf-8').splitlines():
                entry = json.loads(line)
                saved_hash = entry.pop('event_hash')
                if entry.get('run_id') != run_id or entry.get('prev_event_hash') != previous or digest(entry) != saved_hash:
                    raise ValueError('Cannot append to an invalid or unrelated audit chain')
                previous = saved_hash
                self.counter += 1
            self.prev_hash = previous

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            return self._emit_unlocked(event_type, **fields)

    def _emit_unlocked(self, event_type: str, **fields: Any) -> dict[str, Any]:
        next_counter = self.counter + 1
        event = {"run_id": self.run_id, "event_id": f"event-{next_counter:06d}", "time": datetime.now(timezone.utc).isoformat(), "type": event_type, **fields}
        event["input_hash"] = fields.get("input_hash", digest(fields.get("input", {})))
        event["output_hash"] = fields.get("output_hash", digest(fields.get("output", {})))
        event["prev_event_hash"] = self.prev_hash
        event["event_hash"] = digest(event)
        with io_path(self.path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        self.counter = next_counter
        self.prev_hash = event["event_hash"]
        return event

    def session(self, event_type: str, **fields: Any) -> None:
        event = {"run_id": self.run_id, "time": datetime.now(timezone.utc).isoformat(), "type": event_type, **fields}
        with self._lock:
            with io_path(self.session_path).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def manifest(out: str | Path, run_id: str, inputs: list[str], mode: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    from .evidence import sha256_file
    from .rules import rulepack_root
    rules_metadata = json.loads((rulepack_root() / "sources.json").read_text(encoding="utf-8"))
    files = []
    seen: set[str] = set()
    for item in inputs:
        path = record_path(io_path(item).resolve())
        key = str(path).casefold() if sys.platform == "win32" else str(path)
        if key in seen:
            continue
        seen.add(key)
        if io_path(path).exists() and io_path(path).is_file():
            files.append({"path": str(path), "sha256": sha256_file(path), "bytes": io_path(path).stat().st_size})
    context = getcontext()
    decimal_context = {key: getattr(context, key) for key in ("prec", "rounding", "Emin", "Emax", "capitals", "clamp")}
    decimal_context["traps"] = {signal.__name__: enabled for signal, enabled in context.traps.items()}
    data = {"schema_version": 2, "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "mode": mode, "python": sys.version, "platform": platform.platform(), "inputs": files, "model": model_manifest(out, mode), "rules_version": rules_metadata["version"], "rules_version_hash": rules_metadata["version_hash"], "kernel_fingerprint": kernel_fingerprint(), "decimal_context": decimal_context}
    if extra:
        data.update(extra)
    return data


def model_manifest(out: str | Path, requested_mode: str) -> dict[str, Any]:
    """Describe what actually happened; an attempted API call is not success."""
    records = []
    path = Path(out) / "audit.jsonl"
    if io_path(path).is_file():
        for line in io_path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") == "model_call":
                records.append(event)
    result: dict[str, Any] = {"requested_mode": requested_mode, "mode": "kernel", "status": "not_requested", "request_model": None, "actual_model": None}
    if requested_mode == 'harness':
        # Native analysis legitimately has no draft bindings. Determine the
        # final execution mode from the host's completed state, rather than
        # mistaking its zero mapped draft claims for an API-only fallback.
        result_path = Path(out) / 'results.json'
        if io_path(result_path).is_file():
            completed = json.loads(io_path(result_path).read_text(encoding='utf-8')).get('model', {})
            result.update(status=completed.get('status', 'not_recorded'),
                          mode='harness' if completed.get('mode') == 'harness' and
                          completed.get('status') in {'success', 'partial'} else 'kernel')
            for key in ('semantic_claims', 'mapped_claims', 'source_candidates', 'source_parsed_claims',
                        'binding_submissions', 'request_count', 'prompt_count', 'tokens', 'cost'):
                if key in completed:
                    result[key] = completed[key]
            result['request_models'] = sorted({str(row['model']) for row in records if row.get('model')})
            actual_models = sorted({str(row['actual_model']) for row in records if row.get('actual_model')})
            result['actual_models'] = actual_models
            result['actual_model'] = actual_models[0] if len(actual_models) == 1 else None
            if records:
                result['request_model'] = records[-1].get('model')
            return result
    if not records:
        if requested_mode in {"agent", "harness"}:
            result["status"] = "not_recorded"
        return result
    last = records[-1]
    result.update({"status": last.get("status"), "request_model": last.get("model"), "actual_model": last.get("actual_model"), "semantic_claims": last.get("semantic_claims", 0), "mapped_claims": last.get("mapped_claims", 0)})
    if last.get("status") == "success" and last.get("mapped_claims", 0) > 0:
        result["mode"] = "agent"
    if last.get("fallback"):
        result["fallback"] = last["fallback"]
    for key in ("chunks", "sent_chars", "source_chars", "actual_models", "usage", "temperature", "max_tokens", "thinking"):
        if key in last:
            result[key] = last[key]
    chunk_models = sorted({str(item["actual_model"]) for item in last.get("chunk_stats", []) if item.get("actual_model")})
    if chunk_models:
        result["actual_models"] = chunk_models
        result["actual_model"] = chunk_models[0] if len(chunk_models) == 1 else None
    return result


def kernel_fingerprint() -> dict[str, str]:
    """Lock rule code in source and frozen builds without importing transports."""
    from .evidence import sha256_file
    hashes: dict[str, str] = {}
    for name in ("models", "compute", "decimal_math", "thresholds", "rules", "dimensions", "dimension_tables", "metrics", "facts", "judge", "analysis", "financial_derivations", "taxonomy", "mentions", "scope", "source_semantics", "source_table_reference", "source_business_mode", "source_customer_quotation", "source_product_financial", "source_quarter_quotation", "source_method", "smart.verify", "smart.analysis", "smart.claims", "smart.common", "smart.taxonomy", "smart.ingestion"):
        module = importlib.import_module(f"{__package__}.{name}")
        # Use the same semantic identity in source and PyInstaller builds.
        hashes[f"module:{name}"] = digest(_module_code_data(module))
    from .rules import rulepack_root
    root = rulepack_root()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".json", ".yaml", ".yml", ".csv", ".md"}:
            hashes[f"rule:{path.relative_to(root).as_posix()}"] = sha256_file(path)
    return hashes


def _stable_constant(value: Any) -> Any:
    """Serialize bytecode constants without identities, addresses or caches."""
    if value is MISSING:
        return {"kind": "dataclass_missing"}
    if value is None or isinstance(value, (bool, int, str)):
        return {"kind": type(value).__name__, "value": value}
    if value is Ellipsis:
        return {"kind": "ellipsis"}
    if isinstance(value, bytes):
        return {"kind": "bytes", "value": value.hex()}
    if isinstance(value, float):
        return {"kind": "float", "value": value.hex()}
    if isinstance(value, complex):
        return {"kind": "complex", "real": value.real.hex(), "imag": value.imag.hex()}
    if isinstance(value, Decimal):
        return {"kind": "decimal", "value": str(value)}
    if isinstance(value, types.CodeType):
        return {"kind": "code", "value": _stable_code_data(value)}
    if isinstance(value, types.FunctionType):
        return {"kind": "function", "value": _function_code_data(value)}
    if isinstance(value, (tuple, list)):
        return {"kind": type(value).__name__, "value": [_stable_constant(item) for item in value]}
    if isinstance(value, (frozenset, set)):
        items = [_stable_constant(item) for item in value]
        return {"kind": type(value).__name__, "value": sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))}
    if isinstance(value, dict):
        pairs = [[_stable_constant(key), _stable_constant(item)] for key, item in value.items()]
        return {"kind": "dict", "value": sorted(pairs, key=lambda item: json.dumps(item[0], sort_keys=True, ensure_ascii=False))}
    if isinstance(value, type):
        return {"kind": "type", "value": f"{value.__module__}.{value.__qualname__}"}
    raise TypeError(f"unsupported stable kernel constant: {type(value).__name__}")


def _stable_code_data(code: types.CodeType) -> dict[str, Any]:
    # co_filename, firstlineno and line/debug tables deliberately do not enter
    # the identity: an EXE gets a new temporary extraction path on each launch.
    return {
        "argcount": code.co_argcount, "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount, "nlocals": code.co_nlocals,
        "stacksize": code.co_stacksize, "flags": code.co_flags,
        "code": code.co_code.hex(), "consts": [_stable_constant(item) for item in code.co_consts],
        "names": list(code.co_names), "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars), "cellvars": list(code.co_cellvars),
        "exceptiontable": getattr(code, "co_exceptiontable", b"").hex(),
    }


def _function_code_data(function: Any) -> dict[str, Any]:
    data = {"code": _stable_code_data(function.__code__)}
    # Closure functions include the generated body hidden behind dataclass
    # repr wrappers. Ignore mutable runtime closures such as recursion guards.
    closed_functions = []
    for name, cell in zip(function.__code__.co_freevars, function.__closure__ or ()):
        try:
            item = cell.cell_contents
        except ValueError:
            continue
        if isinstance(item, types.FunctionType) and item is not function:
            closed_functions.append([name, _stable_code_data(item.__code__)])
    if closed_functions:
        data["closed_functions"] = closed_functions
    if function.__defaults__:
        try:
            data["defaults"] = _stable_constant(function.__defaults__)
        except TypeError:
            # Dataclass factory sentinels are represented through fields below.
            pass
    if function.__kwdefaults__:
        try:
            data["kwdefaults"] = _stable_constant(function.__kwdefaults__)
        except TypeError:
            pass
    return data


def _module_code_data(module: Any) -> dict[str, Any]:
    functions, classes, constants = {}, {}, {}
    for name, value in sorted(vars(module).items()):
        if name.startswith("__"):
            continue
        if isinstance(value, types.FunctionType) and value.__module__ == module.__name__:
            functions[name] = _function_code_data(value)
        elif isinstance(value, type) and value.__module__ == module.__name__:
            methods = {}
            for method_name, method in sorted(vars(value).items()):
                if isinstance(method, (staticmethod, classmethod)):
                    method = method.__func__
                if isinstance(method, types.FunctionType):
                    methods[method_name] = _function_code_data(method)
            class_data: dict[str, Any] = {"methods": methods}
            if is_dataclass(value):
                class_data["fields"] = [{"name": field.name, "default": _stable_constant(field.default), "default_factory": _stable_constant(field.default_factory), "init": field.init, "compare": field.compare, "kw_only": field.kw_only} for field in fields(value)]
            classes[name] = class_data
        elif isinstance(value, (type(None), bool, int, str, bytes, float, complex, Decimal, tuple, list, set, frozenset, dict)):
            try:
                constants[name] = _stable_constant(value)
            except TypeError:
                pass
    return {"algorithm": "stable-code-v1", "functions": functions, "classes": classes, "constants": constants}


def finalize_manifest(out: str | Path, run_id: str, inputs: list[str], mode: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call after reports and audit/session completion to freeze all outputs."""
    from .evidence import sha256_file
    root = Path(out)
    data = manifest(root, run_id, inputs, mode, extra)
    outputs = []
    names = ("claims.json", "facts.json", "results.json", "report.md", "report.html", "audit.jsonl", "session.jsonl",
             "agent_lock.json", "agent_state.json", "runtime.json", "bindings.json", "coverage.json", "tool_trace.json", "agent_model_cache.json", "run_request.json", "source_dimensions.json", "clarifications.json")
    candidates = [root / name for name in names]
    candidates.extend(record_path(path) for path in sorted(io_path(root / "evidence").rglob("*")))
    candidates.extend(record_path(path) for path in sorted(io_path(root / 'staged-reparse').rglob('*')))
    # Parsed knowledge is both a frozen input and a generated artifact.
    # Export only cache/lock files nominated by this run's input registry,
    # never unrelated files left in an output directory from an older task.
    resolved_root = record_path(io_path(root).resolve())
    knowledge_lock = resolved_root / 'knowledge_lock.json'
    knowledge_cache = resolved_root / 'knowledge-cache'
    for entry in data['inputs']:
        source = Path(entry['path'])
        if source == knowledge_lock or source.is_relative_to(knowledge_cache):
            candidates.append(root / source.relative_to(resolved_root))
    for path in candidates:
        if io_path(path).is_file():
            outputs.append({"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path), "bytes": io_path(path).stat().st_size})
    data["outputs"] = outputs
    atomic_write_text(root / "run_manifest.json", json.dumps(data, ensure_ascii=False, indent=2))
    return data

