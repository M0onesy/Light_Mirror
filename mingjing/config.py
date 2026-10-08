"""Application configuration and local secret storage for MingJing.

The configuration file intentionally contains references to secrets only.  On
Windows, secret values are protected with the current user's DPAPI profile.
Encryption failure rejects persistence. Non-Windows development retains keys
in process memory only; historical encrypted fallback files are read for a
one-time DPAPI migration on Windows.
"""

from __future__ import annotations

import base64
import copy
import ctypes
import hashlib
import hmac
import json
import math
import os
import secrets
import stat
import tempfile
import threading
from ctypes import wintypes
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from .file_io import atomic_write_text, io_path, record_path

from .providers import (AgentConfig, ProviderConfig, ModelConfig, RouteConfig,
                        ProviderError, parse_agent, parse_providers,
                        validate_providers, freeze_agent_config,
                        reject_plaintext_credentials, SECRET_REF)


CONFIG_VERSION = 2


class ConfigError(ValueError):
    """Raised when configuration or a requested filesystem path is invalid."""


class RevisionConflict(ConfigError):
    """Raised when a stale configuration revision attempts to overwrite data."""


def _local_appdata() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if value:
        return Path(value)
    # XDG is useful for tests and Linux development; Windows has LOCALAPPDATA.
    value = os.environ.get("XDG_CONFIG_HOME")
    if value:
        return Path(value)
    return Path.home() / ".config"


def app_root() -> Path:
    if os.environ.get("MINGJING_HOME"):
        return Path(os.environ["MINGJING_HOME"]).expanduser().resolve()
    return _local_appdata() / "MingJing"


def default_config_path() -> Path:
    return app_root() / "config.json"


def _default_workspace() -> Path:
    return app_root() / "workspace"


@dataclass
class WorkspaceConfig:
    data_root: str = ""
    draft_path: str = ""
    corpus_root: str = ""
    corpus_index: str = ""
    knowledge_root: str = ""
    facts_path: str = ""
    output_root: str = ""
    temp_root: str = ""


@dataclass
class RunConfig:
    mode: str = "kernel"
    company: str = ""
    period: str = ""
    report_date: str = ""
    scope: str = "合并"
    nr: str = "未扣非"
    version_strategy: str = "latest"
    selected_rules: list[str] = field(default_factory=list)
    timeout_s: int = 120
    retries: int = 1


@dataclass
class LLMConfig:
    enabled: bool = False
    provider: str = "openai_compatible"
    base_url: str = ""
    chat_path: str = "/chat/completions"
    model: str = ""
    api_key_ref: str = "llm_api_key"
    temperature: float = 0.0
    max_tokens: int = 2000
    timeout_s: int = 60
    verify_tls: bool = True
    thinking: str = "auto"


@dataclass
class HarnessConfig:
    enabled: bool = False
    # Previously stored Desktop/CLI paths are retained but inactive unless
    # the user deliberately selects advanced external runtime mode.
    runtime_mode: str = "embedded"
    executable: str = ""
    home: str = ""
    profile: str = "sdk-minimal"
    patch_file: str = ""
    working_dir: str = ""
    timeout_s: int = 120
    model: str = ""
    api_key_ref: str = "harness_api_key"
    mcp_enabled: bool = False
    disable_network: bool = True
    disable_telemetry: bool = True
    disable_shell: bool = True
    disable_code_mode: bool = True


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 0
    auto_open_browser: bool = True
    bearer_token_required: bool = False
    bearer_token_ref: str = "server_bearer"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    directory: str = ""
    retain_runs: int = 30
    retain_days: int = 90
    redact_secrets: bool = True


@dataclass
class SecurityConfig:
    allowed_roots: list[str] = field(default_factory=list)
    allow_external_paths: bool = True
    follow_symlinks: bool = False
    max_upload_mb: int = 200


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    revision: int = 0
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    run: RunConfig = field(default_factory=RunConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    agent: AgentConfig = field(default_factory=AgentConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "AppConfig":
        """Build a config while ignoring unknown keys from newer clients."""
        value = migrate_config(value or {})
        result = cls()
        for name, typ in (
            ("workspace", WorkspaceConfig),
            ("run", RunConfig),
            ("llm", LLMConfig),
            ("harness", HarnessConfig),
            ("server", ServerConfig),
            ("logging", LoggingConfig),
            ("security", SecurityConfig),
        ):
            raw = value.get(name, {})
            if not isinstance(raw, Mapping):
                raise ConfigError(f"{name} must be an object")
            allowed = {f.name for f in fields(typ)}
            kwargs = {k: copy.deepcopy(raw[k]) for k in allowed if k in raw}
            setattr(result, name, typ(**kwargs))
        if "version" in value:
            result.version = value["version"]
        if "revision" in value:
            result.revision = value["revision"]
        try:
            result.providers = parse_providers(value.get("providers", {}))
            # Missing budget fields take the current defaults; explicitly
            # stored user limits remain authoritative, including older values.
            result.agent = parse_agent(value.get("agent", {}))
        except ProviderError as exc:
            raise ConfigError(str(exc)) from exc
        return result

    def ensure_defaults(self) -> "AppConfig":
        """Populate paths derived from the local application root."""
        workspace = self.workspace
        root = _default_workspace()
        if not workspace.data_root:
            workspace.data_root = str(root)
        if not workspace.output_root:
            workspace.output_root = str(root / "runs")
        if not workspace.temp_root:
            workspace.temp_root = str(root / "tmp")
        if not workspace.corpus_root and os.environ.get("MINGJING_CORPUS_ROOT"):
            workspace.corpus_root = os.environ["MINGJING_CORPUS_ROOT"]
        if not workspace.knowledge_root and os.environ.get("MINGJING_KB_ROOT"):
            workspace.knowledge_root = os.environ["MINGJING_KB_ROOT"]
        # Web model settings are explicit and must not absorb environment
        # values from another in-process frozen task. The separate CLI still
        # reads its documented MINGJING_LLM_* environment contract directly.
        if not self.logging.directory:
            self.logging.directory = str(app_root() / "logs")
        return self


def _legacy_provider(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map existing API settings verbatim into one provider without a key."""
    llm = raw.get("llm", {})
    if not isinstance(llm, Mapping) or not llm.get("base_url") or not llm.get("model"):
        return None
    base = str(llm["base_url"])
    from urllib.parse import urlsplit
    official = urlsplit(base).hostname == "api.deepseek.com"
    thinking = llm.get("thinking", "auto")
    reasoning = "off" if thinking == "disabled" else "high" if thinking == "enabled" else "auto"
    return {
        "enabled": bool(llm.get("enabled", False)), "display_name": "原有 API 配置",
        "vendor": "deepseek" if official else "custom",
        "protocol": "deepseek" if official else "openai-completions",
        "base_url": base, "chat_path": llm.get("chat_path", "/chat/completions"),
        "credential_ref": llm.get("api_key_ref", "llm_api_key"),
        "models": [{"id": llm["model"], "max_tokens": int(llm.get("max_tokens") or 4096),
                    "temperature": llm.get("temperature"), "reasoning": reasoning}],
        "timeout_s": int(llm.get("timeout_s") or 60), "retries": 2,
        "verify_tls": bool(llm.get("verify_tls", True)), "material_access": True,
    }


def _scalar_config_issues(value: Mapping[str, Any]) -> list[str]:
    """Check JSON scalar types before truthiness or numeric coercion is used."""
    issues = []
    for name, default in (("version", CONFIG_VERSION), ("revision", 0)):
        scalar = value.get(name, default)
        if type(scalar) is not int or scalar < (1 if name == "version" else 0):
            issues.append(f"configuration.{name} must be a non-negative integer" if name == "revision"
                          else "configuration.version must be a positive integer")
    for section, typ in (("workspace", WorkspaceConfig), ("run", RunConfig),
                         ("llm", LLMConfig), ("harness", HarnessConfig),
                         ("server", ServerConfig), ("logging", LoggingConfig),
                         ("security", SecurityConfig)):
        raw = value.get(section, {})
        if not isinstance(raw, Mapping):
            issues.append(f"{section} must be an object")
            continue
        for definition in fields(typ):
            if definition.name not in raw:
                continue
            scalar = raw[definition.name]
            label = f"{section}.{definition.name}"
            declared = definition.type
            if declared == "bool" and type(scalar) is not bool:
                issues.append(f"{label} must be boolean")
            elif declared == "int" and type(scalar) is not int:
                issues.append(f"{label} must be an integer")
            elif declared == "float" and (type(scalar) not in {int, float}
                                           or isinstance(scalar, float) and not math.isfinite(scalar)):
                issues.append(f"{label} must be a finite number")
            elif declared == "str" and not isinstance(scalar, str):
                issues.append(f"{label} must be text")
            elif declared == "list[str]" and (not isinstance(scalar, list) or any(not isinstance(item, str) for item in scalar)):
                issues.append(f"{label} must be an array of text")
    return issues


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError("configuration contains a non-finite number")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_nonfinite(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_nonfinite(child)


def migrate_config(value: Mapping[str, Any]) -> dict[str, Any]:
    """Upgrade v1 fields in memory while retaining the original key reference."""
    if not isinstance(value, Mapping):
        raise ConfigError("configuration must be an object")
    raw = copy.deepcopy(dict(value))
    _reject_nonfinite(raw)
    issues = _scalar_config_issues(raw)
    if issues:
        raise ConfigError("; ".join(issues))
    version = raw.get("version", 1)
    if version > CONFIG_VERSION:
        raise ConfigError(f"Configuration version {version} is newer than supported {CONFIG_VERSION}")
    if version < 1:
        raise ConfigError("unsupported configuration version")
    try:
        reject_plaintext_credentials(raw)
    except ProviderError as exc:
        raise ConfigError(str(exc)) from exc
    if version == 1:
        provider = _legacy_provider(raw)
        raw.setdefault("providers", {})
        raw.setdefault("agent", {})
        if provider and "legacy-default" not in raw["providers"]:
            raw["providers"]["legacy-default"] = provider
            raw["agent"].setdefault("enabled", bool(provider["enabled"]))
            raw["agent"].setdefault("routing", {}).setdefault("default", {"provider": "legacy-default", "model": provider["models"][0]["id"]})
        raw["version"] = CONFIG_VERSION
    return raw


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+'\n',
                      sync=True, create_parents=True)


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    target = Path(path) if path else default_config_path()
    if not io_path(target).exists():
        return AppConfig().ensure_defaults()
    try:
        with io_path(target).open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Unable to read configuration: {target}: {exc}") from exc
    config = AppConfig.from_dict(raw)
    if config.version > CONFIG_VERSION:
        raise ConfigError(f"Configuration version {config.version} is newer than supported {CONFIG_VERSION}")
    config.ensure_defaults()
    issues = validate_config(config)
    if issues:
        raise ConfigError("; ".join(issues))
    return config


def save_config(config: AppConfig, path: str | os.PathLike[str] | None = None) -> Path:
    target = Path(path) if path else default_config_path()
    config.ensure_defaults()
    issues = validate_config(config)
    if issues:
        raise ConfigError("; ".join(issues))
    _atomic_write_json(target, config.to_dict())
    return target


def _deep_update(target: Any, values: Mapping[str, Any]) -> None:
    for key, value in values.items():
        if key in {"version", "revision"}:
            continue
        if not hasattr(target, key):
            continue
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, Mapping):
            _deep_update(current, value)
        elif key not in {"api_key", "token", "secret", "api_token"}:
            setattr(target, key, copy.deepcopy(value))


class ConfigManager:
    """Thread-safe configuration persistence and secret access facade."""

    def __init__(self, path: str | os.PathLike[str] | None = None, secret_store: "SecretStore" | None = None):
        self.path = Path(path) if path else default_config_path()
        self.secret_store = secret_store or SecretStore()
        self._lock = threading.RLock()
        self.config = load_config(self.path)
        if io_path(self.path).exists():
            original = json.loads(io_path(self.path).read_text(encoding="utf-8"))
            if int(original.get("version", 1)) < CONFIG_VERSION:
                # Preserve the old bytes for review/rollback before atomic v2 save.
                backup = self.path.with_name(self.path.stem + ".v1.bak.json")
                if not io_path(backup).exists():
                    with io_path(backup).open("xb") as handle:
                        handle.write(io_path(self.path).read_bytes())
                save_config(self.config, self.path)

    def get(self) -> AppConfig:
        with self._lock:
            return AppConfig.from_dict(self.config.to_dict()).ensure_defaults()

    def public_dict(self) -> dict[str, Any]:
        """Return JSON-safe config data; never resolves a secret reference."""
        with self._lock:
            return self.config.to_dict()

    def snapshot(self) -> dict[str, Any]:
        """Freeze this task's settings without resolving or materializing keys."""
        with self._lock:
            return freeze_agent_config(self.config)

    # Compatibility aliases used by the local Web adapter.
    def to_dict(self) -> dict[str, Any]:
        return self.public_dict()

    def validate(self) -> list[str]:
        with self._lock:
            return validate_config(self.config)

    def save(self, expected_revision: int | None = None) -> AppConfig:
        with self._lock:
            if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
                raise ConfigError("expected revision must be a non-negative integer")
            if expected_revision is not None and expected_revision != self.config.revision:
                raise RevisionConflict(f"expected revision {expected_revision}, current is {self.config.revision}")
            candidate = AppConfig.from_dict(self.config.to_dict())
            candidate.revision += 1
            save_config(candidate, self.path)
            self.config = candidate
            return self.get()

    def update_from_dict(self, values: Mapping[str, Any], expected_revision: int | None = None, persist: bool = True) -> AppConfig:
        if not isinstance(values, Mapping):
            raise ConfigError("configuration patch must be an object")
        with self._lock:
            if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
                raise ConfigError("expected revision must be a non-negative integer")
            if expected_revision is not None and expected_revision != self.config.revision:
                raise RevisionConflict(f"expected revision {expected_revision}, current is {self.config.revision}")
            raw = self.config.to_dict()
            patch = copy.deepcopy(dict(values))
            for name, minimum in (("version", 1), ("revision", 0)):
                if name in patch and (type(patch[name]) is not int or patch[name] < minimum):
                    raise ConfigError(f"configuration.{name} must be an integer in its allowed range")
            if patch.get("version", CONFIG_VERSION) > CONFIG_VERSION:
                raise ConfigError("imported configuration version is newer than supported")
            patch.pop("version", None)
            patch.pop("revision", None)
            try:
                reject_plaintext_credentials(patch)
            except ProviderError as exc:
                raise ConfigError(str(exc)) from exc
            _merge_config(raw, patch)
            if isinstance(values.get("llm"), Mapping) and "providers" not in values:
                provider = _legacy_provider(raw)
                if provider:
                    raw["providers"]["legacy-default"] = provider
                    current_route = raw["agent"]["routing"]["default"]
                    if not current_route.get("provider") or current_route["provider"] == "legacy-default":
                        raw["agent"]["routing"]["default"] = {"provider": "legacy-default", "model": provider["models"][0]["id"]}
                        raw["agent"]["enabled"] = bool(provider["enabled"])
            candidate = AppConfig.from_dict(raw)
            candidate.ensure_defaults()
            issues = validate_config(candidate)
            if issues:
                raise ConfigError("; ".join(issues))
            if persist:
                candidate.revision += 1
                save_config(candidate, self.path)
            self.config = candidate
            return self.get()

    def set_secret(self, reference: str, value: str | None) -> None:
        self.secret_store.set(reference, value)

    def get_secret(self, reference: str, default: str | None = None) -> str | None:
        return self.secret_store.get(reference, default=default)


def _merge_config(target: dict[str, Any], patch: Mapping[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            if key == "providers":
                for identifier, provider in value.items():
                    if provider is None:
                        target[key].pop(identifier, None)
                    elif isinstance(provider, Mapping) and identifier in target[key]:
                        _merge_config(target[key][identifier], provider)
                    else:
                        target[key][identifier] = copy.deepcopy(provider)
            else:
                _merge_config(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _blob(data: bytes) -> tuple[Any, Any]:
    array = ctypes.create_string_buffer(data)
    blob = _DATA_BLOB(len(data), ctypes.cast(array, ctypes.POINTER(ctypes.c_char)))
    return blob, array


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_available() -> bool:
    return os.name == "nt"


def _dpapi_protect(data: bytes) -> bytes:
    if not _dpapi_available():
        raise OSError("DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, _ = _blob(data)
    output = _DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if not _dpapi_available():
        raise OSError("DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, _ = _blob(data)
    output = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


class SecretStore:
    """Persistent secret references backed by user DPAPI where available."""

    def __init__(self, root: str | os.PathLike[str] | None = None):
        base = Path(root) if root else app_root()
        self.root = base
        self.path = base / "secrets.json"
        self.key_path = base / "secrets.key"
        self._lock = threading.RLock()
        self._backend = "dpapi" if _dpapi_available() else "memory"
        self._values: dict[str, str] = {}
        self._memory: dict[str, str] = {}
        self._load()

    def __repr__(self) -> str:  # Never accidentally print secret values.
        return f"SecretStore(root={str(self.root)!r}, backend={self._backend!r}, refs={len(self._values)})"

    def _fallback_key(self) -> bytes:
        io_path(self.root).mkdir(parents=True, exist_ok=True)
        if io_path(self.key_path).exists():
            key = io_path(self.key_path).read_bytes()
        else:
            key = secrets.token_bytes(32)
            fd = os.open(io_path(self.key_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, stat.S_IRUSR | stat.S_IWUSR)
            try:
                os.write(fd, key)
            finally:
                os.close(fd)
        if len(key) < 16:
            raise ConfigError("secret key file is invalid")
        return key

    def _fallback_encrypt(self, value: str) -> str:
        key = self._fallback_key()
        nonce = secrets.token_bytes(16)
        raw = value.encode("utf-8")
        stream = bytearray()
        counter = 0
        while len(stream) < len(raw):
            stream.extend(hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest())
            counter += 1
        cipher = bytes(a ^ b for a, b in zip(raw, stream))
        tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
        return base64.b64encode(nonce + cipher + tag).decode("ascii")

    def _fallback_decrypt(self, encoded: str) -> str:
        key = self._fallback_key()
        payload = base64.b64decode(encoded.encode("ascii"), validate=True)
        if len(payload) < 48:
            raise ConfigError("secret payload is invalid")
        nonce, cipher, tag = payload[:16], payload[16:-32], payload[-32:]
        if not hmac.compare_digest(tag, hmac.new(key, nonce + cipher, hashlib.sha256).digest()):
            raise ConfigError("secret payload authentication failed")
        stream = bytearray()
        counter = 0
        while len(stream) < len(cipher):
            stream.extend(hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest())
            counter += 1
        return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")

    def _load(self) -> None:
        if not io_path(self.path).exists():
            return
        try:
            raw = json.loads(io_path(self.path).read_text(encoding="utf-8"))
            backend = str(raw.get("backend", self._backend))
            values = raw.get("values", {})
            if backend not in {"dpapi", "file-key"} or not isinstance(values, dict):
                raise ConfigError("secret store format is unsupported")
            if not _dpapi_available():
                raise ConfigError("persistent Windows secrets require the same Windows user")
            if backend == "file-key":
                # Read the historical encrypted format once, then commit all
                # references with DPAPI. Never silently create another format.
                converted = {ref: base64.b64encode(_dpapi_protect(self._fallback_decrypt(encoded).encode("utf-8"))).decode("ascii") for ref, encoded in values.items()}
                self._backend = "dpapi"
                self._values = converted
                self._persist()
            else:
                self._backend = "dpapi"
                self._values = dict(values)
        except (OSError, ValueError, TypeError) as exc:
            raise ConfigError(f"Unable to read secret store: {self.path}: {exc}") from exc

    def _persist(self) -> None:
        if self._backend != "dpapi":
            return  # Non-Windows development keeps credentials process-local.
        _atomic_write_json(self.path, {"version": 1, "backend": self._backend, "values": self._values})

    def set(self, reference: str, value: str | None) -> None:
        if not isinstance(reference, str) or not SECRET_REF.fullmatch(reference):
            raise ConfigError("secret reference must be a registered identifier")
        with self._lock:
            if value is None or value == "":
                self._values.pop(reference, None)
                self._memory.pop(reference, None)
            else:
                if not isinstance(value, str):
                    raise ConfigError("secret value must be text")
                if _dpapi_available():
                    try:
                        encoded = base64.b64encode(_dpapi_protect(value.encode("utf-8"))).decode("ascii")
                    except Exception as exc:
                        raise ConfigError("Windows DPAPI could not protect the credential; no fallback was written") from exc
                    self._values[reference] = encoded
                else:
                    self._memory[reference] = value
            self._persist()

    def get(self, reference: str, default: str | None = None) -> str | None:
        if not reference:
            return default
        with self._lock:
            if self._backend == "memory":
                return self._memory.get(reference, default)
            encoded = self._values.get(reference)
            if encoded is None:
                return default
            try:
                if self._backend == "dpapi":
                    return _dpapi_unprotect(base64.b64decode(encoded)).decode("utf-8")
                return self._fallback_decrypt(encoded)
            except Exception as exc:
                raise ConfigError(f"Unable to decrypt secret reference {reference!r}: {exc}") from exc

    def delete(self, reference: str) -> None:
        self.set(reference, None)

    def has(self, reference: str) -> bool:
        with self._lock:
            return bool(reference and (reference in self._values or reference in self._memory))

    def references(self) -> list[str]:
        with self._lock:
            return sorted(set(self._values) | set(self._memory))


def canonicalize_path(value: str | os.PathLike[str], *, must_exist: bool = False) -> Path:
    if value is None:
        raise ConfigError("path is required")
    text = os.path.expandvars(os.path.expanduser(os.fspath(value)))
    if not text or "\x00" in text:
        raise ConfigError("path is empty or contains NUL")
    path = Path(text)
    try:
        resolved = record_path(io_path(path).resolve(strict=must_exist))
    except OSError as exc:
        raise ConfigError(f"cannot resolve path {text!r}: {exc}") from exc
    if must_exist and not io_path(resolved).exists():
        raise ConfigError(f"path does not exist: {resolved}")
    return resolved


def path_within(path: str | os.PathLike[str], root: str | os.PathLike[str], *, follow_symlinks: bool = False) -> bool:
    candidate = canonicalize_path(path, must_exist=False) if follow_symlinks else Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    base = canonicalize_path(root, must_exist=False) if follow_symlinks else Path(os.path.abspath(os.path.expanduser(os.fspath(root))))
    if not follow_symlinks:
        # A path that traverses an existing symlink can escape the configured
        # root after creation.  Reject it rather than guessing its target.
        for current in (candidate, *candidate.parents):
            if current == base.parent:
                break
            if io_path(current).exists() and io_path(current).is_symlink():
                return False
        for current in (base, *base.parents):
            if current == base.parent:
                break
            if io_path(current).exists() and io_path(current).is_symlink():
                return False
    try:
        return os.path.commonpath([str(candidate), str(base)]) == str(base)
    except ValueError:
        return False


def validate_path(value: str | os.PathLike[str], *, allowed_roots: list[str] | None = None, must_exist: bool = False, kind: str = "any", follow_symlinks: bool = False) -> Path:
    # Inspect the supplied path before resolve() erases its symlink/junction
    # components. This applies to external corpus paths as well as roots.
    if not follow_symlinks:
        original = Path(os.path.abspath(os.path.expandvars(os.path.expanduser(os.fspath(value)))))
        for component in (original, *original.parents):
            checked = io_path(component)
            if checked.is_symlink() or (hasattr(checked, "is_junction") and checked.is_junction()):
                raise ConfigError(f"symbolic links or junctions are disabled: {component}")
    path = canonicalize_path(value, must_exist=must_exist)
    if kind == "file" and io_path(path).exists() and not io_path(path).is_file():
        raise ConfigError(f"expected a file: {path}")
    if kind == "dir" and io_path(path).exists() and not io_path(path).is_dir():
        raise ConfigError(f"expected a directory: {path}")
    if allowed_roots is not None:
        if not allowed_roots:
            raise ConfigError('allowed roots must not be empty')
        if not any(path_within(path, root, follow_symlinks=follow_symlinks) for root in allowed_roots):
            raise ConfigError(f"path is outside allowed roots: {path}")
    return path


def path_policy(config: AppConfig | Mapping[str, Any]) -> tuple[list[str] | None, bool]:
    """Read the frozen path policy; requested paths never grant permissions."""
    raw = config.to_dict() if isinstance(config, AppConfig) else config
    security = raw.get('security', {})
    roots = None if security.get('allow_external_paths', True) else list(security.get('allowed_roots') or [])
    if roots == []:
        raise ConfigError('security.allowed_roots is required when external paths are disabled')
    return roots, bool(security.get('follow_symlinks', False))


def validate_config_path(config: AppConfig | Mapping[str, Any], value: str | os.PathLike[str], *,
                         field: str, must_exist: bool = False, kind: str = 'any',
                         storage: bool = False) -> Path:
    roots, follow = path_policy(config)
    try:
        # Owned writable storage always rejects link/reparse components.
        # The configured opt-in applies to source material only, whose final
        # resolved target must still lie inside the original allowed roots.
        return validate_path(value, allowed_roots=roots, must_exist=must_exist,
                             kind=kind, follow_symlinks=follow and not storage)
    except (ConfigError, TypeError, ValueError, OSError):
        raise ConfigError(f'{field} violates the path policy, expected type, or existence requirement') from None


def effective_storage_paths(config: AppConfig | Mapping[str, Any], *, include_service: bool = False) -> dict[str, Path]:
    """Validate every effective writable directory before creating any one."""
    raw = config.to_dict() if isinstance(config, AppConfig) else config
    workspace, logging = raw.get('workspace', {}), raw.get('logging', {})
    base = app_root()
    values = {
        'workspace.data_root': workspace.get('data_root') or base/'workspace',
        'workspace.output_root': workspace.get('output_root') or base/'workspace/runs',
        'workspace.temp_root': workspace.get('temp_root') or base/'workspace/tmp',
        'logging.directory': logging.get('directory') or base/'logs',
    }
    if include_service:
        values.update({'service.registry_root': base, 'service.sessions_root': base/'sessions'})
    return {field: validate_config_path(raw, value, field=field, kind='dir', storage=True)
            for field, value in values.items()}


def _configuration_path_issues(config: AppConfig) -> list[str]:
    issues = []
    try:
        effective_storage_paths(config, include_service=True)
    except ConfigError as exc:
        issues.append(str(exc))
    for field, kind in (('draft_path', 'file'), ('corpus_root', 'any'), ('corpus_index', 'file'),
                        ('facts_path', 'file'), ('knowledge_root', 'dir')):
        value = getattr(config.workspace, field)
        if value:
            try:
                validate_config_path(config, value, field='workspace.'+field, kind=kind)
            except ConfigError as exc:
                issues.append(str(exc))
    if config.harness.enabled and config.harness.runtime_mode == 'external':
        for field, kind in (('executable', 'file'), ('home', 'dir'), ('patch_file', 'file'), ('working_dir', 'dir')):
            value = getattr(config.harness, field)
            if value:
                try:
                    validate_config_path(config, value, field='harness.'+field, kind=kind, storage=kind == 'dir')
                except ConfigError as exc:
                    issues.append(str(exc))
    return issues


def validate_server_binding(host: str, server_config: ServerConfig | Mapping[str, Any],
                            secret_resolver: Callable[[str], str | None], *, port: int | None = None) -> None:
    """Validate the effective bind, including launcher overrides, before listening."""
    if not isinstance(server_config, (ServerConfig, Mapping)):
        raise ConfigError("server configuration must be an object")
    raw = asdict(server_config) if isinstance(server_config, ServerConfig) else dict(server_config)
    raw["host"] = host
    if port is not None:
        raw["port"] = port
    issues = _scalar_config_issues({"server": raw})
    if issues:
        raise ConfigError("; ".join(issues))
    if not host or host != host.strip() or any(ord(char) < 33 for char in host) or any(char in host for char in "/\\@?#"):
        raise ConfigError("server.host must be a hostname or IP address")
    selected_port = raw.get("port", 0)
    if type(selected_port) is not int or not 0 <= selected_port <= 65535:
        raise ConfigError("server.port must be between 0 and 65535")
    required = raw.get("bearer_token_required", False)
    if host not in {"127.0.0.1", "localhost", "::1"} and required is not True:
        raise ConfigError("non-loopback binding requires bearer_token_required=true")
    if required:
        reference = raw.get("bearer_token_ref", "server_bearer")
        if not isinstance(reference, str) or not SECRET_REF.fullmatch(reference):
            raise ConfigError("server.bearer_token_ref must be a registered identifier")
        try:
            credential = secret_resolver(reference)
        except Exception:
            raise ConfigError("local service credential is unavailable") from None
        if (not isinstance(credential, str) or not credential or not credential.isascii()
                or any(ord(char) < 33 or ord(char) == 127 for char in credential)):
            raise ConfigError("local service credential must be a non-empty ASCII token without whitespace")


def validate_config(config: AppConfig) -> list[str]:
    """Return validation errors without creating or changing any files."""
    errors = _scalar_config_issues(config.to_dict())
    try:
        _reject_nonfinite(config.to_dict())
    except ConfigError as exc:
        errors.append(str(exc))
    if errors:
        return errors
    if config.version != CONFIG_VERSION:
        errors.append(f"unsupported config version: {config.version}")
    if config.run.mode not in {"kernel", "openai", "agent", "harness", "replay"}:
        errors.append(f"run.mode must be kernel, openai, harness or replay: {config.run.mode}")
    if config.server.port < 0 or config.server.port > 65535:
        errors.append("server.port must be between 0 and 65535")
    for label, value in (("run.timeout_s", config.run.timeout_s), ("run.retries", config.run.retries), ("llm.timeout_s", config.llm.timeout_s), ("harness.timeout_s", config.harness.timeout_s), ("logging.retain_runs", config.logging.retain_runs), ("logging.retain_days", config.logging.retain_days), ("security.max_upload_mb", config.security.max_upload_mb)):
        if value < 0:
            errors.append(f"{label} must be non-negative")
    if not 0 <= config.llm.temperature <= 2:
        errors.append("llm.temperature must be between 0 and 2")
    if config.llm.max_tokens < 0:
        errors.append("llm.max_tokens must be non-negative")
    if config.llm.thinking not in {"auto", "disabled", "enabled"}:
        errors.append("llm.thinking must be auto, disabled or enabled")
    if config.server.host not in {"127.0.0.1", "localhost", "::1"} and config.server.bearer_token_required is not True:
        errors.append("non-loopback server.host requires bearer_token_required=true")
    if config.security.follow_symlinks and not config.security.allowed_roots:
        errors.append("security.allowed_roots is required when follow_symlinks is true")
    if config.llm.enabled and not config.llm.base_url:
        errors.append("llm.base_url is required when llm.enabled is true")
    for label, reference in (("llm.api_key_ref", config.llm.api_key_ref),
                             ("harness.api_key_ref", config.harness.api_key_ref),
                             ("server.bearer_token_ref", config.server.bearer_token_ref)):
        if not SECRET_REF.fullmatch(reference):
            errors.append(f"{label} must be a registered identifier")
    if config.llm.verify_tls is not True:
        errors.append("llm.verify_tls must remain enabled")
    if not isinstance(config.harness.runtime_mode, str) or config.harness.runtime_mode not in {"embedded", "external"}:
        errors.append("harness.runtime_mode must be embedded or external")
    if config.harness.runtime_mode == "external" and config.harness.enabled and not config.harness.executable:
        errors.append("harness.executable is required in enabled external runtime mode")
    errors.extend(validate_providers(config.providers, config.agent))
    errors.extend(_configuration_path_issues(config))
    return errors


def ensure_workspace(config: AppConfig) -> None:
    """Create local application directories, never external corpus directories."""
    paths = effective_storage_paths(config)
    for path in paths.values():
        io_path(path).mkdir(parents=True, exist_ok=True)


def configured_temp_root(config: AppConfig | Mapping[str, Any]) -> Path:
    """Resolve the frozen task's temporary directory under its path policy."""
    raw = config.to_dict() if isinstance(config, AppConfig) else config
    security = raw.get('security', {})
    roots = None if security.get('allow_external_paths', True) else list(security.get('allowed_roots') or [])
    if roots == []:
        raise ConfigError('security.allowed_roots is required for temporary storage')
    value = raw.get('workspace', {}).get('temp_root') or app_root() / 'workspace' / 'tmp'
    path = validate_path(value, allowed_roots=roots, kind='dir', follow_symlinks=False)
    io_path(path).mkdir(parents=True, exist_ok=True)
    return validate_path(path, allowed_roots=roots, must_exist=True, kind='dir', follow_symlinks=False)


__all__ = [
    "AppConfig", "ConfigError", "ConfigManager", "RevisionConflict", "SecretStore",
    "WorkspaceConfig", "RunConfig", "LLMConfig", "HarnessConfig", "ServerConfig",
    "LoggingConfig", "SecurityConfig", "app_root", "default_config_path", "load_config",
    "save_config", "validate_config", "validate_server_binding", "canonicalize_path", "validate_path", "path_within",
    "path_policy", "validate_config_path", "effective_storage_paths",
    "ensure_workspace", "configured_temp_root", "migrate_config", "ProviderConfig", "ModelConfig", "AgentConfig", "RouteConfig",
]
