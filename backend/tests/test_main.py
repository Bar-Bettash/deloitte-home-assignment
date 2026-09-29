import logging

import pytest
from app import main
from app.main import app
from app.model_adapter import ModelInterpretation, ModelUsage
from app.settings import Settings
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health_returns_exact_typed_response() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_rejects_unsupported_methods() -> None:
    response = client.post("/health")

    assert response.status_code == 405


# Hosted mode: Host allowlist, same-origin POSTs, fail-closed configuration.

FAKE_KEY = "fake-signing-key-for-tests-only-0123456789"
HOST = "demo.example.com"
ORIGIN = f"http://{HOST}"
PRESET = {"analysis": {"action": "metric", "airports": ["ANC"], "metric": "long_haul_share"}}


@pytest.fixture(autouse=True)
def _reset_slots():
    main.query_slots.reset()
    yield
    main.query_slots.reset()


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("APP_SIGNING_KEY", FAKE_KEY)
    monkeypatch.setenv("ALLOWED_HOSTS", HOST)


def _client(base_url: str = ORIGIN) -> TestClient:
    return TestClient(main.app, base_url=base_url, follow_redirects=False)


def _post_query(http: TestClient, body: dict, origin: str = ORIGIN):
    return http.post("/api/query", json=body, headers={"Origin": origin})


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def test_hosted_app_serves_ui_and_presets_without_sign_in(hosted):
    http = _client()
    home = http.get("/", headers={"Accept": "text/html"})
    assert home.status_code == 200
    assert home.headers["content-type"].startswith("text/html")
    assert home.headers["cache-control"] == "no-store"
    assert http.get("/static/app.js").status_code == 200
    assert http.get("/login").status_code == 404

    result = _post_query(http, PRESET)
    assert result.status_code == 200, result.text
    assert result.json()["scope"]["bundle_id"] == "annual-2025-r1"
    assert result.headers["cache-control"] == "no-store"

    explained = _post_query(http, {"analysis": {"action": "explain"}, "context_result_id": result.json()["result_id"]})
    assert explained.status_code == 200, explained.text
    assert explained.json()["rows"] == result.json()["rows"]


def test_health_is_open_even_when_hosting_is_misconfigured(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", HOST)
    http = TestClient(main.app, base_url="http://attacker.example.net")
    assert http.get("/health").json() == {"status": "ok"}
    assert http.head("/health").status_code == 200


@pytest.mark.parametrize("headers", [
    {},
    {"Origin": "null"},
    {"Origin": "http://evil.example.com"},
    {"Origin": f"http://{HOST}:8443"},
    {"Origin": f"{ORIGIN}/path"},
    {"Origin": f"http://user@{HOST}"},
    {"Origin": "ftp://demo.example.com"},
])
def test_hosted_posts_require_exact_same_origin(hosted, headers):
    response = _client().post("/api/query", json=PRESET, headers=headers)
    assert response.status_code == 400, headers
    assert _error_code(response) == "invalid_request"
    assert "set-cookie" not in response.headers


def test_unlisted_host_is_rejected(hosted):
    http = TestClient(main.app, base_url="http://attacker.example.net")
    assert http.get("/").status_code == 400
    assert _post_query(http, PRESET, origin="http://attacker.example.net").status_code == 400
    assert http.get("/health").status_code == 200


@pytest.mark.parametrize("base_url", ["http://localhost", "http://testserver", "http://127.0.0.1"])
def test_loopback_hosts_are_rejected_when_hosted(hosted, base_url):
    http = TestClient(main.app, base_url=base_url)
    assert http.get("/").status_code == 400
    assert http.get("/health").status_code == 200


def test_app_logger_emits_info():
    assert logging.getLogger("app.main").isEnabledFor(logging.INFO)


def test_malformed_host_header_is_rejected(hosted):
    http = _client()
    assert http.get("/", headers={"Host": "demo.example.com:notaport"}).status_code == 400
    assert http.get("/", headers={"Host": "demo.example.com@evil"}).status_code == 400


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_api_docs_are_never_served_hosted(hosted, path):
    assert _client().get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_not_served_locally_either(path):
    assert TestClient(main.app).get(path).status_code == 404


def test_hosting_without_signing_key_fails_closed_except_health(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", HOST)
    http = _client()
    assert http.get("/health").status_code == 200
    for response in (http.get("/"), http.get("/static/app.js"), _post_query(http, PRESET)):
        assert response.status_code == 503
        assert _error_code(response) == "internal_error"
        assert response.headers["cache-control"] == "no-store"


def test_short_signing_key_fails_closed(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", HOST)
    monkeypatch.setenv("APP_SIGNING_KEY", "short-key")
    response = _post_query(_client(), PRESET)
    assert response.status_code == 503
    assert "short-key" not in response.text


def test_vercel_mode_uses_secure_cookies_and_https_origin(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_URL", "airport-demo-abc.vercel.app")
    monkeypatch.setenv("APP_SIGNING_KEY", FAKE_KEY)
    https_origin = "https://airport-demo-abc.vercel.app"
    http = TestClient(main.app, base_url=https_origin, follow_redirects=False)
    assert _post_query(http, PRESET, origin="http://airport-demo-abc.vercel.app").status_code == 400
    result = _post_query(http, PRESET, origin=https_origin)
    assert result.status_code == 200, result.text
    assert "secure" in result.headers["set-cookie"].lower()


def test_local_loopback_run_needs_no_configuration():
    http = TestClient(main.app)
    assert http.get("/", headers={"Accept": "text/html"}).status_code == 200
    assert http.get("/static/app.js").headers.get("cache-control") != "no-store"
    assert http.post("/api/query", json=PRESET).status_code == 200
    assert http.post("/api/query", json=PRESET, headers={"Origin": "http://evil.example.com"}).status_code == 400


def test_bad_context_cookie_does_not_block_an_independent_preset(hosted):
    http = _client()
    http.cookies.set(main.CONTEXT_COOKIE, "garbage", domain=HOST)
    fresh = _post_query(http, PRESET)
    assert fresh.status_code == 200, fresh.text


def test_hosted_model_path_passes_signed_context(hosted, monkeypatch):
    admitted = Settings.model_validate({
        "model_api_key": "offline-test-only", "model_name": "fake-model",
        "model_runtime_enabled": True, "model_admitted_name": "fake-model",
        "model_admitted_adapter_sha256": main.ADAPTER_SHA256,
    })
    monkeypatch.setattr(main, "load_settings", lambda: admitted)
    contexts = []

    async def interpret(_message, *, settings, context):
        contexts.append(context)
        return ModelInterpretation("analysis", main.AnalysisRequest(action="explain"), None, ModelUsage(10, 5))

    monkeypatch.setattr(main, "interpret_message", interpret)
    http = _client()
    first = _post_query(http, PRESET)
    followup = _post_query(http, {"message": "Why?", "context_result_id": first.json()["result_id"]})
    assert followup.status_code == 200, followup.text
    assert contexts == [{
        "action": "metric", "airports": ["ANC"], "metric": "long_haul_share", "year": 2025,
        "bundle_id": "annual-2025-r1", "threshold_miles": 3000,
    }]
