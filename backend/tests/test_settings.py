import pytest
from app.settings import Settings, load_settings
from pydantic import ValidationError


def test_defaults_enforce_documented_prototype_limits() -> None:
    settings = Settings()

    assert settings.query_timeout_seconds == 30
    assert settings.max_query_chars == 4000
    assert settings.source_timeout_seconds == 60
    assert settings.source_max_pages == 4
    assert settings.source_page_size == 5000
    assert settings.source_max_bytes == 10 * 1024 * 1024
    assert settings.model_timeout_seconds == 20
    assert settings.model_max_output_tokens == 512
    assert settings.model_max_prompt_tokens == 8000
    assert settings.model_request_budget_usd == 0.02
    assert settings.model_process_budget_usd == 2.0
    assert settings.model_runtime_enabled is False
    assert settings.model_runtime_admitted("a" * 64) is False


def test_missing_model_access_keeps_settings_usable_for_presets() -> None:
    settings = load_settings({})

    assert settings.model_access_available is False
    assert settings.query_timeout_seconds == 30


def test_wrong_type_model_key_remains_a_structured_validation_error() -> None:
    with pytest.raises(ValidationError) as caught:
        Settings.model_validate({"model_api_key": 123})

    assert caught.value.errors()[0]["loc"] == ("model_api_key",)
    assert caught.value.errors()[0]["type"] == "value_error"


def test_validated_environment_overrides_are_typed() -> None:
    settings = load_settings({
        "QUERY_TIMEOUT_SECONDS": "25",
        "SOURCE_MAX_PAGES": "3",
        "MODEL_REQUEST_BUDGET_USD": "0.01",
        "OPENAI_API_KEY": "  test-key-value  ",
        "OPENAI_MODEL": "test.model-v1",
    })

    assert settings.query_timeout_seconds == 25
    assert settings.source_max_pages == 3
    assert settings.model_request_budget_usd == 0.01
    assert settings.model_access_available is True
    assert settings.model_runtime_admitted("a" * 64) is False


def test_exact_runtime_admission_requires_explicit_model_adapter_and_prices() -> None:
    admitted = load_settings({
        "OPENAI_API_KEY": "fake-key",
        "OPENAI_MODEL": "demo.model-v1",
        "MODEL_RUNTIME_ENABLED": "TrUe",
        "MODEL_ADMITTED_NAME": "demo.model-v1",
        "MODEL_ADMITTED_ADAPTER_SHA256": "a" * 64,
        "MODEL_INPUT_USD_PER_MILLION_TOKENS": "0.15",
        "MODEL_OUTPUT_USD_PER_MILLION_TOKENS": "0.6",
    })
    assert admitted.model_runtime_admitted("a" * 64) is True
    assert admitted.model_runtime_admitted("b" * 64) is False
    assert admitted.model_input_usd_per_million_tokens == 0.15
    assert admitted.model_output_usd_per_million_tokens == 0.6
    assert admitted.model_runtime_enabled is True


@pytest.mark.parametrize("missing", [
    "OPENAI_API_KEY", "OPENAI_MODEL", "MODEL_RUNTIME_ENABLED",
    "MODEL_ADMITTED_NAME", "MODEL_ADMITTED_ADAPTER_SHA256",
    "MODEL_INPUT_USD_PER_MILLION_TOKENS", "MODEL_OUTPUT_USD_PER_MILLION_TOKENS",
])
def test_runtime_admission_fails_closed_when_any_field_is_missing(missing: str) -> None:
    values = {
        "OPENAI_API_KEY": "fake-key",
        "OPENAI_MODEL": "demo.model-v1",
        "MODEL_RUNTIME_ENABLED": "true",
        "MODEL_ADMITTED_NAME": "demo.model-v1",
        "MODEL_ADMITTED_ADAPTER_SHA256": "a" * 64,
        "MODEL_INPUT_USD_PER_MILLION_TOKENS": "0.15",
        "MODEL_OUTPUT_USD_PER_MILLION_TOKENS": "0.6",
    }
    values.pop(missing)
    assert load_settings(values).model_runtime_admitted("a" * 64) is False


def test_runtime_admission_requires_exact_model_name() -> None:
    settings = load_settings({
        "OPENAI_API_KEY": "fake-key",
        "OPENAI_MODEL": "demo.model-v1",
        "MODEL_RUNTIME_ENABLED": "true",
        "MODEL_ADMITTED_NAME": "demo.model-v2",
        "MODEL_ADMITTED_ADAPTER_SHA256": "a" * 64,
        "MODEL_INPUT_USD_PER_MILLION_TOKENS": "0.15",
        "MODEL_OUTPUT_USD_PER_MILLION_TOKENS": "0.6",
    })
    assert settings.model_access_available is True
    assert settings.model_runtime_admitted("a" * 64) is False


@pytest.mark.parametrize("overrides", [
    {"MODEL_RUNTIME_ENABLED": "1"},
    {"MODEL_RUNTIME_ENABLED": "yes"},
    {"MODEL_RUNTIME_ENABLED": ""},
    {"MODEL_ADMITTED_NAME": "unsafe model name"},
    {"MODEL_ADMITTED_ADAPTER_SHA256": "a" * 63},
    {"MODEL_ADMITTED_ADAPTER_SHA256": "A" * 64},
    {"MODEL_ADMITTED_ADAPTER_SHA256": "g" * 64},
    {"MODEL_INPUT_USD_PER_MILLION_TOKENS": "0"},
    {"MODEL_OUTPUT_USD_PER_MILLION_TOKENS": "-1"},
    {"MODEL_INPUT_USD_PER_MILLION_TOKENS": "nan"},
    {"MODEL_OUTPUT_USD_PER_MILLION_TOKENS": "inf"},
    {"MODEL_OUTPUT_USD_PER_MILLION_TOKENS": "1000.01"},
])
def test_invalid_admission_values_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        load_settings(overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"QUERY_TIMEOUT_SECONDS": "not-a-number"},
        {"QUERY_TIMEOUT_SECONDS": "31"},
        {"SOURCE_MAX_PAGES": "5"},
        {"SOURCE_PAGE_SIZE": "5001"},
        {"MODEL_TIMEOUT_SECONDS": "21"},
        {"MODEL_REQUEST_BUDGET_USD": "0.03"},
        {"MODEL_PROCESS_BUDGET_USD": "2.01"},
        {"OPENAI_MODEL": "unsafe model name"},
        {"OPENAI_API_KEY": "invalid\nkey"},
    ],
)
def test_malformed_or_unsafe_environment_values_are_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        load_settings(overrides)
