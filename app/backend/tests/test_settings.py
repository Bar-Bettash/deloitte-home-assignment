import pytest
from app.settings import (
    DEFAULT_GEMINI_MODEL,
    LOCAL_ENV_FILE,
    HostingConfigError,
    Settings,
    load_hosting,
    load_local_env,
    load_settings,
)
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
    assert settings.model_max_output_tokens == 1024
    assert settings.model_max_prompt_tokens == 8000
    assert settings.model_name == DEFAULT_GEMINI_MODEL == "gemini-3.8-flash"
    assert settings.model_thinking_budget == 0
    assert settings.model_access_available is False
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
        "GEMINI_API_KEY": "  test-key-value  ",
        "GEMINI_MODEL": "gemini-test.model-v1",
        "GEMINI_THINKING_BUDGET": "256",
    })

    assert settings.query_timeout_seconds == 25
    assert settings.source_max_pages == 3
    assert settings.model_access_available is True
    assert settings.model_api_key.get_secret_value() == "test-key-value"
    assert settings.model_name == "gemini-test.model-v1"
    assert settings.model_thinking_budget == 256
    assert settings.model_runtime_admitted("a" * 64) is False


def test_model_access_means_a_key_is_set_not_that_runtime_is_admitted() -> None:
    assert load_settings({"GEMINI_API_KEY": "fake-key"}).model_access_available is True
    assert load_settings({"GEMINI_API_KEY": "fake-key"}).model_runtime_admitted("a" * 64) is False
    assert load_settings({"GEMINI_API_KEY": "   "}).model_access_available is False
    assert load_settings({"GEMINI_MODEL": "gemini-2.5-pro"}).model_access_available is False


_ADMITTED = {
    "GEMINI_API_KEY": "fake-key",
    "GEMINI_MODEL": "demo.model-v1",
    "MODEL_RUNTIME_ENABLED": "true",
    "MODEL_ADMITTED_NAME": "demo.model-v1",
    "MODEL_ADMITTED_ADAPTER_SHA256": "a" * 64,
}


def test_exact_runtime_admission_requires_explicit_model_and_adapter() -> None:
    admitted = load_settings({**_ADMITTED, "MODEL_RUNTIME_ENABLED": "TrUe"})
    assert admitted.model_runtime_admitted("a" * 64) is True
    assert admitted.model_runtime_admitted("b" * 64) is False
    assert admitted.model_runtime_enabled is True


@pytest.mark.parametrize("missing", [
    "GEMINI_API_KEY", "MODEL_RUNTIME_ENABLED", "MODEL_ADMITTED_NAME", "MODEL_ADMITTED_ADAPTER_SHA256",
])
def test_runtime_admission_fails_closed_when_any_field_is_missing(missing: str) -> None:
    values = dict(_ADMITTED)
    values.pop(missing)
    assert load_settings(values).model_runtime_admitted("a" * 64) is False


def test_runtime_admission_requires_exact_model_name() -> None:
    settings = load_settings({**_ADMITTED, "MODEL_ADMITTED_NAME": "demo.model-v2"})
    assert settings.model_access_available is True
    assert settings.model_runtime_admitted("a" * 64) is False


def test_admitted_name_is_compared_with_the_effective_default_model() -> None:
    values = {**_ADMITTED, "MODEL_ADMITTED_NAME": DEFAULT_GEMINI_MODEL}
    values.pop("GEMINI_MODEL")
    assert load_settings(values).model_runtime_admitted("a" * 64) is True
    values["GEMINI_MODEL"] = "gemini-other"
    assert load_settings(values).model_runtime_admitted("a" * 64) is False


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


@pytest.mark.parametrize(("raw", "expected"), [("", None), ("default", None), (" Default ", None), ("0", 0), ("1024", 1024)])
def test_thinking_budget_parsing(raw: str, expected: int | None) -> None:
    assert load_settings({"GEMINI_THINKING_BUDGET": raw}).model_thinking_budget == expected


def test_blank_model_name_falls_back_to_default() -> None:
    assert load_settings({"GEMINI_MODEL": "  "}).model_name == DEFAULT_GEMINI_MODEL


def test_removed_openai_settings_are_not_read() -> None:
    settings = load_settings({
        "OPENAI_API_KEY": "not-read", "OPENAI_MODEL": "not-read", "MODEL_REASONING_EFFORT": "low",
    })
    assert settings.model_access_available is False
    assert settings.model_name == DEFAULT_GEMINI_MODEL
    assert not hasattr(settings, "model_reasoning_effort")


@pytest.mark.parametrize(
    "overrides",
    [
        {"QUERY_TIMEOUT_SECONDS": "not-a-number"},
        {"QUERY_TIMEOUT_SECONDS": "31"},
        {"SOURCE_MAX_PAGES": "5"},
        {"SOURCE_PAGE_SIZE": "5001"},
        {"MODEL_TIMEOUT_SECONDS": "21"},
        {"GEMINI_MODEL": "unsafe model name"},
        {"GEMINI_MODEL": "models/../x y"},
        {"GEMINI_API_KEY": "invalid\nkey"},
        {"GEMINI_THINKING_BUDGET": "-1"},
        {"GEMINI_THINKING_BUDGET": "lots"},
        {"GEMINI_THINKING_BUDGET": "99999"},
    ],
)
def test_malformed_or_unsafe_environment_values_are_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        load_settings(overrides)


def _env_file(tmp_path, text: str):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_local_env_file_is_loaded_without_overriding_the_shell(tmp_path) -> None:
    path = _env_file(tmp_path, (
        "# comment\n"
        "GEMINI_API_KEY=\"quoted-key\"\n"
        "export GEMINI_MODEL=gemini-x # trailing note\n"
        "EMPTY=\n"
        "SINGLE='a # b'\n"
        "not a variable line\n"
        "APP_SIGNING_KEY=from-file\n"
    ))
    environ = {"APP_SIGNING_KEY": "from-shell"}
    loaded = load_local_env(path, environ)
    assert loaded == ["GEMINI_API_KEY", "GEMINI_MODEL", "SINGLE"]
    assert environ == {
        "APP_SIGNING_KEY": "from-shell", "GEMINI_API_KEY": "quoted-key",
        "GEMINI_MODEL": "gemini-x", "SINGLE": "a # b",
    }


@pytest.mark.parametrize("marker", ["VERCEL", "APP_NO_DOTENV"])
def test_local_env_file_is_ignored_on_vercel_and_in_tests(tmp_path, marker: str) -> None:
    path = _env_file(tmp_path, "GEMINI_API_KEY=abc\n")
    environ = {marker: "1"}
    assert load_local_env(path, environ) == []
    assert "GEMINI_API_KEY" not in environ


def test_missing_local_env_file_is_a_no_op(tmp_path) -> None:
    environ: dict[str, str] = {}
    assert load_local_env(tmp_path / "missing.env", environ) == []
    assert environ == {}


def test_default_local_env_path_is_the_backend_folder() -> None:
    assert LOCAL_ENV_FILE.name == ".env"
    assert LOCAL_ENV_FILE.parent.name == "backend"


def test_removed_spending_settings_are_not_read() -> None:
    settings = load_settings({
        "MODEL_REQUEST_BUDGET_USD": "not-read",
        "MODEL_PROCESS_BUDGET_USD": "not-read",
        "MODEL_INPUT_USD_PER_MILLION_TOKENS": "not-read",
    })
    assert not hasattr(settings, "model_request_budget_usd")
    assert not hasattr(settings, "model_input_usd_per_million_tokens")


FAKE_KEY = "fake-signing-key-for-tests-only-0123456789"


def test_plain_loopback_run_is_local_with_one_query_slot() -> None:
    hosting = load_hosting({})
    assert hosting.hosted is False
    assert hosting.on_vercel is False
    assert hosting.secure_cookies is False
    assert hosting.max_concurrent_queries == 1
    assert hosting.allowed_hosts == frozenset()


def test_local_signing_key_alone_does_not_make_a_run_hosted() -> None:
    assert load_hosting({"APP_SIGNING_KEY": FAKE_KEY}).hosted is False


@pytest.mark.parametrize("environ", [
    {"ALLOWED_HOSTS": "demo.example.com"},
    {"VERCEL": "1"},
])
def test_hosted_mode_without_signing_key_fails_closed(environ) -> None:
    with pytest.raises(HostingConfigError):
        load_hosting(environ)


@pytest.mark.parametrize("key", ["k" * 31, "k" * 513])
def test_short_or_oversized_signing_key_is_rejected(key) -> None:
    with pytest.raises(HostingConfigError) as caught:
        load_hosting({"ALLOWED_HOSTS": "demo.example.com", "APP_SIGNING_KEY": key})
    assert key not in str(caught.value)


def test_hosted_config_collects_allowed_and_vercel_hosts_and_defaults_to_four_slots() -> None:
    hosting = load_hosting({
        "VERCEL": "1",
        "APP_SIGNING_KEY": FAKE_KEY,
        "ALLOWED_HOSTS": " Demo.Example.com ,other.example.org",
        "VERCEL_URL": "app-abc123.vercel.app",
        "VERCEL_BRANCH_URL": "https://app-git-main.vercel.app/",
        "VERCEL_PROJECT_PRODUCTION_URL": "",
    })
    assert hosting.hosted and hosting.on_vercel and hosting.secure_cookies
    assert hosting.allowed_hosts == {
        "demo.example.com", "other.example.org", "app-abc123.vercel.app", "app-git-main.vercel.app",
    }
    assert hosting.max_concurrent_queries == 4
    assert FAKE_KEY not in repr(hosting)


def test_vercel_hosts_are_ignored_off_vercel() -> None:
    hosting = load_hosting({"APP_SIGNING_KEY": FAKE_KEY, "VERCEL_URL": "x.vercel.app"})
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
        load_hosting({"APP_SIGNING_KEY": FAKE_KEY, **environ})


def test_explicit_concurrency_limit_is_used() -> None:
    assert load_hosting({"MAX_CONCURRENT_QUERIES": "3"}).max_concurrent_queries == 3
