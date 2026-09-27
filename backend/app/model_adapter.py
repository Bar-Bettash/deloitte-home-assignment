"""One bounded Responses API call for interpreting an airport question.

This module has no credential lookup or network activity at import time. Callers
own admission, monetary budgets, session validation, and result dispatch.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import httpx
from app.contracts import AIRPORTS, AnalysisRequest
from app.settings import Settings
from pydantic import ValidationError

RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_RESPONSE_BYTES = 64 * 1024

SYSTEM_PROMPT = """Interpret one user question for a constrained airport-analysis application.
Return only one structured outcome. Choose analysis only when the user's request
unambiguously maps to the supplied analysis fields and supported periods. Never
invent an airport, year, metric, threshold, source result, or business quantity.
Ask for clarification when the request is ambiguous. Mark unsupported business
claims, arbitrary data access, and unqualified periods unsupported. One analysis
per question. A follow-up may use only the validated previous analysis supplied
in context; if it needs prior context and none is supplied, ask for clarification.
The server independently validates every analysis and supplies all user-visible
safe-outcome wording. The message field must be null."""
PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()

_METRICS = [
    "passengers", "seats", "departures", "passenger_growth", "seat_occupancy",
    "long_haul_share", "screen_score", "congestion", "cancellation_rate",
    "diversion_rate", "departure_delay_minutes", "taxi_out_minutes",
    "sfo_enplaned_trend", "sfo_pressure",
]
_ANALYSIS_FIELDS = {
    "action": {"type": "string", "enum": ["rank", "compare", "metric", "explain"]},
    "airports": {"type": ["array", "null"], "items": {"type": "string", "enum": sorted(AIRPORTS)}},
    "region": {"type": ["string", "null"], "enum": ["new_england", None]},
    "metric": {"type": ["string", "null"], "enum": [*_METRICS, None]},
    "year": {"type": ["integer", "null"]},
    "threshold_miles": {"type": ["number", "null"]},
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["analysis", "clarification_required", "unsupported_scope"]},
        "analysis": {
            "type": ["object", "null"],
            "properties": _ANALYSIS_FIELDS,
            "required": list(_ANALYSIS_FIELDS),
            "additionalProperties": False,
        },
        "message": {"type": "null"},
    },
    "required": ["kind", "analysis", "message"],
    "additionalProperties": False,
}

_SAFE_MESSAGES = {
    "clarification_required": "Please name the airport, metric, and supported period you want to analyze.",
    "unsupported_scope": "That question is outside the supported airport analyses and periods.",
}


class ModelAdapterError(Exception):
    """A sanitized failure code; provider text and transport details are discarded."""

    def __init__(self, code: Literal[
        "ai_unavailable", "model_timeout", "model_incomplete", "model_refusal",
        "model_invalid_response", "model_prompt_too_large",
    ]) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ModelInterpretation:
    kind: Literal["analysis", "clarification_required", "unsupported_scope"]
    analysis: AnalysisRequest | None
    message: str | None
    usage: ModelUsage

    def as_outcome(self) -> dict[str, object]:
        if self.analysis is not None:
            return {"kind": "analysis", "analysis": self.analysis.model_dump(mode="json", exclude_none=True)}
        return {"kind": self.kind, "message": self.message}


def _make_input(message: str, context: Mapping[str, object] | None, settings: Settings) -> str:
    if not isinstance(message, str) or not message.strip() or len(message) > settings.max_query_chars:
        raise ModelAdapterError("model_prompt_too_large")
    previous = None
    if context is not None:
        try:
            previous = AnalysisRequest.model_validate(context).model_dump(mode="json", exclude_none=True)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ModelAdapterError("model_invalid_response") from exc
    payload = json.dumps({"question": message, "previous_analysis": previous}, ensure_ascii=False, separators=(",", ":"))
    # UTF-8 bytes give a conservative token upper bound without a tokenizer.
    if len(SYSTEM_PROMPT.encode("utf-8")) + len(payload.encode("utf-8")) > settings.model_max_prompt_tokens:
        raise ModelAdapterError("model_prompt_too_large")
    return payload


def _parse_usage(body: dict[str, object], settings: Settings) -> ModelUsage:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        raise ModelAdapterError("model_invalid_response")
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if (type(input_tokens) is not int or type(output_tokens) is not int
            or input_tokens < 0 or input_tokens > settings.model_max_prompt_tokens
            or output_tokens < 0 or output_tokens > settings.model_max_output_tokens):
        raise ModelAdapterError("model_invalid_response")
    return ModelUsage(input_tokens, output_tokens)


def _parse_response(body: object, settings: Settings) -> ModelInterpretation:
    if not isinstance(body, dict):
        raise ModelAdapterError("model_invalid_response")
    if body.get("status") != "completed":
        raise ModelAdapterError("model_incomplete")
    usage = _parse_usage(body, settings)
    output = body.get("output")
    if not isinstance(output, list):
        raise ModelAdapterError("model_invalid_response")
    messages = [item for item in output if isinstance(item, dict) and item.get("type") == "message"]
    if len(messages) != 1 or any(not isinstance(item, dict) or item.get("type") not in {"message", "reasoning"} for item in output):
        raise ModelAdapterError("model_invalid_response")
    message = messages[0]
    if message.get("status") != "completed" or message.get("role", "assistant") != "assistant":
        raise ModelAdapterError("model_invalid_response")
    content = message.get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        raise ModelAdapterError("model_invalid_response")
    part = content[0]
    if part.get("type") == "refusal":
        raise ModelAdapterError("model_refusal")
    if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
        raise ModelAdapterError("model_invalid_response")
    try:
        parsed = json.loads(part["text"])
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ModelAdapterError("model_invalid_response") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"kind", "analysis", "message"} or parsed["message"] is not None:
        raise ModelAdapterError("model_invalid_response")
    kind = parsed["kind"]
    if kind == "analysis":
        if not isinstance(parsed["analysis"], dict) or set(parsed["analysis"]) != set(_ANALYSIS_FIELDS):
            raise ModelAdapterError("model_invalid_response")
        try:
            analysis = AnalysisRequest.model_validate(parsed["analysis"])
        except ValidationError as exc:
            raise ModelAdapterError("model_invalid_response") from exc
        return ModelInterpretation("analysis", analysis, None, usage)
    if kind in _SAFE_MESSAGES and parsed["analysis"] is None:
        return ModelInterpretation(kind, None, _SAFE_MESSAGES[kind], usage)
    raise ModelAdapterError("model_invalid_response")


async def interpret_message(
    message: str,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    context: Mapping[str, object] | None = None,
) -> ModelInterpretation:
    """Make exactly one request. An injected client supports offline MockTransport tests."""
    if not settings.model_access_available:
        raise ModelAdapterError("ai_unavailable")
    user_input = _make_input(message, context, settings)
    request_json = {
        "model": settings.model_name,
        "input": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_input}],
        "text": {"format": {"type": "json_schema", "name": "airport_intent", "strict": True, "schema": OUTPUT_SCHEMA}},
        "store": False,
        "max_output_tokens": settings.model_max_output_tokens,
    }
    headers = {"Authorization": f"Bearer {settings.model_api_key.get_secret_value()}", "Content-Type": "application/json"}

    async def call(http: httpx.AsyncClient) -> object:
        async with http.stream("POST", RESPONSES_URL, json=request_json, headers=headers) as response:
            if response.status_code != 200:
                raise ModelAdapterError("ai_unavailable")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ModelAdapterError("model_invalid_response")
                data.extend(chunk)
        try:
            return json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelAdapterError("model_invalid_response") from exc

    try:
        async with asyncio.timeout(settings.model_timeout_seconds):
            if client is None:
                async with httpx.AsyncClient(timeout=settings.model_timeout_seconds, follow_redirects=False, trust_env=False) as owned:
                    body = await call(owned)
            else:
                body = await call(client)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ModelAdapterError("model_timeout") from exc
    except httpx.HTTPError as exc:
        raise ModelAdapterError("ai_unavailable") from exc
    return _parse_response(body, settings)
