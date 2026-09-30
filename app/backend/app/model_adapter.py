"""One bounded Gemini generateContent call for interpreting an airport question.

This module has no credential lookup or network activity at import time. Callers
own context validation, logging, and result dispatch. Spend is capped by the
Google project's quota and budget, not by this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
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
Return only one structured outcome. Apply the rules below directly; the choice is
a short classification, not an essay. Be liberal in reading the user's wording
(casual phrasing, full airport or city names, investment language) and
conservative in the analysis you choose. Never invent an airport, year, metric,
threshold, source result, or business quantity.

Choose the outcome in this order:
1. unsupported_scope only when the analysis itself is outside the product:
   profitability, return on investment, cost or how much to invest, valuation or
   what an airport is worth, forecasts of future years, rankings outside New
   England, a named airport that is not a supported airport code, arbitrary data
   access such as running SQL or reading files, several analyses in one message,
   or an unqualified period.
2. clarification_required when one missing or ambiguous detail (which airport,
   which second airport, which metric, or prior context that was not supplied)
   would make the question supported.
3. Otherwise analysis. A question that asks for a supported analysis and also
   asks what it means (whether it is a good sign or an investment signal) is that
   one analysis: the application reports the measured result with its caveats.

What the analyses answer. These are the application's core questions, not
unsupported business claims; map the user's intent to the analysis that measures
it:
- Terminal or capacity expansion, expansion candidates, investment attention or
  opportunity, growth potential, capacity pressure, unmet demand or undersupply,
  running out of room, or whether an airport is worth investigating, for New
  England airports: rank, region new_england, metric screen_score (a
  traffic-pressure screen of growth, volume and seat occupancy). The screen is
  relative, so a screening question about one New England airport also ranks
  region new_england. The screen covers New England only, so a screening
  question that names no region or airport also ranks region new_england.
- Congestion or operational strain at two named airports: compare with
  congestion, or with one operational metric when the user names it.
- The share of long-haul or long-distance traffic at an airport, or its appeal
  for long-haul service: metric with long_haul_share.
- Unmet, excess or pent-up passenger demand, undersupply, demand pressure, or
  whether it needs more capacity, at SFO: metric with sfo_pressure.
- Passenger volume, passenger growth, seats, departures or seat occupancy: that
  metric; a passenger trend at SFO is sfo_enplaned_trend.
Still unsupported: anything the data cannot identify, such as profitability,
return on investment, construction cost, investment amounts, valuation, or
demand forecasts for future years.

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
- When a place could mean more than one supported airport, or the question names
  no specific airport, return clarification_required. Never guess an airport.

Previous analysis. previous_analysis, when supplied, is the result on screen.
- A new question names its own airport or airports and its own metric or topic.
  It replaces the previous analysis: take nothing from it, and leave year null
  unless the user names one.
- A follow-up leaves something implicit (only a metric, only a year, or "that",
  "those", "same"). It fills only the implicit fields from the previous
  analysis; a follow-up about "these" airports of a ranking stays that ranking
  with the metric the wording names. A follow-up may use only the validated
  previous analysis supplied in context; if it needs prior context and none is
  supplied, ask for clarification.
  A follow-up keeps the previous analysis's airports, action and year unless the
  user changes them. A follow-up that only narrows or switches the metric (for
  example to one operational measure) after a comparison stays a comparison of the
  same two airports.
- A follow-up may bring in another airport and leave the metric implicit, such as
  "compare it with LAX" or "how does BOS compare with PVD?". "It", "this airport"
  or "there" means the previous analysis's single airport. Return compare with the
  two airports, keeping the previous metric and year, when that metric can be
  compared at both airports. "And PVD?" or "what about PVD?" asks for the same
  analysis at the named airport instead.
- When the previous metric cannot be compared at those two airports, never
  substitute another metric: return clarification_required with analysis set to
  action compare and the two airports, every other field null.

Pending comparison. pending_comparison, when supplied, holds the two airports the
application just asked about: it asked which measure to compare for them.
- A message that names only a measure or topic answers that question: return
  compare with those two airports, in that order, and that metric, keeping the
  year of previous_analysis unless the user names one. This takes precedence over
  continuing previous_analysis.
- If that measure cannot be compared at those two airports, return the
  two-airport clarification above for the same two airports.
- A message that names its own airports or asks for a different analysis is a new
  question: ignore pending_comparison.

Metric scope. sfo_pressure and sfo_enplaned_trend exist only for SFO and cannot be
compared. congestion and the four operational metrics cover only LAX, SNA and
SFO. screen_score ranks New England airports; it is not a two-airport comparison.
passengers, seats, departures, passenger_growth, seat_occupancy and
long_haul_share can be compared between any two supported airports.

Periods. Supported periods are calendar years 2023, 2024 and 2025. If the user
names no year, set year to null: the server then uses the newest accepted period
(2025, compared with 2024). Growth and screen scores need a comparison year (2024
or 2025); operational and SFO metrics support 2024 and 2025. Any other year is
unsupported. The server independently validates every analysis and supplies all
user-visible wording. When kind is not analysis, set analysis to null, except for
the two-airport clarification above."""
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
    # Two validated airport codes when a clarification only lacks a comparable metric;
    # the server words that question itself (comparison_question).
    compare_airports: tuple[str, str] | None = None

    def as_outcome(self) -> dict[str, object]:
        if self.analysis is not None:
            return {"kind": "analysis", "analysis": self.analysis.model_dump(mode="json", exclude_none=True)}
        return {"kind": self.kind, "message": self.message}


def _make_input(message: str, context: Mapping[str, object] | None, settings: Settings,
                pending_comparison: Sequence[str] | None = None) -> str:
    if not isinstance(message, str) or not message.strip() or len(message) > settings.max_query_chars:
        raise ModelAdapterError("model_prompt_too_large")
    previous = None
    if context is not None:
        try:
            previous = AnalysisRequest.model_validate(context).model_dump(mode="json", exclude_none=True)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ModelAdapterError("model_invalid_response") from exc
    fields: dict[str, object] = {"question": message, "previous_analysis": previous}
    # Present only while a comparison clarification awaits its answer.
    if pending_comparison is not None:
        pair = list(pending_comparison)
        if len(pair) != 2 or len(set(pair)) != 2 or not all(isinstance(code, str) for code in pair) or not set(pair) <= AIRPORTS:
            raise ModelAdapterError("model_invalid_response")
        fields["pending_comparison"] = pair
    payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
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


def _parse_response(body: object, context: Mapping[str, object] | None = None) -> ModelInterpretation:
    if not isinstance(body, dict):
        raise ModelAdapterError("model_invalid_response")
    usage = _parse_usage(body)
    try:
        return _interpret(body, usage, context)
    except ModelAdapterError as exc:
        exc.usage = usage
        raise


_OPERATIONAL_AIRPORTS = frozenset({"LAX", "SNA", "SFO"})
_OPERATIONAL_METRICS = frozenset({"congestion", "cancellation_rate", "diversion_rate",
                                  "departure_delay_minutes", "taxi_out_minutes"})
_NOT_COMPARABLE = {
    "sfo_pressure": "SFO demand pressure is measured only for SFO.",
    "sfo_enplaned_trend": "The SFO passenger trend covers SFO only.",
    "screen_score": "The screening score ranks New England airports rather than comparing two.",
}


def _comparison_hint(proposed: object) -> list[str] | None:
    """Two named airports from a clarification that lacks only a comparable metric."""
    if not isinstance(proposed, dict) or not set(proposed) <= set(_ANALYSIS_FIELDS) or proposed.get("action") != "compare":
        return None
    if any(proposed.get(field) is not None for field in _ANALYSIS_FIELDS if field not in {"action", "airports"}):
        return None
    airports = proposed.get("airports")
    if not isinstance(airports, list) or len(airports) != 2 or not all(isinstance(code, str) for code in airports):
        return None
    codes = [code.upper() for code in airports]
    return codes if len(set(codes)) == 2 and set(codes) <= AIRPORTS else None


def comparison_question(airports: list[str], context: Mapping[str, object] | None) -> str:
    """Server-worded clarification naming only measures the contract can compare."""
    first, second = airports
    operational = set(airports) <= _OPERATIONAL_AIRPORTS
    measures = ["passenger growth", "seat occupancy", "passengers", "long-haul share"]
    if operational:
        measures.append("congestion")
    previous = context.get("metric") if isinstance(context, Mapping) else None
    lead = _NOT_COMPARABLE.get(previous) if isinstance(previous, str) else None
    if lead is None and previous in _OPERATIONAL_METRICS and not operational:
        lead = "Operational measures cover only LAX, SNA and SFO."
    example = "congestion" if operational else "passenger growth"
    question = (f"Which measure should I compare for {first} and {second}: {', '.join(measures[:-1])} or {measures[-1]}? "
                f"For example: “Compare {first} and {second} {example}.”")
    return f"{lead} {question}" if lead else question


def _interpret(body: dict[str, object], usage: ModelUsage,
               context: Mapping[str, object] | None = None) -> ModelInterpretation:
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
    # A clarification may name the two airports to compare; only they reach the wording.
    if kind == "clarification_required" and (airports := _comparison_hint(proposed)) is not None:
        return ModelInterpretation(kind, None, comparison_question(airports, context), usage, (airports[0], airports[1]))
    raise ModelAdapterError("model_invalid_response")


async def interpret_message(
    message: str,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    context: Mapping[str, object] | None = None,
    pending_comparison: Sequence[str] | None = None,
) -> ModelInterpretation:
    """Make exactly one request. An injected client supports offline MockTransport tests."""
    if not settings.model_access_available:
        raise ModelAdapterError("ai_unavailable")
    user_input = _make_input(message, context, settings, pending_comparison)
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
    return _parse_response(body, context)
