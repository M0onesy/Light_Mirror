"""Credential-safe HTTP transport for Python provider diagnostics and adapters."""
from __future__ import annotations

import ipaddress
import math
import ssl
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class HTTPPolicyError(ValueError):
    """An endpoint or redirect violated the configured transport boundary."""


def _origin(url: str) -> tuple[str, str, int]:
    try:
        if not isinstance(url, str) or any(ord(char) < 33 or ord(char) == 127 for char in url):
            raise ValueError()
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.fragment:
            raise ValueError()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError()
        return parts.scheme, parts.hostname.lower(), port
    except (TypeError, ValueError):
        raise HTTPPolicyError("HTTP endpoint must be an HTTP(S) URL without credentials or a fragment") from None


def validate_endpoint(url: str) -> None:
    scheme, host, _ = _origin(url)
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if scheme != "https" and not loopback:
        raise HTTPPolicyError("Provider endpoints require HTTPS outside loopback")


class SameOriginRedirectHandler(HTTPRedirectHandler):
    """Preserve credentials only when a redirect stays inside the original origin."""

    def __init__(self, initial_url: str):
        self.initial_origin = _origin(initial_url)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(newurl) != self.initial_origin:
            raise HTTPPolicyError("Cross-origin or HTTPS downgrade redirect rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(request: Request | str, data: bytes | None = None, timeout: float = 30,
            *, verify_tls: bool = True):
    """Open one bounded request with verified TLS and no credential forwarding."""
    if verify_tls is not True:
        raise HTTPPolicyError("TLS certificate validation must remain enabled")
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0:
        raise HTTPPolicyError("HTTP timeout must be a positive finite number")
    endpoint = request.full_url if isinstance(request, Request) else request
    validate_endpoint(endpoint)
    opener = build_opener(SameOriginRedirectHandler(endpoint), HTTPSHandler(context=ssl.create_default_context()))
    return opener.open(request, data=data, timeout=timeout)


def redact_text(value: str, secret_values: Iterable[str] = ()) -> str:
    for secret in sorted({item for item in secret_values if isinstance(item, str) and item}, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return value


def redact_value(value: Any, secret_values: Iterable[str] = ()) -> Any:
    """Redact both keys and values in provider-controlled structured metadata."""
    secrets = tuple(secret_values)
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, dict):
        return {redact_text(key, secrets) if isinstance(key, str) else key: redact_value(child, secrets)
                for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(child, secrets) for child in value]
    return value


def safe_error_message(error: BaseException, secret_values: Iterable[str] = ()) -> str:
    """Do not persist provider-controlled status text, URLs, or response bodies."""
    if isinstance(error, HTTPPolicyError):
        return redact_text(str(error), secret_values)
    if isinstance(error, HTTPError):
        code = error.code if type(error.code) is int else "unknown"
        return f"HTTP request failed (status {code})"
    if isinstance(error, URLError):
        return "HTTP connection failed"
    if isinstance(error, TimeoutError):
        return "HTTP request timed out"
    return redact_text(f"{type(error).__name__}: {error}", secret_values)
