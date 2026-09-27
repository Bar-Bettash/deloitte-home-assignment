"""Offline contract tests for the one-call model interpreter."""

from __future__ import annotations

import asyncio
import json
from functools import wraps

import httpx
import pytest
from app.model_adapter import (
    MAX_RESPONSE_BYTES,
    OUTPUT_SCHEMA,
    PROMPT_SHA256,
    ModelAdapterError,
    interpret_message,
)
from app.settings import Settings


def run_async(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"model_api_key": "test-private-value", "model_name": "test.model", **overrides})


def _analysis() -> dict[str, object]:
    return {
        "action": "metric", "airports": ["ANC"], "region": None,
        "metric": "long_haul_share", "year": 2024, "threshold_miles": 3000,
    }


def _response(outcome: dict[str, object] | None = None, **overrides: object) -> dict[str, object]:
    outcome = outcome or {"kind": "analysis", "analysis": _analysis(), "message": None}
    return {
        "status": "completed",
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(outcome)}]}],
        "usage": {"input_tokens": 300, "output_tokens": 70},
        **overrides,
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@run_async
async def test_one_request_exact_responses_contract_and_validated_analysis() -> None:
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
    assert result.usage.input_tokens == 300
    assert result.usage.output_tokens == 70
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST" and str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == "Bearer test-private-value"
    payload = json.loads(request.content)
    assert payload["model"] == "test.model"
    assert payload["store"] is False and payload["max_output_tokens"] == 512
    assert "tools" not in payload and "previous_response_id" not in payload
    assert payload["text"]["format"] == {
        "type": "json_schema", "name": "airport_intent", "strict": True, "schema": OUTPUT_SCHEMA,
    }
    assert OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(OUTPUT_SCHEMA["required"]) == set(OUTPUT_SCHEMA["properties"])
    nested = OUTPUT_SCHEMA["properties"]["analysis"]
    assert nested["additionalProperties"] is False
    assert set(nested["required"]) == set(nested["properties"])
    assert len(PROMPT_SHA256) == 64


@run_async
async def test_context_is_validated_before_call_and_server_owns_safe_message() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        sent = json.loads(request.content)
        assert json.loads(sent["input"][1]["content"])["previous_analysis"] == {
            "action": "metric", "airports": ["ANC"], "metric": "long_haul_share",
            "year": 2024, "threshold_miles": 3000.0,
        }
        return httpx.Response(200, json=_response({"kind": "clarification_required", "analysis": None, "message": None}))

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
@pytest.mark.parametrize("outcome", [
    {"kind": "analysis", "analysis": {**_analysis(), "metric": "made_up"}, "message": None},
    {"kind": "analysis", "analysis": {**_analysis(), "year": 2022}, "message": None},
    {"kind": "analysis", "analysis": _analysis(), "message": "provider prose"},
    {"kind": "analysis", "analysis": {**_analysis(), "extra": "x"}, "message": None},
    {"kind": "unsupported_scope", "analysis": _analysis(), "message": None},
    {"kind": "unsupported_scope", "analysis": None, "message": "provider prose"},
])
async def test_invalid_outcomes_fail_closed(outcome: dict[str, object]) -> None:
    async with _client(lambda request: httpx.Response(200, json=_response(outcome))) as client:
        with pytest.raises(ModelAdapterError) as exc:
            await interpret_message("question", settings=_settings(), client=client)
    assert exc.value.code == "model_invalid_response"


@run_async
@pytest.mark.parametrize(("response", "code"), [
    (_response(status="incomplete"), "model_incomplete"),
    (_response(output=[{"type": "message", "status": "completed", "content": [{"type": "refusal", "refusal": "private"}]}]), "model_refusal"),
    (_response(output=[]), "model_invalid_response"),
    (_response(output=[*_response()["output"], *_response()["output"]]), "model_invalid_response"),
    (_response(output=[{"type": "function_call"}, *_response()["output"]]), "model_invalid_response"),
    (_response(usage={"input_tokens": 300, "output_tokens": 9999}), "model_invalid_response"),
    (_response(usage=None), "model_invalid_response"),
])
async def test_response_envelope_failures(response: dict[str, object], code: str) -> None:
    async with _client(lambda request: httpx.Response(200, json=response)) as client:
        with pytest.raises(ModelAdapterError) as exc:
            await interpret_message("question", settings=_settings(), client=client)
    assert exc.value.code == code


@run_async
async def test_http_and_provider_text_are_not_exposed() -> None:
    private_body = "test-private-value provider stack trace"
    async with _client(lambda request: httpx.Response(429, text=private_body)) as client:
        with pytest.raises(ModelAdapterError) as exc:
            await interpret_message("question", settings=_settings(), client=client)
    assert exc.value.code == "ai_unavailable"
    assert private_body not in str(exc.value)


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
async def test_no_call_for_missing_settings_or_oversized_prompt() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    async with _client(handler) as client:
        for settings, message, expected_code in [
            (Settings(), "question", "ai_unavailable"),
            (_settings(), "x" * 4001, "model_prompt_too_large"),
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
