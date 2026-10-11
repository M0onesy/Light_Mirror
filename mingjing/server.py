from __future__ import annotations

"""Local, dependency-free MingJing web server.

The server deliberately keeps the HTTP surface small and runs on loopback by
default.  It delegates numerical work to :mod:`mingjing.cli`; this module is
only an orchestration and presentation layer.
"""

import argparse
from contextlib import nullcontext
import errno
import io
import hashlib
import json
import mimetypes
import math
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from types import SimpleNamespace
import webbrowser
import zipfile
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .audit import digest
from .evidence import sha256_file
from .file_io import io_path
from .config import (AppConfig, ConfigError, effective_storage_paths, path_policy,
                     validate_config, validate_config_path, validate_path)


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
_BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
STATIC_ROOT = next((candidate for candidate in (_BUNDLE_ROOT / "web", PROJECT_ROOT / "web") if candidate.exists()), PROJECT_ROOT / "web")
DEFAULT_HTML = STATIC_ROOT / "index.html"
_FROZEN_RUN_LOCK = threading.Lock()
_PRODUCT_LOG_LOCK = threading.Lock()
_LLM_ENV_KEYS = (
    "MINGJING_LLM_BASE_URL", "MINGJING_LLM_MODEL", "MINGJING_LLM_API_KEY",
    "MINGJING_LLM_CHAT_PATH", "MINGJING_LLM_TIMEOUT_S", "MINGJING_LLM_MAX_TOKENS",
    "MINGJING_LLM_TEMPERATURE", "MINGJING_LLM_THINKING",
)


def _harness_runtime_declared(bundle_root: Path) -> bool:
    """Recognize source and onefile layouts without claiming a health check.

    The onefile build carries a compressed finance runtime, not its extracted
    staged directory. Payload verification and startup remain diagnostic work.
    """
    runtime = bundle_root / 'runtime'
    if (runtime / 'harness' / 'deepseek-harness-sdk-runtime-win-x64.exe').is_file():
        return True
    carrier = runtime / 'finance-carrier'
    if (carrier / 'staged' / 'carrier_manifest.json').is_file():
        return True
    return all((carrier / name).is_file() for name in (
        'carrier_manifest.json', 'payload.json', 'finance-carrier.payload.zip'))


def _redact(value: Any, secret_values: list[str]) -> Any:
    if isinstance(value, str):
        for secret in secret_values:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        sensitive = {'api_key', 'token', 'authorization', 'password', 'secret', 'access_token', 'bearer_token'}
        return {_redact(key, secret_values) if isinstance(key, str) else key:
                '[REDACTED]' if isinstance(key, str) and key.lower() in sensitive else _redact(item, secret_values)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secret_values) for item in value]
    return value


def _append_product_log(config: dict[str, Any], item: dict[str, Any], secret_values: list[str]) -> None:
    logging_config = config.get('logging', {})
    directory = logging_config.get('directory')
    if not directory:
        return
    levels = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40}
    event = str(item.get('event') or '')
    severity = 'ERROR' if event in {'failed', 'error'} else 'WARNING' if event in {'fallback', 'warning'} else 'DEBUG' if event in {'model_chunk', 'tool_event', 'session_event'} else 'INFO'
    if levels[severity] < levels.get(str(logging_config.get('level', 'INFO')).upper(), 20):
        return
    security = config.get('security', {})
    roots = None if security.get('allow_external_paths', True) else list(security.get('allowed_roots') or [])
    if roots == []:
        raise ConfigError('security.allowed_roots is required for product logs')
    folder = validate_path(directory, allowed_roots=roots, kind='dir', follow_symlinks=False)
    folder.mkdir(parents=True, exist_ok=True)
    target = validate_path(folder / 'application.jsonl', allowed_roots=[str(folder)], follow_symlinks=False)
    record = _redact({'level': severity, **item}, secret_values)
    with _PRODUCT_LOG_LOCK:
        with target.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')


def _thinking_config(value: Any, model: str) -> dict[str, str] | None:
    if isinstance(value, dict):
        value = value.get("type")
    lowered = str(value or "auto").strip().lower()
    if lowered in {"disabled", "off", "false", "0"}:
        return {"type": "disabled"}
    if lowered in {"enabled", "on", "true", "1"}:
        return {"type": "enabled"}
    if model.lower().startswith("deepseek"):
        return {"type": "disabled"}
    return None


def _evidence_info(fact: dict[str, Any], evidence_id: str) -> dict[str, Any]:
    match = re.fullmatch(r"([a-f0-9]{12}):p(\d+):(-?\d+),(-?\d+),(-?\d+),(-?\d+):([a-f0-9]+)(?::r\d+c\d+)?", evidence_id)
    if not match:
        raise ValueError("此证据没有可显示的 PDF 页面坐标")
    from .evidence import fact_evidence_references
    roles = fact_evidence_references(fact).get(evidence_id, ())
    return {"evidence_id": evidence_id, "fact": fact, "document_sha12": match.group(1), "page": int(match.group(2)), "bbox": [int(match.group(i)) for i in range(3, 7)], "source_path": fact.get("source_path"), "reference_kinds": list(roles)}


def _source_resource_info(resource: dict[str, Any], evidence_id: str) -> dict[str, Any]:
    info = _evidence_info({'source_path': resource['source_path']}, evidence_id)
    info['document_sha256'] = resource.get('document_sha256')
    info.update({key: resource[key] for key in ('reference_kinds', 'quote_text', 'quote_sha256') if key in resource})
    if resource.get('reference_status') == 'source_definition_unresolved':
        info.update({key: resource[key] for key in (
            'quote_text', 'quote_sha256', 'report_id', 'source_gap_ids', 'title') if key in resource})
        info.update(reference_status='source_definition_unresolved', eligible_as_fact=False,
                    authorizes_judgement=False, image_available=True)
    return info


def _evidence_png(info: dict[str, Any], config: dict[str, Any]) -> bytes:
    # The source path comes exclusively from a saved kernel fact. A browser
    # cannot supply an arbitrary path, page number, or rectangle here.
    security = config.get("security", {})
    roots = None if security.get("allow_external_paths", True) else list(security.get("allowed_roots") or [])
    if roots == []:
        raise ValueError("security.allowed_roots is required")
    source = validate_path(str(info.get("source_path") or ""), allowed_roots=roots, must_exist=True, kind="file", follow_symlinks=bool(security.get("follow_symlinks")))
    if source.suffix.lower() != ".pdf":
        raise ValueError("此证据源不是 PDF")
    # Bind the rendered document to the exact bytes whose identity we check.
    # Checking a path and subsequently opening it permits an external corpus
    # update to replace the PDF between those operations. Keep one bounded
    # immutable snapshot instead; later path changes cannot alter this page.
    max_source_bytes = 256 * 1024 * 1024
    with source.open('rb') as stream:
        if os.fstat(stream.fileno()).st_size > max_source_bytes:
            raise ValueError("PDF 过大，无法安全生成页级预览；请在原始 PDF 中查看证据页")
        source_bytes = stream.read(max_source_bytes + 1)
    if len(source_bytes) > max_source_bytes:
        raise ValueError("PDF 过大，无法安全生成页级预览；请在原始 PDF 中查看证据页")
    document_sha256 = hashlib.sha256(source_bytes).hexdigest()
    expected_sha256 = info.get('document_sha256')
    if (not document_sha256.startswith(info["document_sha12"]) or
            expected_sha256 and document_sha256 != expected_sha256):
        raise ValueError("证据源文件已修改，SHA-256 不匹配")
    import fitz
    with fitz.open(stream=source_bytes, filetype='pdf') as doc:
        page_number = int(info["page"])
        if page_number < 1 or page_number > len(doc):
            raise ValueError("证据页码超出 PDF 范围")
        page = doc[page_number - 1]
        rectangle = fitz.Rect(info["bbox"]) & page.rect
        if not rectangle.is_empty:
            page.draw_rect(rectangle, color=(1, 0.2, 0), width=2, fill=(1, 0.9, 0), fill_opacity=0.22, overlay=True)
        return page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False).tobytes("png")


def _prepare_demo(config: LocalConfig) -> dict[str, Any]:
    cfg = config.get()
    root = Path(str(cfg.get("workspace", {}).get("data_root") or _default_root() / "workspace")) / "demo"
    source = next((path for path in (_BUNDLE_ROOT / "samples", PROJECT_ROOT / "samples") if path.is_dir()), None)
    if source is None:
        raise ValueError("内置演示样例缺失，请重新安装当前程序")
    security = cfg.get("security", {})
    roots = None if security.get("allow_external_paths", True) else list(security.get("allowed_roots") or [])
    if roots == []:
        raise ValueError("security.allowed_roots is required")
    root = validate_path(root, allowed_roots=roots, follow_symlinks=bool(security.get("follow_symlinks")))
    for item in source.rglob("*"):
        if item.is_file() and item.suffix.lower() in {".md", ".txt", ".json", ".pdf", ".csv"}:
            target = root / item.relative_to(source)
            validate_path(target, allowed_roots=[str(root)], follow_symlinks=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
    facts_path = root / "corpus" / "facts.json"
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    entries = facts.get("facts", []) if isinstance(facts, dict) else facts
    for fact in entries:
        filename = Path(str(fact.get("source_path") or "demo_annual.pdf")).name
        pdf = root / "corpus" / filename
        if pdf.is_file():
            fact["source_path"] = str(pdf)
    facts_path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    company = next((fact.get("company") for fact in entries if fact.get("company")), "")
    dates = [str(fact.get("publish_date")) for fact in entries if fact.get("publish_date")]
    periods = [str(fact.get("period")) for fact in entries if fact.get("period")]
    return {"draft": str(root / "demo_draft_defined.md"), "corpus": str(root / "corpus"), "company": company,
            "period": max(periods, default=""), "report_date": max(dates, default=""), "mode": "kernel",
            "message": "演示使用可再分发的合成财报与内置草稿，纯内核运行。"}


def _conversation_export_files(task, sessions) -> dict[str, bytes]:
    """Collect only registered, immutable message artifacts for this run."""
    files: dict[str, bytes] = {}
    if sessions is None:
        return files
    with sessions.lock:
        for path in sessions.root.glob('session-*.json'):
            validate_path(path, allowed_roots=[str(sessions.root)], kind='file', follow_symlinks=False)
            state = json.loads(path.read_text(encoding='utf-8'))
            if state.get('run_id') != task.id or Path(state.get('run_path', '')).resolve() != task.out.resolve():
                continue
            sid = state['session_id']
            if sessions.path(sid).resolve() != path.resolve():
                raise ValueError('Invalid conversation registration')
            if sid in sessions.inflight or sid in sessions.active:
                raise ValueError('会话仍在运行，请等待回答完成后导出')
            files[f'conversations/{path.name}'] = path.read_bytes()
            for message in state.get('messages', []):
                if message.get('role') != 'assistant':
                    continue
                mid = message.get('message_id')
                if not isinstance(mid, str) or not re.fullmatch(r'message-[0-9]{4,12}', mid):
                    continue
                root = validate_path(sessions.root/sid/mid, allowed_roots=[str(sessions.root/sid)], kind='dir', follow_symlinks=False)
                agent_hash = message.get('agent_message_manifest_sha256')
                source_hash = message.get('source_reference_manifest_sha256')
                manifest_name, expected = ('message_manifest.json', agent_hash) if agent_hash else ('source_references.json', source_hash)
                if not expected:
                    continue
                manifest_path = validate_path(root/manifest_name, allowed_roots=[str(root)], must_exist=True, kind='file', follow_symlinks=False)
                if sha256_file(manifest_path) != expected:
                    raise ValueError('Frozen conversation manifest changed')
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                if any(manifest.get(key) != state.get(key) for key in ('session_id', 'run_id', 'resource_lock')) or manifest.get('message_id') != mid:
                    raise ValueError('Conversation resource identity changed')
                entries = manifest.get('outputs', []) if agent_hash else [
                    {'path': row['image'], 'sha256': row['image_sha256']} for row in manifest.get('references', []) if row.get('image')]
                prefix = f'conversations/{sid}/{mid}/'
                files[prefix+manifest_name] = manifest_path.read_bytes()
                for entry in entries:
                    relative = PurePosixPath(entry['path'])
                    if (relative.is_absolute() or relative.as_posix() != entry['path'] or
                            any(part in {'.', '..'} or ':' in part or '\\' in part for part in relative.parts)):
                        raise ValueError('Invalid conversation output registry')
                    item = validate_path(root/entry['path'], allowed_roots=[str(root)], must_exist=True, kind='file', follow_symlinks=False)
                    raw = item.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != entry['sha256'] or ('bytes' in entry and len(raw) != entry['bytes']):
                        raise ValueError('Frozen conversation output changed')
                    name = prefix+entry['path']
                    if name in files:
                        raise ValueError('Duplicate conversation export entry')
                    files[name] = raw
    return files


def _export_run(task: RunTask, config: LocalConfig, sessions=None) -> bytes:
    names = {"claims.json", "facts.json", "results.json", "report.md", "report.html", "audit.jsonl", "session.jsonl",
             "run_manifest.json", "web_events.jsonl", "replay.json", "replay_cache.json", "agent_replay.json",
              "coverage.json", "bindings.json", "agent_lock.json", "agent_state.json", "runtime.json", "tool_trace.json", "agent_model_cache.json", "run_request.json", "clarifications.json", "scope_clarifications.json", "source_dimensions.json"}
    cfg = config.get()
    secrets_to_check = [config.secret(str(cfg.get(section, {}).get(key) or default)) for section, key, default in (
        ("llm", "api_key_ref", "llm_api_key"), ("harness", "api_key_ref", "harness_api_key"), ("server", "bearer_token_ref", "server_bearer"))]
    secrets_to_check.extend(config.secret(str(p.get('credential_ref', ''))) for p in cfg.get('providers', {}).values())
    manifest_file = task.out / 'run_manifest.json'
    frozen_outputs: dict[str, dict[str, Any]] = {}
    if manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        for entry in manifest.get('outputs', []):
            relative = PurePosixPath(entry['path'])
            if (relative.is_absolute() or relative.as_posix() != entry['path'] or
                    any(part in {'.', '..'} or ':' in part or '\\' in part for part in relative.parts) or
                    entry['path'] in frozen_outputs):
                raise ValueError('Invalid frozen output registry')
            source = validate_path(task.out / entry['path'], allowed_roots=[str(task.out)],
                                   must_exist=True, kind='file', follow_symlinks=False)
            if source.stat().st_size != entry['bytes'] or sha256_file(source) != entry['sha256']:
                raise ValueError('Frozen output changed; export refused')
            frozen_outputs[entry['path']] = entry
    output = io.BytesIO()
    conversations = _conversation_export_files(task, sessions)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in task.out.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(task.out)
            if relative.as_posix() not in frozen_outputs and str(relative) not in names and relative.parts[0] not in {"input", "evidence"}:
                continue
            if relative.parts[0] in {"input", "evidence"} and item.suffix.lower() not in {".pdf", ".png", ".jpg", ".json", ".md", ".txt", ".csv"}:
                continue
            validate_path(item, allowed_roots=[str(task.out)], must_exist=True, kind="file", follow_symlinks=False)
            raw = item.read_bytes()
            if any(secret and secret.encode("utf-8") in raw for secret in secrets_to_check):
                raise ValueError("运行产物含未脱敏密钥，已阻止导出；请检查并清理本次运行日志")
            archive.writestr(relative.as_posix(), raw)
        results_file = task.out / 'results.json'
        if results_file.is_file():
            source_bytes = results_file.read_bytes()
            payload = json.loads(source_bytes)
            if isinstance(payload, dict) and isinstance(payload.get('results'), list) and isinstance(payload.get('claims'), list):
                from .execution import project_execution
                archive.writestr('execution_view.json', json.dumps({'schema_version': 'run-export-execution.v1',
                    'source_results_sha256': hashlib.sha256(source_bytes).hexdigest(),
                    'frozen': (task.out / 'run_manifest.json').is_file(), 'execution': project_execution(payload)},
                    ensure_ascii=False, indent=2).encode('utf-8'))
        for name, raw in conversations.items():
            if any(secret and secret.encode('utf-8') in raw for secret in secrets_to_check):
                raise ValueError('Conversation export contains a plaintext credential')
            archive.writestr(name, raw)
    raw_archive = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(raw_archive)) as archive:
        for relative, entry in frozen_outputs.items():
            raw = archive.read(relative)
            if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('Export did not preserve all frozen outputs')
        if any(archive.read(name) != raw for name, raw in conversations.items()):
            raise ValueError('Export did not preserve registered conversation artifacts')
    return raw_archive


def _default_root() -> Path:
    # Keep user data outside a one-file bundle and source checkout.
    if os.environ.get("MINGJING_HOME"):
        return Path(os.environ["MINGJING_HOME"]).expanduser().resolve()
    value = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if value:
        return Path(value) / "MingJing"
    return Path.home() / ".mingjing"


def _default_config() -> dict[str, Any]:
    root = _default_root()
    workspace = root / "workspace"
    return {
        "version": 1,
        "revision": 0,
        "workspace": {
            "data_root": str(workspace),
            "draft_path": "",
            "corpus_root": "",
            "corpus_index": "",
            "knowledge_root": "",
            "facts_path": "",
            "output_root": str(workspace / "runs"),
            "temp_root": str(workspace / "tmp"),
        },
        "run": {"mode": "kernel", "company": "", "period": "", "report_date": "", "scope": "合并", "nr": "未扣非", "timeout_s": 600, "retries": 0},
        "llm": {"enabled": False, "provider": "openai", "base_url": "", "chat_path": "/chat/completions", "model": "", "api_key_ref": "llm_api_key", "temperature": 0, "max_tokens": 4096, "timeout_s": 45, "verify_tls": True},
        "harness": {"enabled": False, "executable": "", "home": "", "profile": "sdk-minimal", "patch_file": "", "working_dir": "", "timeout_s": 600, "mcp_enabled": False, "disable_network": True, "disable_telemetry": True, "disable_shell": True, "disable_code_mode": True, "api_key_ref": "harness_api_key"},
        "server": {"host": "127.0.0.1", "port": 0, "auto_open_browser": True, "bearer_token_required": False, "bearer_token_ref": "server_bearer"},
        "logging": {"level": "INFO", "directory": str(root / "logs"), "retain_runs": 50, "retain_days": 90, "redact_secrets": True},
        "security": {"allowed_roots": [str(workspace)], "allow_external_paths": True, "follow_symlinks": False, "max_upload_mb": 100},
    }


class LocalConfig:
    """Small compatibility wrapper around the config agent's ConfigManager."""

    def __init__(self) -> None:
        self._manager = None
        try:
            from .config import ConfigManager  # type: ignore
            self._manager = ConfigManager()
            self._data = self._manager.public_dict()
        except (ImportError, ModuleNotFoundError):
            self.path = _default_root() / "config.json"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._data = _default_config()
        self._lock = threading.RLock()

    def public(self) -> dict[str, Any]:
        with self._lock:
            if self._manager is not None:
                return self._manager.public_dict()
            data = json.loads(json.dumps(self._data, ensure_ascii=False))
            # Never expose values that look like credentials through this API.
            for section in ("llm", "harness", "server"):
                if isinstance(data.get(section), dict):
                    for key in list(data[section]):
                        if "key" in key.lower() or "token" in key.lower() or "secret" in key.lower():
                            data[section][key] = ""
            return data

    def get(self) -> dict[str, Any]:
        with self._lock:
            if self._manager is not None:
                return self._manager.to_dict() if hasattr(self._manager, "to_dict") else self._manager.public_dict()
            return self._data

    def update(self, patch: dict[str, Any], expected_revision: int | None = None) -> dict[str, Any]:
        with self._lock:
            if self._manager is not None:
                value = self._manager.update_from_dict(patch, expected_revision=expected_revision)
                return value.to_dict() if hasattr(value, "to_dict") else (value if isinstance(value, dict) else self._manager.public_dict())
            current = int(self._data.get("revision", 0))
            if expected_revision is not None and expected_revision != current:
                raise ValueError(f"revision conflict: expected {expected_revision}, current {current}")
            _deep_merge(self._data, patch)
            self._data["revision"] = current + 1
            self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            return self.public()

    def secret(self, ref: str) -> str:
        if not ref:
            return ""
        if self._manager is not None:
            try:
                return self._manager.secret_store.get(ref) or ""
            except Exception:
                return ""
        # Environment fallback intentionally avoids writing plaintext secrets.
        return os.environ.get("MINGJING_SECRET_" + ref.upper(), "")

    def set_secret(self, ref: str, value: str) -> None:
        if self._manager is not None:
            self._manager.secret_store.set(ref, value)
            return
        # Without DPAPI, preserve a process-only value.
        os.environ["MINGJING_SECRET_" + ref.upper()] = value


def _deep_merge(dst: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value


def _json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    raw = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    if handler.headers.get('Transfer-Encoding') is not None:
        raise ValueError('Transfer-Encoding is not supported')
    lengths = (handler.headers.get_all('Content-Length', []) if hasattr(handler.headers, 'get_all')
               else [handler.headers['Content-Length']] if 'Content-Length' in handler.headers else [])
    if len(lengths) > 1:
        raise ValueError('A single Content-Length is required')
    value = lengths[0] if lengths else '0'
    if not re.fullmatch(r'[0-9]{1,10}', value):
        raise ValueError('Content-Length must be a non-negative decimal integer')
    length = int(value)
    if length > 8 * 1024 * 1024:
        raise ValueError("request body too large")
    connection = getattr(handler, 'connection', None)
    old_timeout = connection.gettimeout() if connection is not None else None
    try:
        if connection is not None:
            connection.settimeout(15)
        raw = handler.rfile.read(length) if length else b"{}"
    finally:
        if connection is not None:
            connection.settimeout(old_timeout)
    if length and len(raw) != length:
        raise ValueError('Incomplete request body')
    def reject_nonfinite(_value: str):
        raise ValueError('JSON numbers must be finite')
    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError('JSON numbers must be finite')
        return parsed
    value = json.loads(raw.decode("utf-8"), parse_constant=reject_nonfinite, parse_float=finite_float)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _effective_run_request(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Resolve task coordinates once; defaults never prove semantic scope."""
    value = dict(request)
    workspace, run = config.get('workspace', {}), config.get('run', {})
    for field, setting in (('draft', 'draft_path'), ('corpus', 'corpus_root'), ('facts', 'facts_path'),
                           ('knowledge_root', 'knowledge_root')):
        if value.get('material_profile') == 'smart' and field in {'draft', 'corpus', 'facts'}:
            continue
        if not value.get(field) and workspace.get(setting):
            if field == 'draft' and value.get('task_type') == 'analysis':
                continue
            value[field] = workspace[setting]
    for field in ('company', 'period', 'report_date', 'mode'):
        if value.get('material_profile') == 'smart':
            continue
        if not value.get(field) and run.get(field):
            value[field] = run[field]
    value.setdefault('mode', 'kernel')
    value.setdefault('task_type', 'check')
    # A configured scope preference is not an explicit statement in the draft.
    # Only an actual scope_declaration or original header can bind that field.
    return value


class RunTask:
    def __init__(self, run_id: str, request: dict[str, Any], root: Path, config: LocalConfig,
                 *, config_snapshot: dict[str, Any] | None = None) -> None:
        self.id, self.root, self.config = run_id, root, config
        # Freeze routing and security at queue time, not when a worker happens
        # to start. UI changes affect subsequent tasks only.
        self.config_snapshot = json.loads(json.dumps(config.get() if config_snapshot is None else config_snapshot, ensure_ascii=False))
        self.request = _effective_run_request(json.loads(json.dumps(request, ensure_ascii=False)), self.config_snapshot)
        self.status = "queued"
        self.error = ""
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.events: list[dict[str, Any]] = []
        self.cancelled = threading.Event()
        self._resume_lock = threading.Lock()
        self._secret_values: list[str] = []
        self.thread = threading.Thread(target=self._run, name=f"mingjing-{run_id}", daemon=True)

    def emit(self, event: str, **fields: Any) -> None:
        item = _redact({"time": time.time(), "run_id": self.id, "event": event, **fields}, self._secret_values)
        self.events.append(item)
        try:
            event_path = validate_config_path(self.config_snapshot, self.out / "web_events.jsonl",
                                              field='task.events', kind='file', storage=True)
            event_path.parent.mkdir(parents=True, exist_ok=True)
            with event_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception:
            pass
        try:
            _append_product_log(self.config_snapshot, item, self._secret_values)
        except Exception as exc:
            # Keep the numerical task and its own audit available; disclose a
            # product-log failure instead of leaking an exception payload.
            if not getattr(self, '_log_write_failure_reported', False):
                self._log_write_failure_reported = True
                self.events.append({'time': time.time(), 'run_id': self.id, 'event': 'warning',
                                    'reason': 'product_log_unavailable', 'error_class': type(exc).__name__})

    def _task_secret(self, reference: str) -> str | None:
        value = self.config.secret(reference)
        if value and value not in self._secret_values:
            self._secret_values.append(value)
        return value

    @property
    def out(self) -> Path:
        # Runs are always contained in the configured run root.  The client
        # may choose a label, but cannot redirect output to an arbitrary path.
        return self.root / self.id

    def start(self) -> None:
        self.thread.start()

    def resume(self, *, lifecycle_lock=None, is_closing=None) -> None:
        with self._resume_lock:
            self._resume(lifecycle_lock=lifecycle_lock, is_closing=is_closing)

    def _resume(self, *, lifecycle_lock=None, is_closing=None) -> None:
        if self.thread.is_alive():
            raise ValueError('任务仍在运行')
        # Start a new cancellation scope before the potentially long read-only
        # preflight. A concurrent cancel must never be erased by a late clear().
        self.cancelled = threading.Event()
        state_path = validate_path(self.out/'agent_state.json', allowed_roots=[str(self.out)],
                                   kind='file', follow_symlinks=False)
        lock_path = validate_path(self.out/'agent_lock.json', allowed_roots=[str(self.out)],
                                  kind='file', follow_symlinks=False)
        if not state_path.is_file() or not lock_path.is_file():
            raise ValueError('该任务没有可恢复的原生 Agent 状态')
        state = json.loads(state_path.read_text(encoding='utf-8'))
        locked = json.loads(lock_path.read_text(encoding='utf-8'))
        if (not isinstance(state, dict) or not isinstance(locked, dict)
                or state.get('run_id') != self.id
                or not isinstance(locked.get('resource_lock'), str)
                or state.get('resource_lock') != locked['resource_lock']
                or digest({key: value for key, value in locked.items() if key != 'resource_lock'}) != locked['resource_lock']):
            raise ValueError('检查点身份或材料锁不匹配，原任务保留只读；请新建任务')
        if state.get('stage') == 'frozen' or (self.out/'run_manifest.json').exists():
            raise ValueError('冻结结果保留只读；请补材料并新建任务版本')
        from .audit import kernel_fingerprint
        from .agent import orchestration_fingerprint, validate_task_semantics
        if (not isinstance(state.get('request'), dict) or not isinstance(locked.get('config'), dict)
                or locked.get('kernel') != kernel_fingerprint()
                or locked.get('agent') != orchestration_fingerprint(locked['config'])):
            raise ValueError('内核、Agent 或运行时锁已改变，原任务保留只读；请新建任务')
        validate_task_semantics(locked, state['request'])
        accepted_path = validate_path(self.out/'run_request.json', allowed_roots=[str(self.out)],
                                      kind='file', follow_symlinks=False)
        if accepted_path.is_file():
            # The accepted Web request and the Agent checkpoint must name the
            # same business task even if the old draft path was external.
            validate_task_semantics(locked, json.loads(accepted_path.read_text(encoding='utf-8')))
        resources = locked.get('resources')
        if not isinstance(resources, list) or any(not isinstance(resource, dict) for resource in resources):
            raise ValueError('检查点材料清单无效，原任务保留只读；请新建任务')
        drafts = [resource for resource in resources if resource.get('id') == 'draft']
        if len(drafts) != 1:
            raise ValueError('检查点必须包含唯一冻结草稿，原任务保留只读；请新建任务')
        frozen_draft = validate_path(drafts[0].get('path') or '', allowed_roots=[str(self.out/'input')],
                                     must_exist=True, kind='file', follow_symlinks=False)
        for resource in resources:
            if self.cancelled.is_set():
                raise InterruptedError('用户取消了恢复预检')
            source = validate_config_path(locked['config'], resource.get('path') or '',
                                          field='resume.resource', must_exist=True, kind='file')
            if not source.is_file() or sha256_file(source) != resource['sha256']:
                raise ValueError('原材料已改变，原任务保留只读；请新建任务')
        # Shutdown and task publication share the host lifecycle lock. Hashing
        # remains outside it so cancellation and shutdown stay responsive.
        with lifecycle_lock if lifecycle_lock is not None else nullcontext():
            if is_closing is not None and is_closing():
                raise ValueError('服务正在退出，不能恢复任务')
            if self.cancelled.is_set():
                raise InterruptedError('用户取消了恢复预检')
            previous = (self.request, self.config_snapshot, getattr(self, 'resume_requested', False),
                        self.status, self.error, self.finished_at, self.thread)
            self.request = dict(state['request'], draft=str(frozen_draft), mode='harness')
            self.config_snapshot = locked['config']
            self.resume_requested = True
            self.status, self.error = 'queued', ''
            self.finished_at = None
            self.thread = threading.Thread(target=self._run, name=f'mingjing-resume-{self.id}', daemon=True)
            try:
                self.start()
            except Exception:
                (self.request, self.config_snapshot, self.resume_requested,
                 self.status, self.error, self.finished_at, self.thread) = previous
                raise

    def _run(self) -> None:
        self.status = "running"
        if getattr(self, 'resume_requested', False):
            self.agent = None
        try:
            cfg = self.config_snapshot
            if self.cancelled.is_set():
                raise RuntimeError("run cancelled")
            if getattr(self, 'resume_requested', False):
                # Resuming reuses immutable local inputs and accepted request
                # bytes. The normal launch path below would copy an edited
                # external draft over the frozen one before Agent preflight.
                from .agent import FinancialAgent
                agent = FinancialAgent(self.request, cfg, self._task_secret, self.out,
                                       run_id=self.id, emit=self.emit, cancel_event=self.cancelled)
                self.agent = agent
                result = agent.run(resume=True)
                self.request = dict(agent.request, mode='harness')
                self.status = 'cancelled' if self.cancelled.is_set() else 'partial' if result.get('partial') else 'completed'
                self.emit('completed', result=str(self.out / 'results.json'), partial=bool(result.get('partial')),
                          native_agent=result.get('model', {}).get('status') == 'success')
                return
            security = cfg.get("security", {}) if isinstance(cfg, dict) else {}
            allowed = list(security.get("allowed_roots") or [])
            follow = bool(security.get("follow_symlinks", False))
            external = bool(security.get("allow_external_paths", True))
            roots = allowed if not external else None
            if not external and not roots:
                raise ValueError("security.allowed_roots is required when external paths are disabled")
            corpus = validate_path(str(self.request.get("corpus") or ""), allowed_roots=roots, must_exist=True, kind="any", follow_symlinks=follow)
            from .materials import normalize_corpus_additions
            self.request['corpus_additions'] = normalize_corpus_additions(
                self.request.get('corpus_additions'), corpus, allowed_roots=roots, follow_symlinks=follow)
            if self.request.get("facts"):
                self.request["facts"] = str(validate_path(str(self.request["facts"]), allowed_roots=roots, must_exist=True, kind="file", follow_symlinks=follow))
            if self.request.get('knowledge_root'):
                self.request['knowledge_root'] = str(validate_path(
                    self.request['knowledge_root'], allowed_roots=roots, must_exist=True,
                    kind='dir', follow_symlinks=follow))
            # Result directories are created by the service and use the same
            # path policy as input resources.  The browser normally leaves
            # ``out`` unset; callers may provide one only when it passes the
            # configured allowed-root policy.
            if self.request.get("out") and Path(str(self.request["out"])).resolve() != self.out.resolve():
                raise ValueError("output path is assigned by the service; configure workspace.output_root instead")
            self.request["out"] = str(validate_config_path(cfg, self.out, field='task.output', kind='dir', storage=True))
            # Keep an immutable local copy of the draft for audit/replay while
            # leaving the user-selected corpus outside the workspace.
            input_dir = validate_config_path(cfg, self.out / "input", field='task.input', kind='dir', storage=True)
            analysis_intent = self.request.get('task_type') == 'analysis' and not self.request.get('draft')
            if analysis_intent:
                draft = input_dir / 'financial_analysis.md'
            else:
                draft = validate_config_path(cfg, str(self.request.get("draft") or ""),
                                             field='task.draft', must_exist=True, kind='file')
            local_draft = validate_config_path(cfg, input_dir / draft.name, field='task.local_draft',
                                               kind='file', storage=True)
            request_path = validate_config_path(cfg, self.out / 'run_request.json', field='task.run_request',
                                                 kind='file', storage=True)
            # Preflight every local write target before starting task I/O. An
            # existing input junction must never become a copy destination.
            self.emit("started")
            self.out.mkdir(parents=True, exist_ok=True)
            input_dir.mkdir(parents=True, exist_ok=True)
            if analysis_intent:
                # Analysis is a material task, not a request to check invented
                # draft figures. The empty intent is a controlled host resource.
                draft.write_text('# 明镜财报分析\n\n从锁定财报材料计算登记指标与信号。\n', encoding='utf-8')
                self.request['draft'] = str(draft)
                self.emit('analysis_intent', source='host_generated', sha256=sha256_file(draft))
            if draft.resolve() != local_draft.resolve():
                shutil.copy2(draft, local_draft)
            # Preserve accepted resource paths per task instead of reconstructing
            # old tasks from whichever paths happen to be configured today.
            from .agent import write_json
            accepted_request = {key: self.request[key] for key in ('company', 'period', 'report_date', 'task_type', 'mode', 'facts', 'knowledge_root', 'parent_run_id', 'source_session_id', 'scope_declaration', 'scope_clarifications', 'corpus_additions', 'reparse_packets', 'reparse_packet_ids', 'reparse_parent_run', '_controlled_reparse_root', 'reparse_inheritance', 'material_profile', 'validation_policy', 'smart_options', 'smart_draft_metadata', 'parent_batch_id')
                                if key in self.request}
            accepted_request.update(draft=str(local_draft), corpus=str(corpus))
            write_json(request_path, accepted_request)
            requested_mode = str(self.request.get("mode") or "kernel")
            if requested_mode == 'harness':
                from .agent import FinancialAgent
                agent = FinancialAgent({**self.request, 'draft': str(local_draft), 'corpus': str(corpus)},
                                       cfg, self._task_secret, self.out, run_id=self.id,
                                       emit=self.emit, cancel_event=self.cancelled)
                self.agent = agent
                result = agent.run(resume=getattr(self, 'resume_requested', False))
                self.status = 'cancelled' if self.cancelled.is_set() else 'partial' if result.get('partial') else 'completed'
                self.emit('completed', result=str(self.out / 'results.json'), partial=bool(result.get('partial')),
                          native_agent=result.get('model', {}).get('status') == 'success')
                return
            cli_mode = {"openai": "agent", "harness": "agent"}.get(requested_mode, requested_mode)
            args = [sys.executable, "-m", "mingjing.cli", "check", "--draft", str(local_draft), "--corpus", str(corpus), "--out", str(self.out), "--mode", cli_mode, "--report-date", str(self.request.get("report_date") or "")]
            args += ["--run-id", self.id]
            args += ['--task-type', str(self.request.get('task_type') or 'check')]
            args += ['--scope-declaration', str(self.request.get('scope_declaration', ''))]
            if self.request.get('scope_clarifications'):
                args += ['--scope-clarifications-json', json.dumps(self.request['scope_clarifications'], ensure_ascii=False)]
            for addition in self.request['corpus_additions']:
                args += ['--corpus-addition', addition]
            for packet in self.request.get('reparse_packets', []):
                args += ['--reparse-packet', packet['path']]
            if self.request.get('reparse_packets'):
                args += ['--reparse-parent-run', self.request['reparse_parent_run']]
            if self.request.get('reparse_inheritance'):
                args += ['--inherit-supplements-from', self.request['reparse_inheritance']['source_run']]
            args += ['--progress-json', '--run-timeout-s', str(float(cfg.get('run', {}).get('timeout_s') or 0))]
            for key in ("company", "period", "facts"):
                if self.request.get(key):
                    args += [f"--{key}", str(self.request[key])]
            if self.request.get('knowledge_root'):
                args += ['--knowledge-root', self.request['knowledge_root']]
            self.emit("phase", name="checking", command_hash=digest(args))
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            # The Web configuration is authoritative.  Clear inherited LLM
            # variables first so a stale shell/launcher token cannot enable
            # the model when the current profile has it disabled.
            llm_env_keys = _LLM_ENV_KEYS
            for key in llm_env_keys:
                env.pop(key, None)
            llm = cfg.get("llm", {})
            if llm.get("enabled") and llm.get("base_url") and llm.get("model"):
                env.update({
                    "MINGJING_LLM_BASE_URL": str(llm["base_url"]),
                    "MINGJING_LLM_MODEL": str(llm["model"]),
                    "MINGJING_LLM_CHAT_PATH": str(llm.get("chat_path") or "/chat/completions"),
                    "MINGJING_LLM_TIMEOUT_S": str(llm.get("timeout_s") or 60),
                    "MINGJING_LLM_MAX_TOKENS": str(llm.get("max_tokens") or 2000),
                    "MINGJING_LLM_TEMPERATURE": str(llm.get("temperature") if llm.get("temperature") is not None else 0),
                    "MINGJING_LLM_THINKING": str((llm.get("thinking") or {}).get("type", "auto") if isinstance(llm.get("thinking"), dict) else llm.get("thinking") or "auto"),
                })
                secret = self.config.secret(str(llm.get("api_key_ref", "llm_api_key")))
                if secret:
                    env["MINGJING_LLM_API_KEY"] = secret
                    self._secret_values.append(secret)
            # A one-file PyInstaller build has no separate Python interpreter
            # or importable ``mingjing.cli`` module on disk.  Invoke the CLI
            # entrypoint directly in that case; source installs use a child
            # process so cancellation can terminate a long-running parser.
            if getattr(sys, "frozen", False):
                from argparse import Namespace
                from .cli import command_check
                # Frozen mode calls the CLI in-process.  Mirror the child
                # process environment used in source mode so the configured
                # LLM is available to llm.enabled()/decompose(). Restore
                # every variable after the run to avoid leaking a secret to
                # subsequent tasks in this long-lived web process.
                # Environment variables are process-wide. Serialise frozen
                # runs so neither task can overwrite another task's model or
                # token while its semantic adapter is executing.
                self.emit("phase", name="waiting_for_runtime")
                while not _FROZEN_RUN_LOCK.acquire(timeout=0.1):
                    if self.cancelled.is_set():
                        raise RuntimeError("run cancelled")
                previous_env = {key: os.environ.get(key) for key in llm_env_keys}
                try:
                    if self.cancelled.is_set():
                        raise RuntimeError("run cancelled")
                    for key in llm_env_keys:
                        os.environ.pop(key, None)
                    os.environ.update({key: value for key, value in env.items() if key in llm_env_keys})
                    ns = Namespace(draft=str(local_draft), corpus=str(corpus), out=str(self.out), mode=cli_mode, report_date=str(self.request.get("report_date") or ""), period=str(self.request.get("period") or ""), company=str(self.request.get("company") or ""), facts=self.request.get("facts"), run_id=self.id, cancel_event=self.cancelled, run_timeout_s=float(cfg.get('run', {}).get('timeout_s') or 0), progress_callback=lambda name, **fields: self.emit("phase", name=name, **fields))
                    ns.task_type = self.request.get('task_type', 'check')
                    ns.scope_declaration = self.request.get('scope_declaration', '')
                    ns.scope_clarifications = self.request.get('scope_clarifications', [])
                    ns.knowledge_root = self.request.get('knowledge_root', '')
                    ns.corpus_additions = self.request['corpus_additions']
                    ns.reparse_packet_paths = [row['path'] for row in self.request.get('reparse_packets', [])]
                    ns.reparse_parent_run = self.request.get('reparse_parent_run', '')
                    ns.reparse_inheritance = self.request.get('reparse_inheritance')
                    code = command_check(ns)
                    if self.cancelled.is_set():
                        raise RuntimeError("run cancelled")
                    if code:
                        raise RuntimeError(f"check exited with code {code}")
                finally:
                    for key, value in previous_env.items():
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value
                    _FROZEN_RUN_LOCK.release()
                self._complete_cli()
                return
            proc = subprocess.Popen(args, cwd=str(PROJECT_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            def read_logs() -> None:
                if proc.stdout:
                    for line in proc.stdout:
                        raw = line.rstrip()
                        try:
                            progress = json.loads(raw)
                        except ValueError:
                            progress = None
                        if (isinstance(progress, dict) and progress.get('schema_version') == 'mingjing-progress.v1'
                                and isinstance(progress.get('name'), str)):
                            fields = {key: value for key, value in progress.items() if key in {
                                'percent', 'document', 'page', 'pages', 'company', 'index_rows', 'fact_rows',
                                'selected_documents', 'scanned_directories', 'selected_facts',
                                'document_index', 'document_count'} and isinstance(value, (str, int, float))}
                            self.emit('phase', name=progress['name'], **fields)
                        else:
                            self.emit("log", text=raw)
            reader = threading.Thread(target=read_logs, name=f"mingjing-log-{self.id}", daemon=True)
            reader.start()
            deadline = time.monotonic() + max(1, float(cfg.get("run", {}).get("timeout_s") or 600))
            while proc.poll() is None:
                if self.cancelled.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
                    raise RuntimeError("run cancelled")
                if time.monotonic() >= deadline:
                    proc.kill()
                    proc.wait(timeout=3)
                    raise RuntimeError("run exceeded the configured timeout")
                self.cancelled.wait(0.1)
            code = proc.wait()
            reader.join(timeout=1)
            if code:
                raise RuntimeError(f"check exited with code {code}")
            self._complete_cli()
        except Exception as exc:
            self.error = _redact(str(exc), self._secret_values)
            self.status = "cancelled" if self.cancelled.is_set() else "failed"
            if (getattr(self, 'resume_requested', False)
                    and not getattr(getattr(self, 'agent', None), 'resume_committed', False)):
                # Keep failed preflight visible in memory/registry without
                # changing the old run's audit, request, events or inputs.
                self.events.append({'time': time.time(), 'run_id': self.id, 'event': 'failed',
                                    'error': self.error, 'resume_preflight': True})
            else:
                self.emit("failed", error=self.error)
        finally:
            self.finished_at = time.time()
            callback = getattr(self, 'registry_callback', None)
            if callback:
                callback()

    def summary(self) -> dict[str, Any]:
        result = {"id": self.id, "status": self.status, "error": self.error, "out": str(self.out), "started_at": self.started_at, "finished_at": self.finished_at, "events": len(self.events)}
        result['frozen'] = (self.out/'run_manifest.json').is_file()
        result['can_resume'] = False
        result['request'] = {key: value for key, value in self.request.items() if key in
                             {'draft', 'corpus', 'company', 'period', 'report_date', 'task_type', 'mode', 'facts', 'knowledge_root', 'parent_run_id', 'scope_declaration'}
                             and isinstance(value, str)}
        if isinstance(self.request.get('corpus_additions'), list):
            result['request']['corpus_additions'] = list(self.request['corpus_additions'])
        if self.request.get('scope_clarifications'):
            result['request']['scope_clarifications'] = self.request['scope_clarifications']
        result['can_rerun'] = self.status not in {'running', 'queued'} and bool(
            result['request'].get('corpus') and
            (result['request'].get('draft') or result['request'].get('task_type') == 'analysis'))
        result['resume_reason'] = 'frozen_results_read_only' if result['frozen'] else 'native_state_unavailable'
        if not result['frozen'] and self.status not in {'running', 'queued'}:
            try:
                state = json.loads((self.out/'agent_state.json').read_text(encoding='utf-8'))
                result['can_resume'] = state.get('stage') != 'frozen' and (self.out/'agent_lock.json').is_file()
                if result['can_resume']:
                    result['resume_reason'] = 'lock_validation_required'
            except (OSError, ValueError):
                pass
        path = self.out / "results.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                from .execution import project_execution
                execution = project_execution(data)
                result.update({"run_id": data.get("run_id", self.id), "summary": data.get("summary", {}),
                               'execution': execution, 'partial': execution['partial']})
                if result['status'] == 'completed' and execution['partial']:
                    result['status'] = 'partial'
                if result['frozen'] and execution['partial']:
                    result['next_action'] = 'new_task_with_updated_budget_or_materials'
            except Exception:
                pass
        return result

    def _complete_cli(self) -> None:
        from .execution import project_execution
        result_file = self.out / 'results.json'
        partial = project_execution(json.loads(result_file.read_text(encoding='utf-8')))['partial'] if result_file.is_file() else False
        self.status = 'partial' if partial else 'completed'
        self.emit('completed', result=str(result_file), partial=partial, native_agent=False)


class AppState:
    def __init__(self) -> None:
        self.config = LocalConfig()
        data = self.config.get()
        storage = effective_storage_paths(data, include_service=True)
        workspace = storage['workspace.output_root']
        self.runs_root = workspace
        self.tasks: dict[str, RunTask] = {}
        self.lock = threading.RLock()
        self.diagnostics: dict[str, dict[str, Any]] = {}
        self.registry_path = _default_root() / "run_registry.json"
        validate_config_path(data, self.registry_path, field='service.run_registry', storage=True, kind='file')
        self.history_roots: list[str] = []
        self.run_registry: dict[str, dict[str, Any]] = {}
        if self.registry_path.is_file():
            try:
                stored = json.loads(self.registry_path.read_text(encoding="utf-8"))
                if stored.get("format_version") != 1 or not isinstance(stored.get("roots"), list) or not isinstance(stored.get("runs"), dict):
                    raise ValueError()
                if not all(isinstance(root, str) for root in stored["roots"]):
                    raise ValueError()
                if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id)
                           and isinstance(entry, dict) and isinstance(entry.get("out"), str)
                           for run_id, entry in stored["runs"].items()):
                    raise ValueError()
                self.history_roots, self.run_registry = stored["roots"], stored["runs"]
            except (ValueError, TypeError, AttributeError):
                raise ConfigError("运行历史登记文件无效，原文件已保留；请检查用户数据目录") from None
        workspace.mkdir(parents=True, exist_ok=True)
        self._remember_root(workspace, config_snapshot=data)
        from .sessions import FinancialSessions
        self.sessions = FinancialSessions(storage['service.sessions_root'], self.get_task, self.config)
        # 直传模式：上传即核查/分析，知识库去重缓存（mingjing/smart）
        from .smart.web import SmartService
        upload_mb = int((data.get('security') or {}).get('max_upload_mb') or 200)
        self.smart = SmartService(_default_root() / 'kb', workspace / 'smart', config=self.config.get,
                                  secret=self.config.secret, store=self.config, max_upload_mb=upload_mb, host=self)
        self.sessions.launch_run = self.launch

    def _save_registry(self, roots: list[str], records: dict[str, dict[str, Any]], *, config_snapshot=None) -> None:
        from .config import _atomic_write_json
        cfg = self.config.get() if config_snapshot is None else config_snapshot
        validate_config_path(cfg, self.registry_path, field='service.run_registry', storage=True, kind='file')
        _atomic_write_json(self.registry_path, {"format_version": 1, "roots": roots, "runs": records})
        self.history_roots, self.run_registry = list(roots), dict(records)

    def diagnostic_lifecycle(self, phase: str, agent) -> None:
        with self.lock:
            if phase == 'start':
                if self.sessions.closing:
                    raise ValueError('服务正在退出，不能启动诊断')
                self.diagnostics[agent.run_id] = {'agent': agent, 'worker': threading.current_thread()}
            else:
                self.diagnostics.pop(agent.run_id, None)

    def _remember_root(self, root: Path, *, config_snapshot=None) -> Path:
        cfg = self.config.get() if config_snapshot is None else config_snapshot
        root = validate_config_path(cfg, root, field='workspace.output_root', kind='dir', storage=True)
        with self.lock:
            if str(root) not in self.history_roots:
                self._save_registry([*self.history_roots, str(root)], self.run_registry, config_snapshot=cfg)
        return root

    def _register_task(self, task: RunTask) -> None:
        cfg = task.config_snapshot
        validate_config_path(cfg, task.out, field='task.output', kind='dir', storage=True)
        out = validate_path(task.out, allowed_roots=[str(task.root)], follow_symlinks=False)
        with self.lock:
            old = self.run_registry.get(task.id)
            if old and old.get("out") != str(out):
                raise ValueError("run_id already belongs to another output directory")
            entry = {"out": str(out), "created_at": task.started_at, "status": task.status,
                     "finished_at": task.finished_at}
            self._save_registry(self.history_roots, {**self.run_registry, task.id: entry}, config_snapshot=cfg)

    def _registered_out(self, run_id: str) -> Path | None:
        entry = self.run_registry.get(run_id)
        candidates = [Path(entry["out"])] if isinstance(entry, dict) and isinstance(entry.get("out"), str) else []
        candidates += [Path(root) / run_id for root in self.history_roots]
        for candidate in candidates:
            try:
                validate_config_path(self.config.get(), candidate, field='history.output', kind='dir', storage=True)
                out = validate_path(candidate, allowed_roots=self.history_roots, follow_symlinks=False)
                if out.name != run_id or str(out.parent) not in self.history_roots:
                    continue
                if out.is_dir() or entry and entry.get("out") == str(out):
                    return out
            except (ConfigError, OSError, ValueError):
                continue
        return None

    def reparse_context(self, run_id: str) -> dict[str, Any]:
        """Read the finite frozen parent material set, independent of new code."""
        task = self.get_task(run_id)
        if task is None or task.thread.is_alive() or task.status in {'running', 'queued'}:
            raise ValueError('需要已冻结的原任务才能重解析')
        security = self.config.get().get('security', {})
        roots = None if security.get('allow_external_paths', True) else list(security.get('allowed_roots') or [])
        if roots == []:
            raise ValueError('security.allowed_roots is required')
        from .reparse_inputs import load_parent_context
        context = load_parent_context(task.out, allowed_roots=roots)
        if context['data'].get('run_id') != run_id:
            raise ValueError('原任务标识与冻结版本不一致')
        output_root = validate_config_path(task.config_snapshot, task.root / 'reparse' / run_id,
                                          field='task.reparse_output', kind='dir', storage=True)
        output_root = validate_config_path(self.config.get(), output_root,
                                          field='task.reparse_output', kind='dir', storage=True)
        return dict(context, task=task, output_root=output_root)

    def reparse(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - {'report_id', 'page', 'binding_id'}:
            raise ValueError('重解析只接受登记的report_id、page和可选binding_id；禁止路径、数值或公式')
        if not isinstance(payload.get('report_id'), str) or type(payload.get('page')) is not int or payload['page'] < 1:
            raise ValueError('需要登记报告标识与合法整数页码')
        context = self.reparse_context(run_id)
        rows = [row for row in context['source_resources'] if row['id'] == payload['report_id'] and row['id'].startswith('report-')]
        if len(rows) != 1 or Path(rows[0]['path']).suffix.lower() != '.pdf':
            raise ValueError('report_id必须属于原任务锁定的PDF财报')
        claim = None
        if 'binding_id' in payload:
            if not isinstance(payload['binding_id'], str) or not payload['binding_id']:
                raise ValueError('binding_id必须是原任务陈述标识')
            candidates = [row for row in context['data'].get('claims', []) if row.get('claim_id') == payload['binding_id']]
            if len(candidates) != 1 or candidates[0].get('company') != context['company']:
                raise ValueError('binding_id不属于此原任务公司与陈述')
            from .replay import _claim_from_dict
            claim = _claim_from_dict(candidates[0])
        from .session_evidence import _page_blocks
        blocks, _ = _page_blocks(Path(rows[0]['path']), payload['page'])
        if not blocks:
            raise ValueError('该页无可重解析文本层，请补充可读财报材料')
        block = dict(blocks[0], id=rows[0]['id'] + ':' + blocks[0]['id'], report_id=rows[0]['id'], page=payload['page'])
        from .reparse import create_reparse_packet
        result = create_reparse_packet(output_root=context['output_root'], block=block, claim=claim,
            source_resources=context['source_resources'], baseline_facts=context['baseline_facts'],
            company=context['company'], as_of_date=context['as_of_date'], source_generation=context['source_generation'],
            deadline_monotonic=time.monotonic() + 60)
        return dict(result, parent_run_id=run_id, frozen_parent_unchanged=True)

    def reparse_packets(self, run_id: str) -> list[dict[str, Any]]:
        task = self.get_task(run_id)
        if task is None:
            raise ValueError('原任务不存在')
        if not (task.root / 'reparse' / run_id).exists():
            return []
        context = self.reparse_context(run_id)
        from .reparse import list_reparse_packets
        return list_reparse_packets(context['output_root'], expected_source_generation=context['source_generation'])

    def _reparse_packet_facts(self, run_id: str, packet_id: str):
        """Human review uses the same trusted loader as task application."""
        context = self.reparse_context(run_id)
        from .reparse import load_reparse_packet, resolve_reparse_packet
        path = resolve_reparse_packet(context['output_root'], packet_id)
        file_sha = sha256_file(path)
        facts = load_reparse_packet(path, source_resources=context['source_resources'],
            baseline_facts=context['baseline_facts'], company=context['company'], as_of_date=context['as_of_date'],
            expected_packet_id=packet_id, expected_source_generation=context['source_generation'])
        if sha256_file(path) != file_sha:
            raise ValueError('补证包在预览期间发生变化，请重新生成')
        return context, facts, file_sha

    def reparse_packet_preview(self, run_id: str, packet_id: str) -> dict[str, Any]:
        context, facts, file_sha = self._reparse_packet_facts(run_id, packet_id)
        from .presentation import _metric_title, display_standard_value, standard_unit
        public = []
        for fact in facts:
            row = {key: value for key, value in fact.to_dict().items() if key != 'source_path'}
            row.update(title=_metric_title(fact.account), source_name=Path(fact.source_path).name,
                       value_unit=standard_unit(fact.account, {'unit': fact.display_unit}))
            row['display_value'] = display_standard_value(fact.value, row['value_unit'])
            public.append(row)
        return {'parent_run_id': run_id, 'packet_id': packet_id, 'packet_file_sha256': file_sha,
                'status': 'ready', 'readonly': True, 'applied_to_current_task': False,
                'source_generation': context['source_generation'], 'facts': public}

    def reparse_packet_evidence(self, run_id: str, packet_id: str, evidence_id: str):
        context, facts, _ = self._reparse_packet_facts(run_id, packet_id)
        fact = next((fact for fact in facts if evidence_id in fact.evidence), None)
        if fact is None:
            raise ValueError('证据ID不属于该补证包的新增事实')
        info = _evidence_info(fact.to_dict(), evidence_id)
        source = next(row for row in context['source_resources'] if Path(row['path']).resolve() == Path(fact.source_path).resolve())
        info.update(parent_run_id=run_id, packet_id=packet_id, document_sha256=source['sha256'],
                    reference_status='reparse_fact_preview', readonly=True)
        return info

    def reparse_evidence(self, run_id: str, info: dict[str, Any]) -> dict[str, Any]:
        """Offer only a registered PDF ID; full integrity is checked on POST."""
        unavailable = {'available': False, 'reason': '此证据未登记为原任务PDF财报'}
        task = self.get_task(run_id)
        if task is None or type(info.get('page')) is not int:
            return unavailable
        manifest_path = task.out / 'run_manifest.json'
        if not manifest_path.is_file():
            return unavailable
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('run_id') != run_id or manifest.get('schema_version', 0) < 2:
            return unavailable
        source_path = info.get('source_path')
        sha = info.get('doc_sha256') or info.get('document_sha256')
        candidates = [row for row in manifest.get('inputs', []) if Path(row.get('path', '')).suffix.lower() == '.pdf' and
            ((source_path and Path(row['path']).resolve() == Path(source_path).resolve()) or
             (sha and row.get('sha256') == sha))]
        if len(candidates) != 1:
            return unavailable
        row = candidates[0]
        if info.get('document_sha12') and not row['sha256'].startswith(info['document_sha12']):
            return unavailable
        report_id = 'report-' + row['sha256'][:20]
        lock_path = task.out / 'agent_lock.json'
        if lock_path.is_file():
            locked = json.loads(lock_path.read_text(encoding='utf-8'))
            matching = [resource for resource in locked.get('resources', []) if
                resource.get('sha256') == row['sha256'] and str(resource.get('id', '')).startswith('report-')]
            if len(matching) != 1:
                return unavailable
            report_id = matching[0]['id']
        elif info.get('report_id') == 'draft':
            return unavailable
        return {'available': True, 'report_id': report_id, 'page': info['page']}

    def launch(self, request: dict[str, Any], *, reparse_packets: list[dict[str, Any]] | None = None,
               reparse_inheritance: dict[str, Any] | None = None,
               config_snapshot: dict[str, Any] | None = None) -> RunTask:
        if self.sessions.closing:
            raise ValueError('服务正在退出，不能启动新任务')
        run_id = str(request.get("run_id") or f"web-{uuid.uuid4().hex[:12]}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
            raise ValueError("invalid run_id")
        if any(key in request for key in ("candidate_value", "value", "facts_data", "results", "config_snapshot")):
            raise ValueError("run requests cannot contain candidate facts or verdicts")
        if request.get('facts') is not None and not isinstance(request['facts'], str):
            raise ValueError('facts只能引用材料路径，不能接收事实数组')
        if request.get('material_profile', '') not in {'', 'smart'}:
            raise ValueError('unsupported material parser profile')
        if request.get('material_profile') == 'smart':
            from .agent import task_request_semantics
            task_request_semantics(request)
            if request.get('mode') != 'harness':
                raise ValueError('上传解析任务必须使用原生宿主运行')
            if not isinstance(request.get('smart_options', {}), dict) or set(request.get('smart_options', {})) - {'use_llm', 'text_evidence'}:
                raise ValueError('invalid frozen smart policy options')
        if any(key in request for key in ('reparse_packets', 'reparse_packet_ids', 'reparse_packet_paths', 'reparse_parent_run', '_controlled_reparse_root', 'reparse_inheritance', 'inherit_supplements_from')):
            raise ValueError('重解析包只能通过原任务受控补证版本应用')
        if request.get('task_type', 'check') not in {'check', 'analysis'}:
            raise ValueError('unsupported financial task type')
        from .scope import validate_scope_declaration
        validate_scope_declaration(request.get('scope_declaration', ''))
        from .clarifications import request_scope_clarifications
        if 'scope_clarifications' in request:
            request = {**request, 'scope_clarifications': request_scope_clarifications(request)}
        cfg = json.loads(json.dumps(self.config.get() if config_snapshot is None else config_snapshot, ensure_ascii=False))
        from .materials import normalize_corpus_additions
        security = cfg.get('security', {})
        roots = None if security.get('allow_external_paths', True) else list(security.get('allowed_roots') or [])
        if roots == []:
            raise ValueError('security.allowed_roots is required when external paths are disabled')
        request = _effective_run_request(request, cfg)
        # Freeze and validate all effective paths before history writes, mkdir,
        # task registration or worker construction. Task choices override only
        # corresponding source defaults, never security roots or output policy.
        storage = effective_storage_paths(cfg, include_service=True)
        validate_config_path(cfg, self.registry_path, field='service.run_registry', kind='file', storage=True)
        validate_config_path(cfg, self.sessions.root, field='service.sessions_root', kind='dir', storage=True)
        for field, kind in (('draft', 'file'), ('corpus', 'any'), ('facts', 'file'), ('knowledge_root', 'dir')):
            if request.get(field):
                request[field] = str(validate_config_path(cfg, request[field], field='task.'+field,
                                                         must_exist=True, kind=kind))
        if cfg.get('workspace', {}).get('corpus_index'):
            validate_config_path(cfg, cfg['workspace']['corpus_index'], field='workspace.corpus_index',
                                 must_exist=True, kind='file')
        if request.get('mode') == 'harness' and cfg.get('harness', {}).get('runtime_mode') == 'external':
            for field, kind in (('executable', 'file'), ('home', 'dir'), ('patch_file', 'file'), ('working_dir', 'dir')):
                if cfg['harness'].get(field):
                    validate_config_path(cfg, cfg['harness'][field], field='harness.'+field,
                                         # A missing optional executable is a locked runtime
                                         # failure, handled by the documented kernel fallback.
                                         # Its root/link policy still applies before admission.
                                         must_exist=field == 'patch_file', kind=kind, storage=kind == 'dir')
        if reparse_inheritance is not None:
            if reparse_packets is not None:
                raise ValueError('cannot combine packet application with inherited supplements')
            from .task_versions import plan_supplement_inheritance
            # Reconstruct a trusted plan from the frozen run, never from model
            # or browser-supplied fact values or execution coordinates.
            request['reparse_inheritance'] = plan_supplement_inheritance(
                reparse_inheritance['source_run'], request, allowed_roots=roots)
            if request['reparse_inheritance'] != reparse_inheritance:
                raise ValueError('supplement inheritance lock changed before task admission')
        if reparse_packets is not None:
            request['reparse_packets'] = reparse_packets
            request['reparse_packet_ids'] = [row['packet_id'] for row in reparse_packets]
            parent = self.get_task(request.get('parent_run_id', ''))
            if parent is None:
                raise ValueError('重解析补证缺少原任务')
            request['reparse_parent_run'] = str(parent.out)
            request['_controlled_reparse_root'] = str(parent.root / 'reparse' / parent.id)
        request['corpus_additions'] = normalize_corpus_additions(
            request.get('corpus_additions'), str(request.get('corpus') or ''), allowed_roots=roots,
            follow_symlinks=bool(security.get('follow_symlinks', False)))
        root = storage['workspace.output_root']
        out = validate_config_path(cfg, root/run_id, field='task.output', kind='dir', storage=True)
        if request.get('out') and Path(request['out']) != out:
            raise ConfigError('task.out is assigned by the service; configure workspace.output_root instead')
        task = RunTask(run_id, dict(request), root, self.config, config_snapshot=cfg)
        with self.lock:
            if self.sessions.closing:
                raise ValueError('服务正在退出，不能启动新任务')
            if run_id in self.tasks or run_id in self.run_registry or task.out.exists():
                raise ValueError("run_id already exists")
            root = self._remember_root(root, config_snapshot=cfg)
            root.mkdir(parents=True, exist_ok=True)
            self.runs_root = root
            self._register_task(task)
            task.registry_callback = lambda: self._register_task(task)
            self.tasks[run_id] = task
            task.start()
        return task

    def rerun(self, run_id: str, overrides: dict[str, Any]) -> RunTask:
        """Create a new task under current settings, preserving the old run.

        This is a complete new run, not in-place resume or a claim that pending
        work alone can be continued from a frozen result.
        """
        source = self.get_task(run_id)
        if source is None:
            raise ValueError('原任务不存在')
        if source.thread.is_alive() or source.status in {'running', 'queued'}:
            raise ValueError('原任务仍在运行，请先完成或取消')
        fields = {'draft', 'corpus', 'company', 'period', 'report_date', 'task_type', 'mode', 'facts', 'knowledge_root', 'scope_declaration', 'scope_clarifications', 'corpus_additions', 'reparse_packet_ids'}
        if not isinstance(overrides, dict) or set(overrides) - fields:
            raise ValueError('重新核查只接受材料路径和核查参数；预算及供应商请在设置中调整')
        if any(not isinstance(value, str) for key, value in overrides.items() if key not in {'corpus_additions', 'reparse_packet_ids', 'scope_clarifications'}):
            raise ValueError('重新核查参数必须是字符串')
        accepted = source.request
        request_path = source.out / 'run_request.json'
        if request_path.is_file():
            validate_path(request_path, allowed_roots=[str(source.out)], kind='file', follow_symlinks=False)
            accepted = json.loads(request_path.read_text(encoding='utf-8'))
            if not isinstance(accepted, dict):
                raise ValueError('原任务的材料参数损坏，请选择材料新建任务')
        request = {key: value for key, value in accepted.items() if key in fields and isinstance(value, str)}
        for key in ('material_profile', 'validation_policy', 'smart_options', 'smart_draft_metadata', 'parent_batch_id'):
            if key in accepted:
                request[key] = accepted[key]
        if 'corpus_additions' in accepted:
            request['corpus_additions'] = accepted['corpus_additions']
        if 'scope_clarifications' in accepted:
            request['scope_clarifications'] = accepted['scope_clarifications']
        packet_ids = overrides.get('reparse_packet_ids')
        if packet_ids is not None and (not isinstance(packet_ids, list) or not 1 <= len(packet_ids) <= 12 or
                any(not isinstance(identifier, str) for identifier in packet_ids) or len(set(packet_ids)) != len(packet_ids)):
            raise ValueError('reparse_packet_ids必须是不重复的已登记包ID列表（最多12项）')
        if packet_ids is None:
            from .task_versions import assert_plain_version_allowed
            previous_results = {}
            results_path = source.out / 'results.json'
            if results_path.is_file():
                validate_path(results_path, allowed_roots=[str(source.out)], kind='file', follow_symlinks=False)
                previous_results = json.loads(results_path.read_text(encoding='utf-8'))
            assert_plain_version_allowed(accepted, previous_results)
        if packet_ids is not None and any(overrides[key] != request.get(key, '' if key != 'corpus_additions' else [])
                for key in overrides if key not in {'mode', 'reparse_packet_ids'}):
            raise ValueError('补证包仅适用于原任务完整材料、公司、期间、口径与回溯日期')
        request.update(overrides)
        request.pop('reparse_packet_ids', None)
        if overrides.get('scope_declaration') and 'scope_clarifications' not in overrides:
            request['scope_clarifications'] = []
        if overrides.get('scope_clarifications') and 'scope_declaration' not in overrides:
            request['scope_declaration'] = ''
        if 'draft' in overrides and 'scope_declaration' not in overrides:
            # A prior clarification applies to its locked draft, not to every
            # later document chosen from the same conversation.
            request['scope_declaration'] = ''
        if 'draft' in overrides and 'scope_clarifications' not in overrides:
            request['scope_clarifications'] = []
        request.setdefault('task_type', 'check')
        request.setdefault('mode', 'kernel')
        if request['task_type'] not in {'check', 'analysis'} or request['mode'] not in {'kernel', 'agent', 'openai', 'harness'}:
            raise ValueError('不支持的金融任务类型或运行模式')
        cfg = self.config.get()
        security = cfg.get('security', {})
        roots = None if security.get('allow_external_paths', True) else security.get('allowed_roots', [])
        if roots == []:
            raise ValueError('security.allowed_roots is required when external paths are disabled')
        follow = bool(security.get('follow_symlinks', False))
        required = ['corpus'] if request['task_type'] == 'analysis' else ['draft', 'corpus']
        for key in required:
            if not request.get(key):
                raise ValueError('原任务缺少材料路径，请选择材料新建任务')
        for key in ('draft', 'corpus', 'facts'):
            if request.get(key):
                request[key] = str(validate_path(request[key], allowed_roots=roots, must_exist=True,
                                                 kind='any' if key == 'corpus' else 'file', follow_symlinks=follow))
        request['parent_run_id'] = source.id
        packet_entries = None
        if packet_ids is not None:
            from .reparse import load_reparse_packet, resolve_reparse_packet
            context = self.reparse_context(source.id)
            packet_entries = []
            for identifier in packet_ids:
                path = resolve_reparse_packet(context['output_root'], identifier)
                facts = load_reparse_packet(path, source_resources=context['source_resources'],
                    baseline_facts=context['baseline_facts'], company=context['company'], as_of_date=context['as_of_date'],
                    expected_packet_id=identifier, expected_source_generation=context['source_generation'])
                packet = json.loads(io_path(path).read_text(encoding='utf-8'))
                if packet.get('status') != 'ready' or not facts:
                    raise ValueError('仅无冲突且含新增事实的ready包可创建补证版本；冲突需专业核对')
                packet_entries.append({'packet_id': identifier, 'path': str(path), 'sha256': sha256_file(path),
                                       'source_generation': context['source_generation']})
        return self.launch(request, reparse_packets=packet_entries) if packet_entries is not None else self.launch(request)

    def get_task(self, run_id: str) -> RunTask | None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
            return None
        with self.lock:
            task = self.tasks.get(run_id)
        if task:
            validate_config_path(self.config.get(), task.out, field='history.output', kind='dir', storage=True)
            return task
        out = self._registered_out(run_id)
        if out is None:
            return None
        entry = self.run_registry.get(run_id, {})
        task = RunTask(run_id, {"out": str(out)}, out.parent, self.config)
        task.started_at = entry.get("created_at", task.started_at)
        task.finished_at = entry.get("finished_at")
        task.status = entry.get("status", "interrupted")
        if not isinstance(task.status, str) or task.status not in {"completed", "partial", "failed", "cancelled", "running", "queued", "interrupted"}:
            task.status = "interrupted"
        if task.status in {"running", "queued"}:
            task.status = "interrupted"
        path = out / "results.json"
        try:
            validate_path(path, allowed_roots=[str(out)], follow_symlinks=False)
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("run_id", run_id) != run_id:
                    return None
                if (out / 'run_manifest.json').is_file():
                    task.status = 'partial' if payload.get('partial') else 'completed'
                elif task.status not in {'cancelled', 'failed', 'interrupted'}:
                    task.status = 'partial' if payload.get('partial') else 'interrupted'
                task.finished_at = path.stat().st_mtime
                request_file = out / 'run_request.json'
                validate_path(request_file, allowed_roots=[str(out)], follow_symlinks=False)
                if request_file.is_file():
                    accepted = json.loads(request_file.read_text(encoding='utf-8'))
                elif (out / 'agent_state.json').is_file():
                    accepted = json.loads((out / 'agent_state.json').read_text(encoding='utf-8')).get('request', {})
                else:
                    accepted = {key: payload[key] for key in ('draft', 'corpus', 'company', 'report_date', 'task_type', 'mode', 'knowledge_root', 'scope_declaration') if key in payload}
                task.request.update({key: value for key, value in accepted.items() if key in
                                    {'draft', 'corpus', 'company', 'period', 'report_date', 'task_type', 'mode', 'facts', 'knowledge_root', 'parent_run_id', 'scope_declaration'}
                                     and isinstance(value, str)})
                if isinstance(accepted.get('corpus_additions'), list):
                    task.request['corpus_additions'] = accepted['corpus_additions']
                if isinstance(accepted.get('scope_clarifications'), list):
                    task.request['scope_clarifications'] = accepted['scope_clarifications']
                for key in ('reparse_packets', 'reparse_packet_ids', 'reparse_parent_run', '_controlled_reparse_root', 'reparse_inheritance', 'material_profile', 'validation_policy', 'smart_options', 'smart_draft_metadata', 'parent_batch_id'):
                    if key in accepted:
                        task.request[key] = accepted[key]
            events = out / "web_events.jsonl"
            validate_path(events, allowed_roots=[str(out)], follow_symlinks=False)
            if events.is_file():
                for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict) and event.get("run_id") == run_id:
                            task.events.append(event)
                    except (ValueError, TypeError):
                        pass
            self._register_task(task)
        except (ValueError, OSError, TypeError):
            return None
        with self.lock:
            self.tasks.setdefault(run_id, task)
            return self.tasks[run_id]

    def list_tasks(self) -> list[RunTask]:
        """Discover completed/interrupted runs across all persisted output roots."""
        cfg = self.config.get()
        self._remember_root(Path(cfg.get("workspace", {}).get("output_root") or self.runs_root))
        ids = set(self.run_registry) | set(self.tasks)
        for raw in list(self.history_roots):
            try:
                validate_config_path(cfg, raw, field='history.output_root', kind='dir', storage=True)
                root = validate_path(raw, must_exist=True, kind="dir", follow_symlinks=False)
                for out in root.iterdir():
                    if out.is_dir() and ((out / "results.json").is_file() or (out / "agent_state.json").is_file()):
                        ids.add(out.name)
            except (ConfigError, OSError):
                continue
        return [task for run_id in sorted(ids) if (task := self.get_task(run_id)) is not None]


class Handler(BaseHTTPRequestHandler):
    server_version = "MingJing/1.0"

    @property
    def state(self) -> AppState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _authorized(self, *, require_token: bool = True) -> bool:
        host_header = self.headers.get("Host", "")
        host_name = urlparse("http://" + host_header).hostname
        bound_host = str(self.server.server_address[0])
        if bound_host in {"127.0.0.1", "::1"} and host_name not in {"127.0.0.1", "localhost", "::1"}:
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc.lower() != host_header.lower():
            return False
        cfg = self.state.config.public().get("server", {})
        if not require_token or not cfg.get("bearer_token_required"):
            return True
        token = self.headers.get("Authorization", "")
        secret = self.state.config.secret(str(cfg.get("bearer_token_ref", "server_bearer")))
        return bool(secret) and secrets.compare_digest(token.removeprefix("Bearer ").strip(), secret)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        # Let the local browser load the login application. Host/Origin
        # validation still applies; every data API requires its bearer.
        if not self._authorized(require_token=path == '/api' or path.startswith('/api/')):
            _json(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}); return
        if self.state.smart.handle(self, 'GET', path, parsed.query):
            return
        try:
            if path == "/api/health":
                from . import __version__
                _json(self, 200, {"status": "ok", "version": __version__, "service": "mingjing", "instance_id": getattr(self.server, "instance_id", None), "python": sys.version.split()[0], "runs": len(self.state.tasks)}); return
            if path == "/api/config":
                value = self.state.config.public()
                # Presence is a cheap declared capability; extraction and
                # actual startup are performed by the explicit diagnostic.
                declared = _harness_runtime_declared(_BUNDLE_ROOT)
                _json(self, 200, {"config": value, "revision": value.get("revision", 0), "schema_version": value.get("version", 1), "capabilities": {"pdf": self._pdf_available(), "streamlit": self._module_available("streamlit"), "harness": declared, "harness_runtime_configured": declared, "harness_capability_status": 'declared_requires_health_check' if declared else 'unavailable', "llm": bool(value.get("providers"))}}); return
            if path == '/api/providers':
                from .providers import provider_presets
                _json(self, 200, {'providers': self.state.config.public().get('providers', {}), 'presets': provider_presets()}); return
            if path in {'/api/agent/sessions', '/api/sessions'}:
                _json(self, 200, {'sessions': self.state.sessions.list()}); return
            if path.startswith('/api/agent/sessions/') or path.startswith('/api/sessions/'):
                bits = path.split('/')
                sid = bits[4] if bits[2] == 'agent' else bits[3]
                state = self.state.sessions.get(sid)
                _json(self, 200, self.state.sessions.public(state)); return
            if path == "/api/runs":
                values = [task.summary() for task in self.state.list_tasks()]
                _json(self, 200, {"runs": sorted(values, key=lambda x: x.get("started_at", 0), reverse=True)}); return
            if path.startswith("/api/runs/"):
                bits = path.split("/")
                run_id = bits[3]
                task = self.state.get_task(run_id)
                if not task:
                    _json(self, 404, {"error": "run not found"}); return
                if len(bits) >= 5 and bits[4] == "events":
                    from .activity import project_events
                    _json(self, 200, {"run_id": run_id, "events": project_events(task.events),
                                      "event_format": "activity.v1", "raw_event_count": len(task.events)}); return
                if len(bits) == 5 and bits[4] == 'reparse-packets':
                    _json(self, 200, {'parent_run_id': run_id, 'packets': self.state.reparse_packets(run_id)}); return
                if len(bits) >= 6 and bits[4] == 'reparse-packets':
                    try:
                        packet_id = bits[5]
                        query = parse_qs(parsed.query, keep_blank_values=True)
                        if len(bits) == 6 and not query:
                            _json(self, 200, self.state.reparse_packet_preview(run_id, packet_id)); return
                        if len(bits) != 8 or bits[6] != 'evidence' or set(query) - {'format'} or any(len(values) != 1 for values in query.values()):
                            raise ValueError('补证预览只接受登记包与证据ID，不接受路径、页码或金额')
                        format_name = (query.get('format') or ['json'])[0]
                        if format_name not in {'json', 'png'}:
                            raise ValueError('补证证据格式仅支持json或png')
                        info = self.state.reparse_packet_evidence(run_id, packet_id, bits[7])
                        if format_name == 'json':
                            _json(self, 200, {key: value for key, value in info.items() if key not in {'source_path', 'fact'}}); return
                        source = Path(info['source_path'])
                        if sha256_file(source) != info['document_sha256']:
                            raise ValueError('补证证据源文件已修改')
                        raw = _evidence_png(info, self.state.config.get())
                        if sha256_file(source) != info['document_sha256']:
                            raise ValueError('补证证据在预览期间发生变化')
                        self.send_response(200)
                        self.send_header('Content-Type', 'image/png')
                        self.send_header('Content-Length', str(len(raw)))
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.end_headers(); self.wfile.write(raw); return
                    except (ConfigError, ValueError, OSError) as exc:
                        _json(self, 400, {'error': str(exc)}); return
                if len(bits) >= 5 and bits[4] == "results":
                    p = task.out / "results.json"
                    if not p.exists(): _json(self, 202, task.summary()); return
                    from .execution import project_result_view
                    _json(self, 200, project_result_view(json.loads(p.read_text(encoding="utf-8")))); return
                if len(bits) >= 5 and bits[4] == "export":
                    if task.status not in {"completed", "partial"}:
                        _json(self, 409, {"error": "运行尚未完成，暂不能导出"}); return
                    raw = _export_run(task, self.state.config, self.state.sessions)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", f'attachment; filename="MingJing-{run_id}.zip"')
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                _json(self, 200, task.summary()); return
            if path.startswith("/api/evidence/"):
                evidence_id = path.rsplit("/", 1)[-1]
                query = parse_qs(parsed.query)
                if 'session' in query:
                    from .session_evidence import load_source_evidence, SessionEvidenceError
                    try:
                        if set(query) - {'session', 'message', 'run', 'format'} or any(len(values) != 1 for values in query.values()):
                            raise SessionEvidenceError('会话证据仅接受已登记的会话、消息和证据ID')
                        sid = query['session'][0]
                        try:
                            session = self.state.sessions.get(sid)
                        except ValueError:
                            raise SessionEvidenceError('会话不存在', 404)
                        if query.get('run', [session['run_id']])[0] != session['run_id']:
                            raise SessionEvidenceError('证据请求与会话原运行不匹配', 403)
                        info = load_source_evidence(self.state.sessions.root, session,
                            (query.get('message') or [''])[0], evidence_id, self.state.config.get())
                        format_name = (query.get('format') or ['json'])[0]
                        if format_name == 'png':
                            if not info.get('image_available'):
                                raise SessionEvidenceError('此原文引用没有PDF页级图像')
                            raw = Path(info['_image_path']).read_bytes()
                            if hashlib.sha256(raw).hexdigest() != info['_image_sha256']:
                                raise SessionEvidenceError('会话证据图像在读取期间发生变化', 409)
                            self.send_response(200)
                            self.send_header('Content-Type', 'image/png')
                            self.send_header('Content-Length', str(len(raw)))
                            self.send_header('Cache-Control', 'no-store')
                            self.send_header('X-Content-Type-Options', 'nosniff')
                            self.end_headers()
                            self.wfile.write(raw)
                        elif format_name == 'json':
                            public = {key: value for key, value in info.items() if not key.startswith('_image_')}
                            public['reparse'] = self.state.reparse_evidence(session['run_id'], public)
                            _json(self, 200, public)
                        else:
                            raise SessionEvidenceError('会话证据格式仅支持json或png')
                    except SessionEvidenceError as exc:
                        _json(self, exc.status, {'error': str(exc)})
                    return
                run_id = (query.get("run") or [""])[0]
                task = self.state.get_task(run_id) if run_id else None
                if task:
                    data = json.loads((task.out / "results.json").read_text(encoding="utf-8"))
                    from .evidence import fact_evidence_references
                    resource = data.get('evidence_resources', {}).get(evidence_id)
                    for fact in data.get("facts", []):
                        if evidence_id in fact_evidence_references(fact):
                            def fact_info():
                                if resource and resource.get('source_path'):
                                    if Path(resource['source_path']).resolve() != Path(fact.get('source_path', '')).resolve():
                                        raise ValueError('Evidence resource conflicts with its frozen Fact source')
                                    info = _source_resource_info(resource, evidence_id)
                                    info['fact'] = fact
                                    return info
                                return _evidence_info(fact, evidence_id)
                            if (query.get("format") or [""])[0] == "png":
                                raw = _evidence_png(fact_info(), self.state.config.get())
                                self.send_response(200)
                                self.send_header("Content-Type", "image/png")
                                self.send_header("Content-Length", str(len(raw)))
                                self.send_header("Cache-Control", "no-store")
                                self.end_headers()
                                self.wfile.write(raw)
                            else:
                                try:
                                    info = fact_info()
                                except ValueError:
                                    info = {"evidence_id": evidence_id, "fact": fact}
                                info['reparse'] = self.state.reparse_evidence(run_id, info)
                                _json(self, 200, info)
                            return
                    if resource and resource.get('source_path'):
                        info = _source_resource_info(resource, evidence_id)
                        if (query.get('format') or [''])[0] == 'png':
                            raw = _evidence_png(info, self.state.config.get())
                            self.send_response(200)
                            self.send_header('Content-Type', 'image/png')
                            self.send_header('Content-Length', str(len(raw)))
                            self.send_header('Cache-Control', 'no-store')
                            self.end_headers(); self.wfile.write(raw)
                        else:
                            info['reparse'] = self.state.reparse_evidence(run_id, info)
                            _json(self, 200, info)
                        return
                _json(self, 404, {"error": "evidence not found"}); return
            if path == "/api/config/export":
                _json(self, 200, {"config": self.state.config.public(), "exported_at": time.time(), "secrets": "omitted"}); return
            if path == "/" or path == "/index.html":
                self.state.smart.static(self, "index.html"); return
            if path in {"/classic", "/harness"}:
                self._static("index.html"); return
            self._static(path.removeprefix("/"))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Cancelling a fetch/closing a tab is normal. The socket cannot
            # receive a second HTTP error response after it has gone away.
            return
        except Exception as exc:
            _json(self, 500, {"error": str(exc)})

    def _static(self, name: str) -> None:
        ui = STATIC_ROOT / 'harness'
        base = ui if ui.is_dir() and (ui / name).is_file() else STATIC_ROOT
        path = (base / name).resolve()
        if base.resolve() not in path.parents or not path.is_file():
            _json(self, 404, {"error": "not found"}); return
        raw = path.read_bytes(); self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream"); self.send_header("Content-Length", str(len(raw)))
        self.send_header('X-Content-Type-Options','nosniff')
        if base == ui:
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(raw)

    def do_POST(self) -> None:
        if not self._authorized(): _json(self, 401, {"error": "unauthorized"}); return
        path = urlparse(self.path).path.rstrip("/")
        if self.state.smart.handle(self, 'POST', path, urlparse(self.path).query):
            return
        try:
            payload = _body(self)
            if self.state.sessions.closing and path != '/api/shutdown':
                raise ValueError('服务正在退出，不能启动新操作')
            if path == '/api/paths/pick':
                if set(payload) - {'kind'} or payload.get('kind') not in {'file','dir'}:
                    raise ValueError('选择路径仅接受 file 或 dir 类型')
                from .native_picker import pick_path
                selected = pick_path(payload['kind'])
                if not selected:
                    _json(self,200,{'cancelled':True}); return
                security = self.state.config.get().get('security',{})
                roots = None if security.get('allow_external_paths',True) else list(security.get('allowed_roots') or [])
                if roots == []:
                    raise ValueError('security.allowed_roots is required')
                chosen = validate_path(selected,allowed_roots=roots,must_exist=True,kind=payload['kind'],follow_symlinks=False)
                _json(self,200,{'path':str(chosen),'kind':payload['kind']}); return
            if path.startswith('/api/providers/'):
                from .provider_api import discover, test_agent, test_connection
                bits = path.split('/')
                provider_id, action = bits[3], bits[4]
                if action == 'discover':
                    _json(self, 200, discover(self.state.config.get(), provider_id, self.state.config.secret)); return
                if action in {'test-agent', 'test'}:
                    diagnostic = test_agent if action == 'test-agent' else test_connection
                    cfg = self.state.config.get()
                    diagnostic_root = validate_config_path(cfg, _default_root() / 'diagnostics',
                                                           field='service.provider_diagnostics', kind='dir', storage=True)
                    result = diagnostic(cfg, provider_id, self.state.config.secret,
                                        diagnostic_root, payload.get('model'), lifecycle=self.state.diagnostic_lifecycle)
                    _json(self, 200, result); return
            if path in {'/api/agent/sessions', '/api/sessions'}:
                result = self.state.sessions.create(str(payload.get('run_id') or ''))
                if payload.get('question'):
                    result = self.state.sessions.submit(result['session_id'], str(payload['question']))
                _json(self, 202, result); return
            if path.startswith('/api/agent/sessions/') or path.startswith('/api/sessions/'):
                bits = path.split('/')
                sid = bits[4] if bits[2] == 'agent' else bits[3]
                action = bits[-1]
                if action == 'replay' and len(bits) >= 7 and bits[-3] == 'messages':
                    if payload:
                        raise ValueError('message replay uses frozen inputs and accepts no overrides')
                    _json(self, 200, self.state.sessions.replay_message(sid, bits[-2])); return
                if action == 'materials':
                    if set(payload) - {'draft', 'corpus', 'corpus_additions', 'facts', 'question', 'scope_declaration', 'scope_clarifications'}:
                        raise ValueError('补充材料仅接受资料路径与问题，不能提交候选数值或判定')
                    if not any(payload.get(key) for key in ('draft', 'corpus', 'corpus_additions', 'facts')) and not {'scope_declaration', 'scope_clarifications'}.intersection(payload):
                        raise ValueError('请至少提供一项新的材料路径或口径澄清')
                    from .scope import validate_scope_declaration
                    if 'scope_declaration' in payload:
                        validate_scope_declaration(payload['scope_declaration'])
                    from .clarifications import request_scope_clarifications
                    if 'scope_clarifications' in payload:
                        request_scope_clarifications(payload)
                    security = self.state.config.get().get('security', {})
                    roots = None if security.get('allow_external_paths', True) else list(security.get('allowed_roots') or [])
                    if roots == []:
                        raise ValueError('security.allowed_roots is required')
                    material = {}
                    for key in ('draft', 'corpus', 'facts'):
                        if payload.get(key):
                            source = validate_path(str(payload[key]), allowed_roots=roots, must_exist=True,
                                                   kind='file' if key in {'draft', 'facts'} else 'any', follow_symlinks=False)
                            if key == 'draft' and source.suffix.lower() not in {'.pdf', '.md', '.txt', '.csv'}:
                                raise ValueError('新草稿支持 PDF、Markdown 和文本')
                            if key == 'facts' and source.suffix.lower() != '.json':
                                raise ValueError('事实索引须为 JSON 文件')
                            material[key] = str(source)
                    if 'corpus_additions' in payload:
                        additions = payload['corpus_additions']
                        if not isinstance(additions, list) or any(not isinstance(item, str) or not item.strip() for item in additions):
                            raise ValueError('追加材料必须是非空路径组成的数组')
                        material['corpus_additions'] = [str(validate_path(item, allowed_roots=roots,
                            must_exist=True, kind='any', follow_symlinks=False)) for item in additions]
                    if payload.get('question'):
                        material['question'] = str(payload['question'])
                    if 'scope_declaration' in payload:
                        material['scope_declaration'] = payload['scope_declaration']
                    if 'scope_clarifications' in payload:
                        material['scope_clarifications'] = payload['scope_clarifications']
                    _json(self, 202, self.state.sessions.add_materials(sid, material)); return
                if action == 'messages':
                    _json(self, 202, self.state.sessions.submit(sid, str(payload.get('question') or payload.get('message') or ''))); return
                if action == 'cancel':
                    _json(self, 202, self.state.sessions.cancel(sid)); return
                if action == 'resume':
                    _json(self, 202, self.state.sessions.resume(sid)); return
            if path == "/api/draft/preview":
                cfg = self.state.config.get()
                security = cfg.get("security", {})
                roots = None if security.get("allow_external_paths", True) else list(security.get("allowed_roots") or [])
                if roots == []:
                    raise ValueError("security.allowed_roots is required")
                source = validate_path(str(payload.get("draft") or ""), allowed_roots=roots, must_exist=True, kind="file", follow_symlinks=bool(security.get("follow_symlinks")))
                if source.suffix.lower() == ".pdf":
                    from .parser import extract_text
                    text = "\n".join(page["text"] for page in extract_text(source))
                elif source.suffix.lower() in {".md", ".txt", ".csv"}:
                    text = source.read_text(encoding="utf-8-sig", errors="replace")
                else:
                    raise ValueError("预览支持 PDF、Markdown 和文本")
                _json(self, 200, {"text": text[:300000], "truncated": len(text) > 300000, "sha256": sha256_file(source)}); return
            if path == "/api/demo":
                _json(self, 200, _prepare_demo(self.state.config)); return
            if path == "/api/shutdown":
                self.server.cancel_activities()
                _json(self, 202, {"ok": True, "message": "明镜正在退出，已请求取消活动任务。"})
                threading.Thread(target=self.server.shutdown, name="mingjing-shutdown", daemon=True).start()
                return
            if path == "/api/config/validate":
                try:
                    candidate = AppConfig.from_dict(payload.get("config") if isinstance(payload.get("config"), dict) else self.state.config.public()).ensure_defaults()
                    issues = validate_config(candidate)
                    _json(self, 200, {"ok": not issues, "issues": issues, "config": candidate.to_dict()})
                except Exception as exc:
                    _json(self, 400, {"ok": False, "issues": [str(exc)]})
                return
            if path == "/api/config/test-path":
                cfg = self.state.config.get()
                security = cfg.get("security", {}) if isinstance(cfg, dict) else {}
                external = bool(security.get("allow_external_paths", True))
                roots = list(security.get("allowed_roots") or []) if not external else None
                try:
                    if roots == []:
                        raise ConfigError("security.allowed_roots is required when external paths are disabled")
                    p = validate_path(str(payload.get("path", "")), allowed_roots=roots, must_exist=False, follow_symlinks=bool(security.get("follow_symlinks", False)))
                    _json(self, 200, {"ok": p.exists(), "path": str(p), "is_file": p.is_file(), "is_dir": p.is_dir(), "readable": os.access(p, os.R_OK), "writable": os.access(p, os.W_OK)})
                except ConfigError as exc:
                    _json(self, 400, {"ok": False, "error": str(exc)})
                return
            if path == "/api/config/test-llm":
                _json(self, 200, self._test_llm(payload)); return
            if path == "/api/config/test-harness":
                _json(self, 200, self._test_harness(payload)); return
            if path == "/api/run":
                payload = _effective_run_request(payload, self.state.config.get())
                required = ['corpus'] if payload.get('task_type') == 'analysis' else ["draft", "corpus"]
                missing = [key for key in required if not payload.get(key)]
                if missing: _json(self, 400, {"error": "missing fields", "fields": missing}); return
                if str(payload.get("mode") or "kernel") not in {"kernel", "agent", "openai", "harness"}:
                    _json(self, 400, {"error": "unsupported run mode"}); return
                task = self.state.launch(payload)
                value = self.state.config.update({"workspace": {"draft_path": str(payload.get("draft") or ''), "corpus_root": str(payload["corpus"])},
                                                  "run": {"company": str(payload.get("company") or ""), "period": str(payload.get("period") or ""), "report_date": str(payload.get("report_date") or ""), "mode": {"agent": "openai"}.get(str(payload.get("mode") or "kernel"), str(payload.get("mode") or "kernel"))}})
                _json(self, 202, {**task.summary(), "config_revision": value.get("revision")}); return
            if path.startswith("/api/runs/") and path.endswith("/cancel"):
                run_id = path.split("/")[3]; task = self.state.get_task(run_id)
                if not task: _json(self, 404, {"error": "run not found"}); return
                task.cancelled.set(); _json(self, 202, task.summary()); return
            if path.startswith('/api/runs/') and path.endswith('/resume'):
                task = self.state.get_task(path.split('/')[3])
                if not task: _json(self, 404, {'error': 'run not found'}); return
                task.resume(lifecycle_lock=self.state.lock, is_closing=lambda: self.state.sessions.closing)
                _json(self, 202, task.summary()); return
            if path.startswith('/api/runs/') and path.endswith('/rerun'):
                run_id = path.split('/')[3]
                if not self.state.get_task(run_id): _json(self, 404, {'error': 'run not found'}); return
                task = self.state.rerun(run_id, payload)
                _json(self, 202, {**task.summary(), 'rerun_scope': 'complete_new_task',
                                  'config_revision': task.config_snapshot.get('revision')}); return
            if path.startswith('/api/runs/') and path.endswith('/reparse'):
                run_id = path.split('/')[3]
                if not self.state.get_task(run_id): _json(self, 404, {'error': 'run not found'}); return
                _json(self, 201, self.state.reparse(run_id, payload)); return
            if path == "/api/replay":
                if payload.get('kind') == 'message':
                    if set(payload) - {'kind', 'session_id', 'message_id'}:
                        raise ValueError('message replay accepts only registered session and message IDs')
                    _json(self, 200, self.state.sessions.replay_message(
                        str(payload.get('session_id') or ''), str(payload.get('message_id') or ''))); return
                requested = Path(str(payload.get("run") or ""))
                run = validate_path(requested, allowed_roots=self.state.history_roots, must_exist=True, kind='dir', follow_symlinks=False)
                task = self.state.get_task(run.name)
                if task is None or task.out.resolve() != run.resolve():
                    _json(self, 400, {"error": "run path is outside the configured runs directory"}); return
                results = run / "results.json"
                if not results.exists(): _json(self, 404, {"error": "results.json not found"}); return
                if payload.get('mode') == 'agent':
                    from .agent_replay import replay_agent
                    outcome = replay_agent(run)
                else:
                    from .replay import replay_run
                    outcome = replay_run(run)
                from .execution import project_execution
                _json(self, 200, dict(outcome, source_execution=project_execution(json.loads(results.read_text(encoding='utf-8'))),
                                     validation_scope='reproducibility_only')); return
            if path == "/api/config/import":
                value = payload.get("config") if isinstance(payload.get("config"), dict) else payload
                _json(self, 200, self.state.config.update(value, payload.get("revision"))); return
            _json(self, 404, {"error": "unknown endpoint"})
        except ValueError as exc:
            _json(self, 409 if "revision" in str(exc) else 400, {"error": str(exc)})
        except Exception as exc:
            _json(self, 500, {"error": str(exc)})

    def _test_harness(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .harness_client import HarnessRuntime
        cfg = self.state.config.get()
        home = validate_config_path(cfg, _default_root() / 'diagnostics' / f'harness-{uuid.uuid4().hex[:12]}',
                                    field='service.harness_diagnostics', kind='dir', storage=True)
        runtime = HarnessRuntime(home=home,
                                 request_handler=lambda method, params: (_ for _ in ()).throw(PermissionError('Health check has no model or tool access')),
                                 config=cfg)
        owner = SimpleNamespace(run_id='harness-health-' + uuid.uuid4().hex[:12], runtime=runtime, cancelled=threading.Event())
        lifecycle = getattr(self.state, 'diagnostic_lifecycle', None)
        observed = False
        try:
            if lifecycle:
                lifecycle('start', owner)
                observed = True
            description = runtime.start()
            if owner.cancelled.is_set():
                raise InterruptedError('Harness health check cancelled by shutdown')
            return {'ok': True, 'runtime_available': True, 'semantic_integration': True, 'description': description,
                    'agent_capability': 'not_tested', 'message': '原生 Harness 已启动；请使用 Agent 能力测试验证真实工具闭环。'}
        except Exception as exc:
            return {'ok': False, 'runtime_available': False, 'semantic_integration': False,
                    'fallback': 'kernel', 'reason': type(exc).__name__, 'message': '原生运行时未通过启动检测，任务将记录纯内核降级。'}
        finally:
            try:
                runtime.close()
            finally:
                if observed:
                    lifecycle('end', owner)

    def _test_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Perform a bounded health request without exposing credentials."""
        import urllib.request
        from . import http_transport

        cfg = self.state.config.get().get("llm", {})
        base = str(payload.get("base_url") or cfg.get("base_url") or "").rstrip("/")
        model = str(payload.get("model") or cfg.get("model") or "")
        if not base or not model:
            return {"ok": False, "message": "请先填写 Base URL 和模型"}
        chat_path = str(payload.get("chat_path") or cfg.get("chat_path") or "/chat/completions")
        endpoint = base + (chat_path if chat_path.startswith("/") else "/" + chat_path)
        token = self.state.config.secret(str(cfg.get("api_key_ref") or "llm_api_key"))
        if payload.get("api_key"):
            token = str(payload["api_key"])
        try:
            max_tokens = max(8, int(payload.get("max_tokens") or cfg.get("max_tokens") or 32))
        except (TypeError, ValueError):
            max_tokens = 32
        try:
            temperature = float(payload.get("temperature") if payload.get("temperature") is not None else cfg.get("temperature") or 0)
        except (TypeError, ValueError):
            temperature = 0.0
        body = {"model": model, "temperature": temperature, "max_tokens": max_tokens, "messages": [{"role": "user", "content": "Reply with the single word OK."}]}
        thinking = _thinking_config(payload.get("thinking") or cfg.get("thinking"), model)
        if thinking:
            body["thinking"] = thinking
        req = urllib.request.Request(endpoint, method="POST", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": "Bearer " + token} if token else {"Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with http_transport.urlopen(req, timeout=max(1, float(payload.get("timeout_s") or cfg.get("timeout_s") or 15))) as response:
                raw = response.read()
            body_response = json.loads(raw.decode("utf-8"))
            if not isinstance(body_response, dict) or not isinstance(body_response.get("choices"), list) or not body_response["choices"]:
                raise ValueError("响应缺少 choices")
            choice = body_response["choices"][0]
            if not isinstance(choice, dict):
                raise ValueError("响应 choice 类型无效")
            message = choice.get("message") or {}
            if not isinstance(message, dict):
                raise ValueError("响应 message 类型无效")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("响应 content 为空或仅包含 reasoning_content")
            if str(choice.get("finish_reason") or "") == "length":
                raise ValueError("响应被 max_tokens 截断")
            return {"ok": True, "message": "连接成功，已验证非空完整回复", "latency_ms": round((time.monotonic() - started) * 1000), "model": body_response.get("model") or model, "finish_reason": choice.get("finish_reason")}
        except Exception as exc:
            return {"ok": False, "message": _redact(f"连接失败：{type(exc).__name__}: {exc}", [token]), "latency_ms": round((time.monotonic() - started) * 1000)}

    @staticmethod
    def _module_available(name: str) -> bool:
        try:
            import importlib.util
            return importlib.util.find_spec(name) is not None
        except Exception:
            return False

    @staticmethod
    def _pdf_available() -> bool:
        return Handler._module_available("fitz")

    def do_DELETE(self) -> None:
        if not self._authorized(): _json(self, 401, {"error": "unauthorized"}); return
        parsed = urlparse(self.path)
        if not self.state.smart.handle(self, 'DELETE', parsed.path.rstrip('/'), parsed.query):
            _json(self, 404, {"error": "not found"})

    def do_PATCH(self) -> None:
        if not self._authorized(): _json(self, 401, {"error": "unauthorized"}); return
        parsed = urlparse(self.path)
        if self.state.smart.handle(self, 'PATCH', parsed.path.rstrip('/'), parsed.query):
            return
        try:
            payload = _body(self)
            secrets_payload = payload.pop("secrets", {}) if isinstance(payload.get("secrets"), dict) else {}
            with self.state.config._lock:
                current = self.state.config.get()
                if payload.get('revision') is not None and payload['revision'] != current.get('revision'):
                    raise ValueError('revision conflict')
                candidate = json.loads(json.dumps(current))
                _deep_merge(candidate, payload)
                issues = validate_config(AppConfig.from_dict(candidate).ensure_defaults())
                if issues:
                    raise ValueError('; '.join(issues))
                for ref, secret in secrets_payload.items():
                    if isinstance(secret, str) and secret:
                        self.state.config.set_secret(str(ref), secret)
                value = self.state.config.update(payload, payload.get("revision"))
            _json(self, 200, value)
        except ValueError as exc:
            _json(self, 409 if "revision" in str(exc) else 400, {"error": str(exc)})
        except Exception as exc:
            _json(self, 500, {"error": str(exc)})


class MingJingServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: AppState):
        from .config import validate_server_binding
        validate_server_binding(address[0], state.config.get().get('server', {}), state.config.secret,
                                port=address[1])
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.state = state
        self._bound_ready = False
        super().__init__(address, Handler)
        self._bound_ready = True

    def cancel_activities(self) -> None:
        self.state.smart.cancel_all()
        with self.state.lock:
            self.state.sessions.closing = True
            diagnostics = list(self.state.diagnostics.values())
            tasks = list(self.state.tasks.values())
            for task in tasks:
                if task.status in {'queued', 'running'}:
                    task.cancelled.set()
            for record in diagnostics:
                record['agent'].cancelled.set()
        self.state.sessions.cancel_running()
        for record in diagnostics:
            record['agent'].cancelled.set()
        for task in tasks:
            if task.status in {'queued', 'running'}:
                task.cancelled.set()

    def shutdown(self) -> None:
        self.cancel_activities()
        super().shutdown()

    def server_close(self) -> None:
        if not self._bound_ready:
            # HTTPServer closes a failed bind during construction. The retry
            # on an automatic port must retain the live application state.
            super().server_close()
            return
        self.cancel_activities()
        deadline = time.monotonic() + 3
        self.state.sessions.wait_running(max(0, deadline - time.monotonic()))
        for task in list(self.state.tasks.values()):
            if task.thread is not threading.current_thread() and task.thread.is_alive():
                task.thread.join(max(0, deadline - time.monotonic()))
        for record in list(self.state.smart.runs.values()):
            worker = record.get('_thread')
            if worker and worker is not threading.current_thread() and worker.is_alive():
                worker.join(max(0, deadline - time.monotonic()))
        with self.state.lock:
            diagnostics = list(self.state.diagnostics.values())
        for record in diagnostics:
            worker = record['worker']
            if worker is not threading.current_thread() and worker.is_alive():
                worker.join(max(0, deadline - time.monotonic()))
        # A hung provider/runtime must not outlive application shutdown. These
        # are the exact child handles owned by this host, never external PIDs.
        agents = list(self.state.sessions.active.values()) + [getattr(task, 'agent', None) for task in self.state.tasks.values()]
        agents += [record['agent'] for record in diagnostics]
        for agent in agents:
            process = getattr(getattr(agent, 'runtime', None), '_process', None)
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
        super().server_close()


def serve(host: str | None = None, port: int | None = None, open_browser: bool = True) -> MingJingServer:
    state = AppState()
    configured = state.config.public().get("server", {})
    host = host if host is not None else str(configured.get("host") or "127.0.0.1")
    port = int(port) if port is not None else int(configured.get("port") or 0)
    try:
        httpd = MingJingServer((host, port), state)
    except OSError as exc:
        if not port or (exc.errno not in {errno.EADDRINUSE, errno.EACCES, 10048, 10013} and getattr(exc, "winerror", None) not in {10048, 10013}):
            raise
        httpd = MingJingServer((host, 0), state)
        print(f"MingJing configured port {port} is occupied; selected {httpd.server_port}", flush=True)
    display_host = f"[{host}]" if ":" in host else host
    url = f"http://{display_host}:{httpd.server_port}/"
    if open_browser and bool(configured.get("auto_open_browser", True)):
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    print(f"MingJing Web: {url}", flush=True)
    return httpd


def run(host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> None:
    httpd = serve(host, port, open_browser)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mingjing-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    run(args.host, args.port, not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
