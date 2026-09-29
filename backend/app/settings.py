"""Typed, bounded settings for the prototype (local and hosted).

Settings are read only when ``load_settings`` is called. Importing this module
does not inspect environment variables, load dotenv files, or make network calls.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


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

    model_api_key: SecretStr | None = None
    model_name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    # Candidate evaluation may use model_access_available. Runtime calls require
    # this separate, exact admission record. Spend is capped by the provider
    # project's hard monthly budget, not by process-local accounting.
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

    @property
    def model_access_available(self) -> bool:
        return self.model_api_key is not None and self.model_name is not None

    @field_validator("model_admitted_adapter_sha256")
    @classmethod
    def validate_admitted_adapter_hash(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("admitted adapter hash must be lowercase SHA-256 hex")
        return value

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
    "OPENAI_API_KEY": "model_api_key",
    "OPENAI_MODEL": "model_name",
    "MODEL_RUNTIME_ENABLED": "model_runtime_enabled",
    "MODEL_ADMITTED_NAME": "model_admitted_name",
    "MODEL_ADMITTED_ADAPTER_SHA256": "model_admitted_adapter_sha256",
}

_INTEGER_FIELDS = {
    "query_timeout_seconds", "max_query_chars", "source_timeout_seconds",
    "source_max_pages", "source_page_size", "source_max_bytes",
    "model_timeout_seconds", "model_max_output_tokens", "model_max_prompt_tokens",
}


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Validate supported environment overrides without reading dotenv files."""
    source = os.environ if environ is None else environ
    values = {field: source[variable] for variable, field in _ENV_FIELDS.items() if variable in source}
    try:
        for field in _INTEGER_FIELDS & values.keys():
            values[field] = int(values[field])
        if "model_runtime_enabled" in values:
            enabled = values["model_runtime_enabled"]
            if not isinstance(enabled, str) or enabled.lower() not in {"true", "false"}:
                raise ValueError("MODEL_RUNTIME_ENABLED must be true or false")
            values["model_runtime_enabled"] = enabled.lower() == "true"
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric setting is malformed") from exc
    return Settings.model_validate(values)


MIN_ACCESS_CODE_CHARS = 20
MIN_SIGNING_KEY_BYTES = 32
MAX_CONCURRENT_QUERIES_LIMIT = 16
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
# Vercel system variables naming this deployment's own hostnames [RE-VERIFY at deploy].
_VERCEL_HOST_VARIABLES = ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL")


class HostingConfigError(ValueError):
    """Gated deployment settings are missing, weak, or malformed.

    Messages name the variable but never include its value.
    """


class HostingConfig(BaseModel):
    """Access-gate, host and concurrency settings, validated separately from model settings.

    Gated mode applies when running on Vercel or when ``ALLOWED_HOSTS`` or
    ``ACCESS_CODE`` is set. Only a plain loopback run with none of them keeps the
    access gate off. Model misconfiguration never disables presets; hosting
    misconfiguration closes everything except ``/health``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    gated: bool
    on_vercel: bool
    access_code: SecretStr | None
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
    """Validate gate settings; raise HostingConfigError when gated mode is unsafe."""
    source = os.environ if environ is None else environ
    on_vercel = bool(source.get("VERCEL", "").strip())
    raw_hosts = source.get("ALLOWED_HOSTS", "")
    access_code = source.get("ACCESS_CODE", "") or None
    signing_key = source.get("APP_SIGNING_KEY", "") or None
    hosts = {_host_entry(item, "ALLOWED_HOSTS") for item in raw_hosts.split(",") if item.strip()}
    if on_vercel:
        hosts |= {
            _host_entry(source[variable], variable)
            for variable in _VERCEL_HOST_VARIABLES
            if source.get(variable, "").strip()
        }
    gated = on_vercel or bool(raw_hosts.strip()) or access_code is not None

    if access_code is not None and (
        len(access_code) < MIN_ACCESS_CODE_CHARS
        or len(access_code) > 256
        or any(ord(char) < 33 or ord(char) == 127 for char in access_code)
    ):
        raise HostingConfigError("ACCESS_CODE must be 20-256 printable characters without spaces")
    if signing_key is not None and not MIN_SIGNING_KEY_BYTES <= len(signing_key.encode("utf-8")) <= 512:
        raise HostingConfigError("APP_SIGNING_KEY must be 32-512 bytes")
    if gated and access_code is None:
        raise HostingConfigError("ACCESS_CODE is required in gated mode")
    if gated and signing_key is None:
        raise HostingConfigError("APP_SIGNING_KEY is required in gated mode")

    raw_limit = source.get("MAX_CONCURRENT_QUERIES", "").strip()
    try:
        limit = int(raw_limit) if raw_limit else (4 if gated else 1)
    except ValueError as exc:
        raise HostingConfigError("MAX_CONCURRENT_QUERIES must be an integer") from exc
    if not 1 <= limit <= MAX_CONCURRENT_QUERIES_LIMIT:
        raise HostingConfigError("MAX_CONCURRENT_QUERIES must be between 1 and 16")
    return HostingConfig(
        gated=gated, on_vercel=on_vercel, access_code=access_code, signing_key=signing_key,
        allowed_hosts=frozenset(hosts), max_concurrent_queries=limit,
    )
