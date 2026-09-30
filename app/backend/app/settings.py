"""Typed, bounded settings for the prototype (local and hosted).

Settings are read only when ``load_settings`` is called. Importing this module
does not inspect environment variables, load dotenv files, or make network calls.
``load_local_env`` is the one explicit way a local run picks up ``backend/.env``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_timeout_seconds: Annotated[int, Field(strict=True, gt=0, le=30)] = 30
    max_query_chars: Annotated[int, Field(strict=True, gt=0, le=4000)] = 4000

    source_timeout_seconds: Annotated[int, Field(strict=True, gt=0, le=60)] = 60
    source_max_pages: Annotated[int, Field(strict=True, gt=0, le=4)] = 4
    source_page_size: Annotated[int, Field(strict=True, gt=0, le=5000)] = 5000
    source_max_bytes: Annotated[int, Field(strict=True, gt=0, le=10 * 1024 * 1024)] = 10 * 1024 * 1024

    model_timeout_seconds: Annotated[int, Field(strict=True, gt=0, le=20)] = 20
    model_max_output_tokens: Annotated[int, Field(strict=True, gt=0, le=512)] = 512
    model_max_prompt_tokens: Annotated[int, Field(strict=True, gt=0, le=8000)] = 8000

    # Candidate evaluation may use model_access_available (a key is set). Runtime
    # free text additionally requires the separate, exact admission record below;
    # a key alone never enables chat. Spend is capped by the Google project's
    # quota and budget, not by process-local accounting.
    model_api_key: SecretStr | None = None
    model_name: Annotated[str, Field(min_length=1, max_length=100)] = DEFAULT_GEMINI_MODEL
    # Gemini thinking tokens count against the output cap; 0 turns thinking off on
    # Flash models. None omits thinkingConfig for models that reject a budget.
    model_thinking_budget: Annotated[int, Field(strict=True, ge=0, le=24576)] | None = 0
    model_runtime_enabled: Annotated[bool, Field(strict=True)] = False
    model_admitted_name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    model_admitted_adapter_sha256: Annotated[str, Field(min_length=64, max_length=64)] | None = None

    @field_validator("model_api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            # Pydantic wraps ValueError as ValidationError; TypeError escapes validation.
            raise ValueError("model API key must be text")  # noqa: TRY004
        value = value.strip()
        if not value:
            return None
        if len(value) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("model API key is malformed")
        return value

    @field_validator("model_name", "model_admitted_name")
    @classmethod
    def validate_model_name(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", value):
            raise ValueError("model name is malformed")
        return value

    @field_validator("model_admitted_adapter_sha256")
    @classmethod
    def validate_admitted_adapter_hash(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("admitted adapter hash must be lowercase SHA-256 hex")
        return value

    @property
    def model_access_available(self) -> bool:
        return self.model_api_key is not None

    def model_runtime_admitted(self, adapter_hash: str) -> bool:
        """Fail closed unless the exact model and adapter are admitted."""
        return (
            self.model_runtime_enabled
            and self.model_access_available
            and self.model_admitted_name == self.model_name
            and self.model_admitted_adapter_sha256 is not None
            and self.model_admitted_adapter_sha256 == adapter_hash
        )


_ENV_FIELDS = {
    "QUERY_TIMEOUT_SECONDS": "query_timeout_seconds",
    "MAX_QUERY_CHARS": "max_query_chars",
    "SOURCE_TIMEOUT_SECONDS": "source_timeout_seconds",
    "SOURCE_MAX_PAGES": "source_max_pages",
    "SOURCE_PAGE_SIZE": "source_page_size",
    "SOURCE_MAX_BYTES": "source_max_bytes",
    "MODEL_TIMEOUT_SECONDS": "model_timeout_seconds",
    "MODEL_MAX_OUTPUT_TOKENS": "model_max_output_tokens",
    "MODEL_MAX_PROMPT_TOKENS": "model_max_prompt_tokens",
    "GEMINI_API_KEY": "model_api_key",
    "GEMINI_MODEL": "model_name",
    "GEMINI_THINKING_BUDGET": "model_thinking_budget",
    "MODEL_RUNTIME_ENABLED": "model_runtime_enabled",
    "MODEL_ADMITTED_NAME": "model_admitted_name",
    "MODEL_ADMITTED_ADAPTER_SHA256": "model_admitted_adapter_sha256",
}

_INTEGER_FIELDS = {
    "query_timeout_seconds", "max_query_chars", "source_timeout_seconds",
    "source_max_pages", "source_page_size", "source_max_bytes",
    "model_timeout_seconds", "model_max_output_tokens", "model_max_prompt_tokens",
}
LOCAL_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def load_local_env(path: Path = LOCAL_ENV_FILE, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Copy KEY=VALUE lines from ``backend/.env`` into the environment for a local run.

    Variables already set in the environment win, and empty values are skipped.
    Nothing is loaded when APP_NO_DOTENV is set (the test suite sets it) or on Vercel, where settings come from the project's encrypted
    environment variables (.vercelignore also keeps the file out of the upload).
    Returns the names loaded, never their values.
    """
    target = os.environ if environ is None else environ
    if target.get("VERCEL", "").strip() or target.get("APP_NO_DOTENV") or not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if match is None:
            continue
        name, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if value and name not in target:
            target[name] = value
            loaded.append(name)
    return loaded


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Validate supported environment overrides without reading dotenv files."""
    source = os.environ if environ is None else environ
    values = {field: source[variable] for variable, field in _ENV_FIELDS.items() if variable in source}
    if "model_name" in values and not str(values["model_name"]).strip():
        del values["model_name"]
    try:
        for field in _INTEGER_FIELDS & values.keys():
            values[field] = int(values[field])
        if "model_thinking_budget" in values:
            budget = str(values["model_thinking_budget"]).strip().lower()
            values["model_thinking_budget"] = None if budget in {"", "default"} else int(budget)
        if "model_runtime_enabled" in values:
            enabled = values["model_runtime_enabled"]
            if not isinstance(enabled, str) or enabled.lower() not in {"true", "false"}:
                raise ValueError("MODEL_RUNTIME_ENABLED must be true or false")
            values["model_runtime_enabled"] = enabled.lower() == "true"
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric setting is malformed") from exc
    return Settings.model_validate(values)


MIN_SIGNING_KEY_BYTES = 32
MAX_CONCURRENT_QUERIES_LIMIT = 16
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
# Vercel system variables naming this deployment's own hostnames [RE-VERIFY at deploy].
_VERCEL_HOST_VARIABLES = ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL")


class HostingConfigError(ValueError):
    """Hosted deployment settings are missing, weak, or malformed.

    Messages name the variable but never include its value.
    """


class HostingConfig(BaseModel):
    """Host, signing-key and concurrency settings, validated separately from model settings.

    Hosted mode applies when running on Vercel or when ``ALLOWED_HOSTS`` is set;
    it requires ``APP_SIGNING_KEY`` (which signs the follow-up context cookie).
    A plain loopback run with neither uses a random per-process key. Model
    misconfiguration never disables presets; hosting misconfiguration closes
    everything except ``/health``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    hosted: bool
    on_vercel: bool
    signing_key: SecretStr | None
    allowed_hosts: frozenset[str]
    max_concurrent_queries: Annotated[int, Field(strict=True, ge=1, le=MAX_CONCURRENT_QUERIES_LIMIT)]

    @property
    def secure_cookies(self) -> bool:
        return self.on_vercel


def _host_entry(value: str, variable: str) -> str:
    host = value.strip().lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    if not _HOSTNAME.fullmatch(host):
        raise HostingConfigError(f"{variable} contains an invalid hostname")
    return host


def load_hosting(environ: Mapping[str, str] | None = None) -> HostingConfig:
    """Validate hosting settings; raise HostingConfigError when hosted mode is unsafe."""
    source = os.environ if environ is None else environ
    on_vercel = bool(source.get("VERCEL", "").strip())
    raw_hosts = source.get("ALLOWED_HOSTS", "")
    signing_key = source.get("APP_SIGNING_KEY", "") or None
    hosts = {_host_entry(item, "ALLOWED_HOSTS") for item in raw_hosts.split(",") if item.strip()}
    if on_vercel:
        hosts |= {
            _host_entry(source[variable], variable)
            for variable in _VERCEL_HOST_VARIABLES
            if source.get(variable, "").strip()
        }
    hosted = on_vercel or bool(raw_hosts.strip())

    if signing_key is not None and not MIN_SIGNING_KEY_BYTES <= len(signing_key.encode("utf-8")) <= 512:
        raise HostingConfigError("APP_SIGNING_KEY must be 32-512 bytes")
    if hosted and signing_key is None:
        raise HostingConfigError("APP_SIGNING_KEY is required in hosted mode")

    raw_limit = source.get("MAX_CONCURRENT_QUERIES", "").strip()
    try:
        limit = int(raw_limit) if raw_limit else (4 if hosted else 1)
    except ValueError as exc:
        raise HostingConfigError("MAX_CONCURRENT_QUERIES must be an integer") from exc
    if not 1 <= limit <= MAX_CONCURRENT_QUERIES_LIMIT:
        raise HostingConfigError("MAX_CONCURRENT_QUERIES must be between 1 and 16")
    return HostingConfig(
        hosted=hosted, on_vercel=on_vercel, signing_key=signing_key,
        allowed_hosts=frozenset(hosts), max_concurrent_queries=limit,
    )
