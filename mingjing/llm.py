from __future__ import annotations

"""Optional OpenAI-compatible semantic adapter.

The model may identify source spans and labels, but its numbers are never
consumed as facts or verdicts.  The deterministic kernel remains authoritative.
"""

import json
import math
import hashlib
import http.client
import os
import re
import time
import urllib.request
from typing import Any

from .http_transport import urlopen, safe_error_message, redact_value


class LLMUnavailable(RuntimeError):
    """Raised when the optional semantic model cannot produce valid output."""


def enabled() -> bool:
    return bool(os.getenv("MINGJING_LLM_BASE_URL") and os.getenv("MINGJING_LLM_API_KEY") and os.getenv("MINGJING_LLM_MODEL"))


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _chunks(text: str, max_chars: int) -> list[str]:
    """Split long drafts at paragraph/newline boundaries where possible."""
    if len(text) <= max_chars:
        return [text]
    output: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind("。", start, end), text.rfind(".", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        part = text[start:end].strip()
        if part:
            output.append(part)
        start = end
    return output or [""]


def _relevant_excerpt(text: str, limit: int = 48000) -> str:
    """Select report lines that can contain verifiable financial claims.

    This is a transport/privacy bound, not a fact extractor: every retained
    line is copied verbatim from the draft and deterministic extraction still
    decides the numeric value and verdict.
    """
    terms = (
        "营业收入", "营业总收入", "营业成本", "归母净利润", "归属于母公司",
        "净利润", "扣非", "经营活动产生", "经营现金流", "现金流",
        "毛利率", "净利率", "资产负债率", "净资产收益率", "ROE", "每股收益",
        "营收",
    )
    number = re.compile(r"[-+]?\d[\d,，]*(?:\.\d+)?")
    lines = text.splitlines()
    selected: list[str] = []
    for idx, line in enumerate(lines):
        if not number.search(line) or not any(term in line for term in terms):
            continue
        # Keep one neighboring line for table labels split across PDF blocks.
        for pos in (idx - 1, idx, idx + 1):
            if 0 <= pos < len(lines) and lines[pos].strip() and lines[pos] not in selected:
                selected.append(lines[pos])
    excerpt = "\n".join(selected)
    if len(excerpt) > limit:
        excerpt = excerpt[:limit]
    return excerpt or text[:limit]


def _json_payload(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model response content is empty")
    value_text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", value_text, flags=re.I | re.S)
    if fenced:
        value_text = fenced.group(1).strip()
    try:
        value = json.loads(value_text)
    except json.JSONDecodeError:
        candidates = [x for x in (value_text.find("["), value_text.find("{")) if x >= 0]
        first = min(candidates, default=-1)
        last = max(value_text.rfind("]"), value_text.rfind("}"))
        if first < 0 or last <= first:
            raise ValueError("model response is not JSON")
        value = json.loads(value_text[first : last + 1])
    if isinstance(value, dict):
        value = value.get("claims", value.get("items", value.get("statements")))
    if not isinstance(value, list):
        raise ValueError("model response must be a JSON array")
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        clean: dict[str, Any] = {}
        for key in ("text", "source", "source_text", "type", "claim_type", "account", "period", "relation", "label"):
            val = item.get(key)
            if isinstance(val, (str, int, float, bool)):
                clean[key] = str(val) if key not in {"type", "claim_type"} else val
        if clean.get("source") is None and clean.get("source_text") is not None:
            clean["source"] = clean.pop("source_text")
        if clean.get("text") or clean.get("source"):
            items.append(clean)
    return items


def _thinking(model: str) -> dict[str, str] | None:
    configured = os.getenv("MINGJING_LLM_THINKING")
    if configured:
        lowered = configured.strip().lower()
        if lowered in {"0", "false", "off", "disabled", "none"}:
            return {"type": "disabled"}
        if lowered in {"1", "true", "on", "enabled"}:
            return {"type": "enabled"}
    if model.lower().startswith("deepseek"):
        return {"type": "disabled"}
    return None


def _request(endpoint: str, token: str, payload: dict[str, Any], timeout: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    request = urllib.request.Request(endpoint, method="POST")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    started = time.monotonic()
    payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    request_hash = hashlib.sha256(payload_bytes).hexdigest()
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urlopen(request, data=payload_bytes, timeout=timeout) as response:
                body_bytes = response.read()
            body = json.loads(body_bytes.decode("utf-8"))
            break
        except (TimeoutError, OSError, http.client.IncompleteRead) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.25)
                continue
            raise LLMUnavailable(safe_error_message(exc, [token])) from None
        except Exception as exc:
            raise LLMUnavailable(safe_error_message(exc, [token])) from None
    else:
        raise LLMUnavailable(safe_error_message(last_error, [token]) if last_error else "model request failed") from None
    elapsed_ms = round((time.monotonic() - started) * 1000)
    try:
        choice = body["choices"][0]
        message = choice.get("message") or {}
        finish = str(choice.get("finish_reason") or "")
        content = message.get("content")
        if finish == "length":
            raise ValueError("model response truncated (finish_reason=length)")
        claims = _json_payload(content)
    except LLMUnavailable:
        raise
    except Exception as exc:
        raise LLMUnavailable(safe_error_message(exc, [token])) from None
    return redact_value(claims, [token]), redact_value({"latency_ms": elapsed_ms, "finish_reason": finish, "response_bytes": len(body_bytes),
                    "actual_model": body.get("model"), "usage": body.get("usage"),
                    "request_hash": request_hash, "response_hash": hashlib.sha256(body_bytes).hexdigest()}, [token])


def decompose_with_metadata(text: str, *, max_chars: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract source-backed semantic claims using bounded requests."""
    if not enabled():
        raise LLMUnavailable("MINGJING_LLM_BASE_URL/API_KEY/MODEL is not configured")
    base = os.environ["MINGJING_LLM_BASE_URL"].rstrip("/")
    path = os.getenv("MINGJING_LLM_CHAT_PATH", "/chat/completions") or "/chat/completions"
    endpoint = base + (path if path.startswith("/") else "/" + path)
    model = os.environ["MINGJING_LLM_MODEL"]
    timeout = _float_env("MINGJING_LLM_TIMEOUT_S", 45.0)
    max_tokens = _int_env("MINGJING_LLM_MAX_TOKENS", 2048)
    temperature = _float_env("MINGJING_LLM_TEMPERATURE", 0.0)
    # Keep each request small enough that reasoning-capable gateways can
    # return a complete JSON array within the configured output budget.
    chunk_size = max_chars or _int_env("MINGJING_LLM_CHUNK_CHARS", 1800, 800)
    source_text = _relevant_excerpt(text)
    chunks = _chunks(source_text, chunk_size)
    all_claims: list[dict[str, Any]] = []
    timings: list[dict[str, Any]] = []
    system = ("Extract all historical numeric company financial statements from the supplied report excerpt (maximum 12). Return ONLY a JSON array. "
              "Each item must include `source`, an exact verbatim substring copied from the excerpt, and may include "
              "`type`, `account`, `period`, or `relation`. Each source must be shorter than 100 characters. Prioritize company revenue, profits, cash flow, margins and their year-on-year changes. Skip dates, page numbers, codes, headings, forecasts, market-wide statistics, "
              "and non-financial prose. Do not invent or correct values; do not provide verdicts, formulas, "
              "or recommendations.")
    for index, chunk in enumerate(chunks):
        payload: dict[str, Any] = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": f"Excerpt {index + 1}/{len(chunks)}:\n{chunk}"}]}
        thinking = _thinking(model)
        if thinking is not None:
            payload["thinking"] = thinking
        try:
            claims, timing = _request(endpoint, os.environ["MINGJING_LLM_API_KEY"], payload, timeout)
        except LLMUnavailable as exc:
            # A long list can exhaust the provider's output budget. Retry the
            # same bounded excerpt once with a larger cap; this remains a
            # semantic hint only and never changes deterministic numbers.
            if "truncated" not in str(exc).lower():
                raise
            retry_payload = dict(payload)
            retry_payload["max_tokens"] = min(max(max_tokens * 2, 4096), 8192)
            claims, timing = _request(endpoint, os.environ["MINGJING_LLM_API_KEY"], retry_payload, timeout)
            timing["retry"] = True
            timing["retry_max_tokens"] = retry_payload["max_tokens"]
        all_claims.extend(claims)
        timings.append({"chunk": index + 1, "chars": len(chunk), **timing})
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in all_claims:
        key = (str(item.get("source") or item.get("text") or ""), str(item.get("type") or item.get("claim_type") or ""))
        if key[0] and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique, {"chunks": len(chunks), "source_chars": len(text), "sent_chars": len(source_text), "model": model, "endpoint": endpoint, "temperature": temperature, "max_tokens": max_tokens, "thinking": _thinking(model), "timeout_s": timeout, "actual_models": sorted({t['actual_model'] for t in timings if t.get('actual_model')}), "usage": {key: sum((t.get('usage') or {}).get(key, 0) or 0 for t in timings) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}, "chunk_stats": timings}


def decompose(text: str) -> list[dict[str, Any]]:
    return decompose_with_metadata(text)[0]
