"""Typed, bounded settings for the local prototype.

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
    model_request_budget_usd: Annotated[float, Field(strict=True, ge=0, le=0.02, allow_inf_nan=False)] = 0.02
    model_process_budget_usd: Annotated[float, Field(strict=True, ge=0, le=2.0, allow_inf_nan=False)] = 2.0

    model_api_key: SecretStr | None = None
    model_name: Annotated[str, Field(min_length=1, max_length=100)] | None = None

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

    @field_validator("model_name")
    @classmethod
    def validate_model_name(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", value):
            raise ValueError("model name is malformed")
        return value

    @property
    def model_access_available(self) -> bool:
        return self.model_api_key is not None and self.model_name is not None


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
    "MODEL_REQUEST_BUDGET_USD": "model_request_budget_usd",
    "MODEL_PROCESS_BUDGET_USD": "model_process_budget_usd",
    "OPENAI_API_KEY": "model_api_key",
    "OPENAI_MODEL": "model_name",
}

_INTEGER_FIELDS = {
    "query_timeout_seconds", "max_query_chars", "source_timeout_seconds",
    "source_max_pages", "source_page_size", "source_max_bytes",
    "model_timeout_seconds", "model_max_output_tokens", "model_max_prompt_tokens",
}
_FLOAT_FIELDS = {"model_request_budget_usd", "model_process_budget_usd"}


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Validate supported environment overrides without reading dotenv files."""
    source = os.environ if environ is None else environ
    values = {field: source[variable] for variable, field in _ENV_FIELDS.items() if variable in source}
    try:
        for field in _INTEGER_FIELDS & values.keys():
            values[field] = int(values[field])
        for field in _FLOAT_FIELDS & values.keys():
            values[field] = float(values[field])
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric setting is malformed") from exc
    return Settings.model_validate(values)
