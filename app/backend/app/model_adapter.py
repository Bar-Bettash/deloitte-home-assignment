"""One bounded Gemini generateContent call for interpreting an airport question.

This module has no credential lookup or network activity at import time. Callers
own context validation, logging, and result dispatch. Spend is capped by the
Google project's quota and budget, not by this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from app.contracts import AIRPORTS, AnalysisRequest
from app.settings import Settings
from pydantic import ValidationError

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_RESPONSE_BYTES = 64 * 1024

SYSTEM_PROMPT = """Interpret one user question for a constrained airport-analysis application.
Return only one structured outcome. Choose analysis only when the user's request
unambiguously maps to the supplied analysis fields and supported periods. Never
invent an airport, year, metric, threshold, source result, or business quantity.
Ask for clarification when the request is ambiguous. Mark unsupported business
claims, arbitrary data access, and unqualified periods unsupported.

What the analyses answer. These are the application's core questions, not
unsupported business claims; map the user's intent to the analysis that measures
it:
- Which New England airports are candidates for terminal or capacity expansion,
  or deserve investment attention first: rank, region new_england, metric
  screen_score (a traffic-pressure screen of growth, volume and seat occupancy).
- Congestion or operational strain at two named airports: compare with
  congestion, or with one operational metric when the user names it.
- The share of long-haul or long-distance traffic at an airport: metric with
  long_haul_share.
- Unmet, excess or pent-up passenger demand, or demand pressure, at SFO: metric
  with sfo_pressure.
Still unsupported: anything the data cannot identify, such as profitability,
return on investment, construction cost, or demand forecasts for future years.

Analysis field rules. Every analysis field is always present; set each field the
action does not use to null.
- rank: region new_england or a list of New England airports, one metric. Ranking
  is limited to New England airports; a ranking of any other airport is
  unsupported_scope.
- compare: exactly two airports and one metric; region null.
- metric: exactly one airport and one metric; region null.
- explain: explains the previous result. Set action to explain and every other
  field to null.
- threshold_miles is used only with long_haul_share; otherwise null.

Scope rules.
- One analysis per message. A message that asks for several analyses at once
  (for example two metrics, or two separate questions) is unsupported_scope; the
  user must submit one analysis at a time.
- When a place could mean more than one supported airport, or names no specific
  supported airport, return clarification_required. Never guess an airport.

Follow-ups. A follow-up may use only the validated previous analysis supplied in
context; if it needs prior context and none is supplied, ask for clarification.
A follow-up keeps the previous analysis's airports, action and year unless the
user changes them. A follow-up that only narrows or switches the metric (for
example to one operational measure) after a comparison stays a comparison of the
same two airports.

Periods. Supported periods are calendar years 2023, 2024 and 2025. If the user
names no year, set year to null: the server then uses the newest accepted period
(2025, compared with 2024). Growth and screen scores need a comparison year (2024
or 2025); operational and SFO metrics support 2024 and 2025. Any other year is
unsupported. The server independently validates every analysis and supplies all
user-visible wording. When kind is not analysis, set analysis to null."""
# Recorded in evaluation reports so a result names the exact prompt and adapter.
PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
ADAPTER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

_METRICS = [
    "passengers", "seats", "departures", "passenger_growth", "seat_occupancy",
    "long_haul_share", "screen_score", "congestion", "cancellation_rate",
    "diversion_rate", "departure_delay_minutes", "taxi_out_minutes",
    "sfo_enplaned_trend", "sfo_pressure",
]


def _nullable(schema: dict[str, object]) -> dict[str, object]:
    return {"anyOf": [schema, {"type": "null"}]}


# JSON Schema for generationConfig.responseFormat.text.schema (responseSchema is
# deprecated). The analysis object is fixed-shape: every field is required and
# unused ones are null, with no extra properties. The contract (AnalysisRequest)
# stays the authoritative validator and re-checks everything the model returns.
_ANALYSIS_FIELDS: dict[str, dict[str, object]] = {
    "action": {"type": "string", "enum": ["rank", "compare", "metric", "explain"]},
    "airports": _nullable({"type": "array", "items": {"type": "string", "enum": sorted(AIRPORTS)}}),
    "region": _nullable({"type": "string", "enum": ["new_england"]}),
    "metric": _nullable({"type": "string", "enum": _METRICS}),
    "year": {"type": ["integer", "null"]},
    "threshold_miles": {"type": ["number", "null"]},
}
_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": _ANALYSIS_FIELDS,
    "required": list(_ANALYSIS_FIELDS),
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["analysis", "clarification_required", "unsupported_scope"]},
        "analysis": _nullable(_ANALYSIS_SCHEMA),
    },
    "required": ["kind", "analysis"],
    "additionalProperties": False,
}
_REFUSAL_FINISH = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"})

_SAFE_MESSAGES = {
    "clarification_required": "Please name the airport, metric, and supported period you want to analyze.",
    "unsupported_scope": "That question is outside the supported airport analyses and periods.",
}


class ModelAdapterError(Exception):
    """A sanitized failure code; provider text and transport details are discarded."""

    def __init__(self, code: Literal[
        "ai_unavailable", "model_timeout", "model_incomplete", "model_refusal",
        "model_invalid_response", "model_prompt_too_large",
    ], provider_status: int | None = None, usage: ModelUsage | None = None) -> None:
        self.code = code
        # HTTP status from the provider, for the server log only (never the client).
        self.provider_status = provider_status
        # Token usage of a call that succeeded over HTTP but whose output was
        # rejected; evaluation uses it to cost billed-but-rejected calls.
        self.usage = usage
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


def _request_json(settings: Settings, user_input: str) -> dict[str, object]:
    generation: dict[str, object] = {
        "responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": OUTPUT_SCHEMA}},
        "maxOutputTokens": settings.model_max_output_tokens,
        "temperature": 0,
    }
    # Thinking tokens count against maxOutputTokens; a zero budget keeps Flash
    # models inside the cap. Omitted when unset so other models use their default.
    if settings.model_thinking_budget is not None:
        generation["thinkingConfig"] = {"thinkingBudget": settings.model_thinking_budget}
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_input}]}],
        "generationConfig": generation,
    }


def _count(value: object) -> int:
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise ModelAdapterError("model_invalid_response")
    return value


def _parse_usage(body: dict[str, object]) -> ModelUsage:
    usage = body.get("usageMetadata")
    if usage is None:
        return ModelUsage(0, 0)
    if not isinstance(usage, dict):
        raise ModelAdapterError("model_invalid_response")
    return ModelUsage(
        _count(usage.get("promptTokenCount")),
        _count(usage.get("candidatesTokenCount")) + _count(usage.get("thoughtsTokenCount")),
    )


def _response_text(body: dict[str, object]) -> str:
    feedback = body.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise ModelAdapterError("model_refusal")
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise ModelAdapterError("model_invalid_response")
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish in _REFUSAL_FINISH:
        raise ModelAdapterError("model_refusal")
    if finish == "MAX_TOKENS":
        raise ModelAdapterError("model_incomplete")
    if finish != "STOP":
        raise ModelAdapterError("model_invalid_response")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise ModelAdapterError("model_invalid_response")
    texts = [part["text"] for part in parts
             if isinstance(part, dict) and not part.get("thought") and isinstance(part.get("text"), str)]
    if not texts:
        raise ModelAdapterError("model_invalid_response")
    return "".join(texts)


def _parse_response(body: object) -> ModelInterpretation:
    if not isinstance(body, dict):
        raise ModelAdapterError("model_invalid_response")
    usage = _parse_usage(body)
    try:
        return _interpret(body, usage)
    except ModelAdapterError as exc:
        exc.usage = usage
        raise


def _interpret(body: dict[str, object], usage: ModelUsage) -> ModelInterpretation:
    try:
        parsed = json.loads(_response_text(body))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ModelAdapterError("model_invalid_response") from exc
    if not isinstance(parsed, dict) or "kind" not in parsed or not set(parsed) <= {"kind", "analysis"}:
        raise ModelAdapterError("model_invalid_response")
    kind, proposed = parsed["kind"], parsed.get("analysis")
    if kind == "analysis":
        if not isinstance(proposed, dict) or not set(proposed) <= set(_ANALYSIS_FIELDS):
            raise ModelAdapterError("model_invalid_response")
        try:
            analysis = AnalysisRequest.model_validate({field: proposed.get(field) for field in _ANALYSIS_FIELDS})
        except ValidationError as exc:
            raise ModelAdapterError("model_invalid_response") from exc
        return ModelInterpretation("analysis", analysis, None, usage)
    # A nullable object may come back as {} or with every field null instead of null.
    empty = proposed is None or (isinstance(proposed, dict) and all(value is None for value in proposed.values()))
    if kind in _SAFE_MESSAGES and empty:
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
    request_json = _request_json(settings, user_input)
    headers = {"x-goog-api-key": settings.model_api_key.get_secret_value(), "Content-Type": "application/json"}
    url = GEMINI_URL.format(model=quote(settings.model_name, safe=""))

    async def call(http: httpx.AsyncClient) -> object:
        async with http.stream("POST", url, json=request_json, headers=headers) as response:
            if response.status_code != 200:
                raise ModelAdapterError("ai_unavailable", provider_status=response.status_code)
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
    return _parse_response(body)
