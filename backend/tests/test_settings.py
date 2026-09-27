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
