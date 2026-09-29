import pytest
from app.settings import HostingConfigError, Settings, load_hosting, load_settings
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
        "OPENAI_API_KEY": "  test-key-value  ",
        "OPENAI_MODEL": "test.model-v1",
    })

    assert settings.query_timeout_seconds == 25
    assert settings.source_max_pages == 3
    assert settings.model_access_available is True
    assert settings.model_runtime_admitted("a" * 64) is False


def test_exact_runtime_admission_requires_explicit_model_and_adapter() -> None:
    admitted = load_settings({
        "OPENAI_API_KEY": "fake-key",
        "OPENAI_MODEL": "demo.model-v1",
        "MODEL_RUNTIME_ENABLED": "TrUe",
        "MODEL_ADMITTED_NAME": "demo.model-v1",
        "MODEL_ADMITTED_ADAPTER_SHA256": "a" * 64,
    })
    assert admitted.model_runtime_admitted("a" * 64) is True
    assert admitted.model_runtime_admitted("b" * 64) is False
    assert admitted.model_runtime_enabled is True


@pytest.mark.parametrize("missing", [
    "OPENAI_API_KEY", "OPENAI_MODEL", "MODEL_RUNTIME_ENABLED",
    "MODEL_ADMITTED_NAME", "MODEL_ADMITTED_ADAPTER_SHA256",
])
def test_runtime_admission_fails_closed_when_any_field_is_missing(missing: str) -> None:
    values = {
        "OPENAI_API_KEY": "fake-key",
        "OPENAI_MODEL": "demo.model-v1",
        "MODEL_RUNTIME_ENABLED": "true",
        "MODEL_ADMITTED_NAME": "demo.model-v1",
        "MODEL_ADMITTED_ADAPTER_SHA256": "a" * 64,
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
        {"OPENAI_MODEL": "unsafe model name"},
        {"OPENAI_API_KEY": "invalid\nkey"},
    ],
)
def test_malformed_or_unsafe_environment_values_are_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        load_settings(overrides)


def test_removed_spending_settings_are_not_read() -> None:
    settings = load_settings({
        "MODEL_REQUEST_BUDGET_USD": "not-read",
        "MODEL_PROCESS_BUDGET_USD": "not-read",
        "MODEL_INPUT_USD_PER_MILLION_TOKENS": "not-read",
    })
    assert not hasattr(settings, "model_request_budget_usd")
    assert not hasattr(settings, "model_input_usd_per_million_tokens")


FAKE_CODE = "fake-access-code-0123456789"
FAKE_KEY = "fake-signing-key-for-tests-only-0123456789"


def test_plain_loopback_run_keeps_gate_off_with_one_query_slot() -> None:
    hosting = load_hosting({})
    assert hosting.gated is False
    assert hosting.on_vercel is False
    assert hosting.secure_cookies is False
    assert hosting.max_concurrent_queries == 1
    assert hosting.allowed_hosts == frozenset()


def test_local_signing_key_alone_does_not_gate() -> None:
    assert load_hosting({"APP_SIGNING_KEY": FAKE_KEY}).gated is False


@pytest.mark.parametrize("environ", [
    {"ALLOWED_HOSTS": "demo.example.com"},
    {"ACCESS_CODE": FAKE_CODE},
    {"VERCEL": "1"},
    {"ALLOWED_HOSTS": "demo.example.com", "APP_SIGNING_KEY": FAKE_KEY},
    {"ACCESS_CODE": FAKE_CODE, "ALLOWED_HOSTS": "demo.example.com"},
    {"VERCEL": "1", "APP_SIGNING_KEY": FAKE_KEY},
])
def test_any_gating_signal_without_both_secrets_fails_closed(environ) -> None:
    with pytest.raises(HostingConfigError) as caught:
        load_hosting(environ)
    assert FAKE_CODE not in str(caught.value)
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize("code", ["short-code", "x" * 19, "has space in the code value", "tab\tinside-the-code-value", "x" * 257])
def test_weak_or_malformed_access_code_is_rejected(code) -> None:
    with pytest.raises(HostingConfigError) as caught:
        load_hosting({"ACCESS_CODE": code, "APP_SIGNING_KEY": FAKE_KEY})
    assert code not in str(caught.value)


@pytest.mark.parametrize("key", ["k" * 31, "k" * 513])
def test_short_or_oversized_signing_key_is_rejected(key) -> None:
    with pytest.raises(HostingConfigError):
        load_hosting({"ACCESS_CODE": FAKE_CODE, "APP_SIGNING_KEY": key})


def test_gated_config_collects_allowed_and_vercel_hosts_and_defaults_to_four_slots() -> None:
    hosting = load_hosting({
        "VERCEL": "1",
        "ACCESS_CODE": FAKE_CODE,
        "APP_SIGNING_KEY": FAKE_KEY,
        "ALLOWED_HOSTS": " Demo.Example.com ,other.example.org",
        "VERCEL_URL": "app-abc123.vercel.app",
        "VERCEL_BRANCH_URL": "https://app-git-main.vercel.app/",
        "VERCEL_PROJECT_PRODUCTION_URL": "",
    })
    assert hosting.gated and hosting.on_vercel and hosting.secure_cookies
    assert hosting.allowed_hosts == {
        "demo.example.com", "other.example.org", "app-abc123.vercel.app", "app-git-main.vercel.app",
    }
    assert hosting.max_concurrent_queries == 4
    assert hosting.access_code.get_secret_value() == FAKE_CODE
    assert FAKE_CODE not in repr(hosting)
    assert FAKE_KEY not in repr(hosting)


def test_vercel_hosts_are_ignored_off_vercel() -> None:
    hosting = load_hosting({"ACCESS_CODE": FAKE_CODE, "APP_SIGNING_KEY": FAKE_KEY, "VERCEL_URL": "x.vercel.app"})
    assert hosting.allowed_hosts == frozenset()


@pytest.mark.parametrize("environ", [
    {"ALLOWED_HOSTS": "bad host"},
    {"ALLOWED_HOSTS": "demo.example.com:8443"},
    {"ALLOWED_HOSTS": "*.example.com"},
    {"MAX_CONCURRENT_QUERIES": "0"},
    {"MAX_CONCURRENT_QUERIES": "17"},
    {"MAX_CONCURRENT_QUERIES": "many"},
])
def test_malformed_host_or_limit_is_rejected(environ) -> None:
    with pytest.raises(HostingConfigError):
        load_hosting({"ACCESS_CODE": FAKE_CODE, "APP_SIGNING_KEY": FAKE_KEY, **environ})


def test_explicit_concurrency_limit_is_used() -> None:
    assert load_hosting({"MAX_CONCURRENT_QUERIES": "3"}).max_concurrent_queries == 3
