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
        "responseMimeType": "application/json",
        "responseSchema": OUTPUT_SCHEMA,
        "maxOutputTokens": 512,
        "temperature": 0,
        "thinkingConfig": {"thinkingBudget": 0},
    }
    assert "tools" not in payload


def test_schema_uses_gemini_openapi_subset() -> None:
    assert OUTPUT_SCHEMA["type"] == "OBJECT"
    assert set(OUTPUT_SCHEMA["required"]) == set(OUTPUT_SCHEMA["properties"]) == {"kind", "analysis"}
    nested = OUTPUT_SCHEMA["properties"]["analysis"]
    assert nested["nullable"] is True
    assert set(nested["propertyOrdering"]) == set(nested["properties"])
    for field in nested["properties"].values():
        assert field["type"].isupper()
        assert all(isinstance(value, str) for value in field.get("enum", []))
    assert "bundle_id" not in nested["properties"]
    assert "additionalProperties" not in json.dumps(OUTPUT_SCHEMA)


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
    assert result.message == "That question is outside the supported airport analyses and periods."


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
    year = OUTPUT_SCHEMA["properties"]["analysis"]["properties"]["year"]
    assert year == {"type": "INTEGER", "nullable": True}
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
