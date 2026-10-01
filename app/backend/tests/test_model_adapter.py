"""Offline contract tests for the one-call Gemini interpreter."""

from __future__ import annotations

import asyncio
import hashlib
import json
from functools import wraps
from pathlib import Path

import httpx
import pytest
from app import model_adapter
from app.model_adapter import (
    ADAPTER_SHA256,
    MAX_RESPONSE_BYTES,
    OUTPUT_SCHEMA,
    PROMPT_SHA256,
    ModelAdapterError,
    interpret_message,
)
from app.settings import Settings

KEY = "test-private-value"


def run_async(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"model_api_key": KEY, "model_name": "test.model", **overrides})


def _analysis() -> dict[str, object]:
    return {
        "action": "metric", "airports": ["ANC"], "region": None,
        "metric": "long_haul_share", "year": 2024, "threshold_miles": 3000,
    }


def _response(outcome: dict[str, object] | None = None, *, parts: list | None = None, **overrides: object) -> dict:
    outcome = outcome or {"kind": "analysis", "analysis": _analysis()}
    candidate = {
        "content": {"role": "model", "parts": parts if parts is not None else [{"text": json.dumps(outcome)}]},
        "finishReason": "STOP",
    }
    candidate.update(overrides.pop("candidate", {}))
    return {
        "candidates": [candidate],
        "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 70, "totalTokenCount": 370},
        **overrides,
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@run_async
async def test_one_request_exact_gemini_contract_and_validated_analysis() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_response())

    async with _client(handler) as client:
        result = await interpret_message("ANC long haul share in 2024", settings=_settings(), client=client)
    assert result.as_outcome() == {"kind": "analysis", "analysis": {
        "action": "metric", "airports": ["ANC"], "metric": "long_haul_share",
        "year": 2024, "threshold_miles": 3000.0,
    }}
    assert (result.usage.input_tokens, result.usage.output_tokens) == (300, 70)
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/models/test.model:generateContent"
    assert request.headers["x-goog-api-key"] == KEY
    assert "authorization" not in request.headers
    assert KEY not in str(request.url)
    payload = json.loads(request.content)
    assert set(payload) == {"systemInstruction", "contents", "generationConfig"}
    assert payload["systemInstruction"] == {"parts": [{"text": model_adapter.SYSTEM_PROMPT}]}
    assert payload["contents"][0]["role"] == "user"
    assert payload["generationConfig"] == {
        "responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": OUTPUT_SCHEMA}},
        "maxOutputTokens": 1024,
        "temperature": 0,
        "thinkingConfig": {"thinkingBudget": 0},
    }
    assert "tools" not in payload


def _non_null(schema: dict) -> dict:
    """The non-null branch of a nullable field schema."""
    if "anyOf" in schema:
        branches = [branch for branch in schema["anyOf"] if branch != {"type": "null"}]
        assert len(branches) == 1 and {"type": "null"} in schema["anyOf"]
        return branches[0]
    return schema


def test_schema_is_a_fixed_shape_json_schema() -> None:
    from app.contracts import AIRPORTS, METRICS, AnalysisRequest

    assert OUTPUT_SCHEMA["type"] == "object"
    assert OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(OUTPUT_SCHEMA["required"]) == set(OUTPUT_SCHEMA["properties"]) == {"kind", "analysis"}
    nested = _non_null(OUTPUT_SCHEMA["properties"]["analysis"])
    fields = {"action", "airports", "region", "metric", "year", "threshold_miles"}
    # Every field is always present (unused ones null) and nothing else is allowed.
    assert nested["type"] == "object" and nested["additionalProperties"] is False
    assert set(nested["required"]) == set(nested["properties"]) == fields
    assert "bundle_id" not in nested["properties"]
    properties = nested["properties"]
    # Enums mirror the contract exactly.
    assert properties["action"] == {"type": "string", "enum": ["rank", "compare", "metric", "explain"]}
    assert set(_non_null(properties["metric"])["enum"]) == set(METRICS)
    assert set(_non_null(properties["airports"])["items"]["enum"]) == set(AIRPORTS)
    assert _non_null(properties["region"])["enum"] == ["new_england"]
    assert properties["year"] == {"type": ["integer", "null"]}
    assert properties["threshold_miles"] == {"type": ["number", "null"]}
    for name in fields - {"action"}:
        assert "null" in json.dumps(properties[name])
    # The schema never admits a field the contract lacks.
    assert fields <= set(AnalysisRequest.model_fields)
    assert "nullable" not in json.dumps(OUTPUT_SCHEMA)
    assert all(isinstance(value, str) for value in _non_null(properties["metric"])["enum"])


def test_fixed_shape_output_with_nulls_is_accepted() -> None:
    full = {"action": "compare", "airports": ["LAX", "SNA"], "region": None,
            "metric": "cancellation_rate", "year": 2025, "threshold_miles": None}
    _, result = asyncio.run(_captured_payload(_settings(), {"kind": "analysis", "analysis": full}))
    assert result.as_outcome() == {"kind": "analysis", "analysis": {
        "action": "compare", "airports": ["LAX", "SNA"], "metric": "cancellation_rate", "year": 2025}}
    explain = {"action": "explain", "airports": None, "region": None, "metric": None, "year": None, "threshold_miles": None}
    _, result = asyncio.run(_captured_payload(_settings(), {"kind": "analysis", "analysis": explain}))
    assert result.as_outcome() == {"kind": "analysis", "analysis": {"action": "explain"}}


@pytest.mark.parametrize("rule", [
    "Ranking\n  is limited to New England airports",
    "compare: exactly two airports",
    "metric: exactly one airport",
    "Set action to explain and every other field to null",
    "keeps the previous analysis's airports, action and year",
    "after a comparison stays a comparison",
    "several separate questions are unsupported_scope",
    "several measures for the same airports follow the overview rule",
    "seats or departures with other measures is unsupported_scope",
    "return clarification_required. Never guess an airport",
])
def test_prompt_states_general_contract_rules(rule: str) -> None:
    assert rule in model_adapter.SYSTEM_PROMPT


@pytest.mark.parametrize("phrase", [
    "Show only cancellations", "Explain that result", "Rank SFO", "Compare LA airports",
    "SFO unmet demand", "Calculate ANC long-haul share",
    "Does SFO need more capacity", "Is Manchester worth investigating", "Just cancellations",
    "What about ANC long-haul share", "is that an investment signal", "Orange County",
])
def test_prompt_does_not_copy_evaluation_questions(phrase: str) -> None:
    assert phrase.lower() not in model_adapter.SYSTEM_PROMPT.lower()


@run_async
async def test_rejected_output_keeps_reported_usage_for_cost_accounting() -> None:
    bad = {"kind": "analysis", "analysis": {**_analysis(), "action": "rank", "airports": ["SFO"],
                                             "metric": "passengers", "threshold_miles": None}}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response(bad))

    async with _client(handler) as client:
        with pytest.raises(ModelAdapterError) as caught:
            await interpret_message("rank it", settings=_settings(), client=client)
    assert caught.value.code == "model_invalid_response"
    assert (caught.value.usage.input_tokens, caught.value.usage.output_tokens) == (300, 70)


@run_async
async def test_http_failure_has_no_usage() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    async with _client(handler) as client:
        with pytest.raises(ModelAdapterError) as caught:
            await interpret_message("ANC", settings=_settings(), client=client)
    assert caught.value.usage is None


def test_adapter_identity_covers_complete_source_not_only_prompt() -> None:
    source = Path(model_adapter.__file__).read_bytes()
    assert ADAPTER_SHA256 == hashlib.sha256(source).hexdigest()
    assert ADAPTER_SHA256 != PROMPT_SHA256
    assert len(PROMPT_SHA256) == 64


@run_async
async def test_thinking_config_is_omitted_when_budget_unset() -> None:
    payload, _ = await _captured_payload(_settings(model_thinking_budget=None))
    assert "thinkingConfig" not in payload["generationConfig"]
    payload, _ = await _captured_payload(_settings(model_thinking_budget=128))
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 128}


@run_async
async def test_model_name_is_url_escaped() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=_response())

    async with _client(handler) as client:
        await interpret_message("question", settings=_settings(model_name="gemini-2.5-flash"), client=client)
    assert urls == ["https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"]


@run_async
async def test_context_is_validated_before_call_and_server_owns_safe_message() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        sent = json.loads(request.content)
        assert json.loads(sent["contents"][0]["parts"][0]["text"])["previous_analysis"] == {
            "action": "metric", "airports": ["ANC"], "metric": "long_haul_share",
            "year": 2024, "threshold_miles": 3000.0,
        }
        return httpx.Response(200, json=_response({"kind": "clarification_required", "analysis": None}))

    async with _client(handler) as client:
        result = await interpret_message("and last year?", settings=_settings(), context=_analysis(), client=client)
        assert result.as_outcome() == {
            "kind": "clarification_required",
            "message": "Please name the airport, metric, and supported period you want to analyze.",
        }
        with pytest.raises(ModelAdapterError) as exc:
            await interpret_message("question", settings=_settings(), context={"action": "DROP"}, client=client)
    assert exc.value.code == "model_invalid_response"
    assert calls == 1


@run_async
async def test_unsupported_scope_and_omitted_analysis_key() -> None:
    async with _client(lambda request: httpx.Response(200, json=_response({"kind": "unsupported_scope"}))) as client:
        result = await interpret_message("what is BOS ROI?", settings=_settings(), client=client)
    assert result.kind == "unsupported_scope"
    assert result.message == "I can't answer that from this airport data. I can rank New England airports, compare two airports on one measure or overall, or show one airport's figures for 2023 to 2025."


@run_async
@pytest.mark.parametrize("empty", [{}, {"action": None, "airports": None, "year": None}])
async def test_empty_analysis_object_counts_as_null_for_safe_outcomes(empty) -> None:
    body = _response({"kind": "clarification_required", "analysis": empty})
    async with _client(lambda request: httpx.Response(200, json=body)) as client:
        result = await interpret_message("which airport?", settings=_settings(), client=client)
    assert result.kind == "clarification_required" and result.analysis is None


@run_async
async def test_omitted_nullable_analysis_fields_default_to_null() -> None:
    outcome = {"kind": "analysis", "analysis": {"action": "metric", "airports": ["ANC"], "metric": "long_haul_share"}}
    _, result = await _captured_payload(_settings(), outcome)
    assert result.analysis.year is None and result.analysis.threshold_miles is None


@run_async
@pytest.mark.parametrize("outcome", [
    {"kind": "analysis", "analysis": {**_analysis(), "metric": "made_up"}},
    {"kind": "analysis", "analysis": {**_analysis(), "year": 2022}},
    {"kind": "analysis", "analysis": {**_analysis(), "extra": "x"}},
    {"kind": "analysis", "analysis": None},
    {"kind": "analysis", "analysis": _analysis(), "message": "provider prose"},
    {"kind": "unsupported_scope", "analysis": _analysis()},
    {"kind": "something_else", "analysis": None},
    {"analysis": _analysis()},
    ["not", "an", "object"],
])
async def test_invalid_outcomes_fail_closed(outcome: object) -> None:
    body = _response(parts=[{"text": json.dumps(outcome)}])
    async with _client(lambda request: httpx.Response(200, json=body)) as client:
        with pytest.raises(ModelAdapterError) as exc:
            await interpret_message("question", settings=_settings(), client=client)
    assert exc.value.code == "model_invalid_response"


@run_async
@pytest.mark.parametrize(("response", "code"), [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "model_refusal"),
    (_response(candidate={"finishReason": "SAFETY"}), "model_refusal"),
    (_response(candidate={"finishReason": "RECITATION"}), "model_refusal"),
    (_response(candidate={"finishReason": "MAX_TOKENS"}), "model_incomplete"),
    (_response(candidate={"finishReason": "OTHER"}), "model_invalid_response"),
    (_response(candidates=[]), "model_invalid_response"),
    (_response(parts=[]), "model_invalid_response"),
    (_response(parts=[{"text": "{\"kind\": \"analysis\""}]), "model_invalid_response"),
    (_response(usageMetadata={"promptTokenCount": -1}), "model_invalid_response"),
    (_response(usageMetadata="nope"), "model_invalid_response"),
    ([], "model_invalid_response"),
])
async def test_response_envelope_failures(response: object, code: str) -> None:
    async with _client(lambda request: httpx.Response(200, json=response)) as client:
        with pytest.raises(ModelAdapterError) as exc:
            await interpret_message("question", settings=_settings(), client=client)
    assert exc.value.code == code


@run_async
async def test_thought_parts_are_ignored_and_split_text_is_joined() -> None:
    text = json.dumps({"kind": "analysis", "analysis": _analysis()})
    body = _response(parts=[{"text": "private reasoning", "thought": True}, {"text": text[:20]}, {"text": text[20:]}])
    body["usageMetadata"]["thoughtsTokenCount"] = 15
    async with _client(lambda request: httpx.Response(200, json=body)) as client:
        result = await interpret_message("question", settings=_settings(), client=client)
    assert result.analysis.metric == "long_haul_share"
    assert result.usage.output_tokens == 85


@run_async
async def test_missing_usage_metadata_is_tolerated() -> None:
    body = _response()
    del body["usageMetadata"]
    async with _client(lambda request: httpx.Response(200, json=body)) as client:
        result = await interpret_message("question", settings=_settings(), client=client)
    assert (result.usage.input_tokens, result.usage.output_tokens) == (0, 0)


@run_async
@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503])
async def test_http_errors_keep_status_for_logs_and_hide_provider_text(status: int) -> None:
    private_body = f"{KEY} provider stack trace"
    async with _client(lambda request: httpx.Response(status, text=private_body)) as client:
        with pytest.raises(ModelAdapterError) as exc:
            await interpret_message("question", settings=_settings(), client=client)
    assert exc.value.code == "ai_unavailable"
    assert exc.value.provider_status == status
    assert private_body not in str(exc.value) and KEY not in repr(exc.value)


@run_async
async def test_hard_total_timeout_even_with_injected_client() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json=_response())

    async with _client(handler) as client:
        with pytest.raises(ModelAdapterError) as exc:
            await interpret_message("question", settings=_settings(model_timeout_seconds=1), client=client)
    assert exc.value.code == "model_timeout"


@run_async
async def test_transport_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with _client(handler) as client:
        with pytest.raises(ModelAdapterError) as exc:
            await interpret_message("question", settings=_settings(), client=client)
    assert exc.value.code == "ai_unavailable" and exc.value.provider_status is None


@run_async
async def test_no_call_for_missing_key_or_oversized_prompt() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    async with _client(handler) as client:
        for settings, message, expected_code in [
            (Settings(), "question", "ai_unavailable"),
            (_settings(), "x" * 4001, "model_prompt_too_large"),
            (_settings(), "   ", "model_prompt_too_large"),
            (_settings(model_max_prompt_tokens=100), "question", "model_prompt_too_large"),
        ]:
            with pytest.raises(ModelAdapterError) as exc:
                await interpret_message(message, settings=settings, client=client)
            assert exc.value.code == expected_code
    assert calls == 0


@run_async
async def test_response_byte_cap_and_malformed_json() -> None:
    for content in [b"x" * (MAX_RESPONSE_BYTES + 1), b"not-json"]:
        async with _client(lambda request, payload=content: httpx.Response(200, content=payload)) as client:
            with pytest.raises(ModelAdapterError) as exc:
                await interpret_message("question", settings=_settings(), client=client)
        assert exc.value.code == "model_invalid_response"


async def _captured_payload(settings: Settings, outcome: dict[str, object] | None = None) -> tuple[dict, object]:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=_response(outcome))

    async with _client(handler) as client:
        result = await interpret_message("ANC long haul share", settings=settings, client=client)
    return captured[0], result


def test_prompt_declares_supported_periods_including_2025() -> None:
    year = _non_null(OUTPUT_SCHEMA["properties"]["analysis"])["properties"]["year"]
    assert year == {"type": ["integer", "null"]}
    assert "2025" in model_adapter.SYSTEM_PROMPT
    assert "set year to null" in model_adapter.SYSTEM_PROMPT


@pytest.mark.parametrize("year", [2025, None])
def test_2025_and_omitted_year_outcomes_are_accepted(year) -> None:
    analysis = {**_analysis(), "year": year}
    _, result = asyncio.run(_captured_payload(_settings(), {"kind": "analysis", "analysis": analysis}))
    assert result.analysis.year == year
    assert result.analysis.bundle_id is None


@run_async
async def test_unsupported_year_from_model_fails_closed() -> None:
    analysis = {**_analysis(), "year": 2026}
    with pytest.raises(ModelAdapterError) as exc:
        await _captured_payload(_settings(), {"kind": "analysis", "analysis": analysis})
    assert exc.value.code == "model_invalid_response"


def _hint(airports, **fields) -> dict[str, object]:
    return {"kind": "clarification_required", "analysis": {
        "action": "compare", "airports": airports, "region": None, "metric": None, "year": None,
        "threshold_miles": None, **fields}}


async def _clarify(outcome: dict[str, object], context: dict[str, object] | None):
    async with _client(lambda request: httpx.Response(200, json=_response(outcome))) as client:
        return await interpret_message("follow-up", settings=_settings(), context=context, client=client)


@run_async
@pytest.mark.parametrize(("context", "airports", "message"), [
    ({"action": "metric", "airports": ["SFO"], "metric": "sfo_pressure"}, ["SFO", "LAX"],
     ("SFO demand pressure is measured only for SFO. Which measure should I compare for SFO and LAX: an overall "
     "comparison of the key measures, passenger growth, seat occupancy, passengers, long-haul share or congestion? "
     "For example: “Compare everything” or “Compare SFO and LAX congestion.”")),
    ({"action": "rank", "region": "new_england", "metric": "screen_score"}, ["bos", "PVD"],
     ("The screening score ranks New England airports rather than comparing two. Which measure should I compare for BOS "
     "and PVD: an overall comparison of the key measures, passenger growth, seat occupancy, passengers or long-haul share? "
     "For example: “Compare everything” or “Compare BOS and PVD passenger growth.”")),
    ({"action": "compare", "airports": ["LAX", "SNA"], "metric": "congestion"}, ["LAX", "BOS"],
     ("Operational measures cover only LAX, SNA and SFO. Which measure should I compare for LAX and BOS: an overall "
     "comparison of the key measures, passenger growth, seat occupancy, passengers or long-haul share? "
     "For example: “Compare everything” or “Compare LAX and BOS passenger growth.”")),
    (None, ["ANC", "BOS"],
     ("Which measure should I compare for ANC and BOS: an overall comparison of the key measures, passenger growth, seat "
     "occupancy, passengers or long-haul share? For example: “Compare everything” or “Compare ANC and BOS "
     "passenger growth.”")),
])
async def test_comparison_clarification_names_only_comparable_measures(context, airports, message) -> None:
    result = await _clarify(_hint(airports), context)
    assert result.kind == "clarification_required" and result.analysis is None
    assert result.compare_airports == tuple(code.upper() for code in airports)
    assert result.message == message and len(result.message) <= 500
    assert model_adapter.comparison_question(list(result.compare_airports), context) == message


@run_async
@pytest.mark.parametrize("outcome", [
    _hint(["SFO"]),
    _hint(["SFO", "SFO"]),
    _hint(["SFO", "XXX"]),
    _hint(["SFO", "LAX"], metric="congestion"),
    _hint(["SFO", "LAX"], year=2025),
    _hint(["SFO", "LAX"], action="metric"),
    _hint(["SFO", "LAX"], note="provider prose"),
    {"kind": "unsupported_scope", "analysis": _hint(["SFO", "LAX"])["analysis"]},
])
async def test_malformed_comparison_hints_fail_closed(outcome) -> None:
    with pytest.raises(ModelAdapterError) as exc:
        await _clarify(outcome, None)
    assert exc.value.code == "model_invalid_response"


@run_async
async def test_pending_comparison_is_sent_only_when_supplied_and_validated_before_the_call() -> None:
    sent_inputs = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_inputs.append(json.loads(json.loads(request.content)["contents"][0]["parts"][0]["text"]))
        outcome = {"kind": "analysis", "analysis": {**_analysis(), "action": "compare", "airports": ["SFO", "LAX"],
                                                    "metric": "congestion", "year": 2025, "threshold_miles": None}}
        return httpx.Response(200, json=_response(outcome))

    context = {"action": "metric", "airports": ["SFO"], "metric": "sfo_pressure"}
    async with _client(handler) as client:
        answered = await interpret_message("congestion", settings=_settings(), context=context, client=client,
                                           pending_comparison=["SFO", "LAX"])
        await interpret_message("congestion", settings=_settings(), context=context, client=client)
        for pair in (["SFO"], ["SFO", "SFO"], ["SFO", "XXX"], ["sfo", "LAX"], ["SFO", "LAX", "SNA"]):
            with pytest.raises(ModelAdapterError) as exc:
                await interpret_message("congestion", settings=_settings(), context=context, client=client,
                                        pending_comparison=pair)
            assert exc.value.code == "model_invalid_response"
    assert answered.analysis.airports == ["SFO", "LAX"] and answered.analysis.metric == "congestion"
    assert sent_inputs[0] == {"question": "congestion", "previous_analysis": context, "pending_comparison": ["SFO", "LAX"]}
    # Without a pending clarification the model input is exactly as before.
    assert sent_inputs[1] == {"question": "congestion", "previous_analysis": context}
    assert len(sent_inputs) == 2
