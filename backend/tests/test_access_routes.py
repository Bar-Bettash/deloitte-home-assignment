"""HTTP behaviour of the access gate, login routes, host and origin checks."""

import pytest
from app import main
from app.access import ACCESS_COOKIE, AccessSigner
from app.model_adapter import ModelInterpretation, ModelUsage
from app.settings import Settings
from fastapi.testclient import TestClient

FAKE_CODE = "fake-access-code-0123456789"
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
def login_page(tmp_path, monkeypatch):
    page = tmp_path / "login.html"
    page.write_text("<!doctype html><title>Access</title><form></form>", encoding="utf-8")
    monkeypatch.setattr(main, "LOGIN_PAGE", page)
    return page


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setenv("ACCESS_CODE", FAKE_CODE)
    monkeypatch.setenv("APP_SIGNING_KEY", FAKE_KEY)
    monkeypatch.setenv("ALLOWED_HOSTS", HOST)


@pytest.fixture
def counters(monkeypatch):
    calls = {"dispatch": 0, "interpret": 0}
    real_dispatch = main.dispatch_analysis

    def counting_dispatch(*args, **kwargs):
        calls["dispatch"] += 1
        return real_dispatch(*args, **kwargs)

    async def counting_interpret(*_args, **_kwargs):
        calls["interpret"] += 1
        raise AssertionError("model must not be reached in these tests")

    admitted = Settings.model_validate({
        "model_api_key": "offline-test-only", "model_name": "fake-model",
        "model_runtime_enabled": True, "model_admitted_name": "fake-model",
        "model_admitted_adapter_sha256": main.ADAPTER_SHA256,
    })
    monkeypatch.setattr(main, "dispatch_analysis", counting_dispatch)
    monkeypatch.setattr(main, "interpret_message", counting_interpret)
    monkeypatch.setattr(main, "load_settings", lambda: admitted)
    return calls


def _client(base_url: str = ORIGIN) -> TestClient:
    return TestClient(main.app, base_url=base_url, follow_redirects=False)


def _login(http: TestClient, code: str = FAKE_CODE, origin: str = ORIGIN):
    return http.post("/api/access", json={"code": code}, headers={"Origin": origin})


def _post_query(http: TestClient, body: dict, origin: str = ORIGIN):
    return http.post("/api/query", json=body, headers={"Origin": origin})


def _tamper(cookie: str) -> str:
    return cookie[:-1] + ("B" if cookie.endswith("A") else "A")


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def test_unauthenticated_requests_are_denied_before_any_work(gated, counters):
    http = _client()
    page = http.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
    assert page.status_code == 303
    assert page.headers["location"] == "/login"
    assert page.headers["cache-control"] == "no-store"

    for path in ("/", "/static/app.js", "/static/index.html", "/static/../static/app.js", "/nonexistent"):
        denied = http.get(path)
        assert denied.status_code == 401, path
        assert _error_code(denied) == "access_required"
    assert http.head("/").status_code == 401
    assert http.get("/static/app.js", headers={"Accept": "text/html"}).status_code == 303

    fake_id = "11111111-1111-4111-8111-111111111111"
    for body in (PRESET, {"message": "Compare LAX and SNA"},
                 {"analysis": {"action": "explain"}, "context_result_id": fake_id},
                 {"message": "Explain", "context_result_id": fake_id}):
        response = _post_query(http, body)
        assert response.status_code == 401
        assert _error_code(response) == "access_required"
        assert response.headers["content-type"].startswith("application/json")
    assert counters == {"dispatch": 0, "interpret": 0}


def test_health_is_open_but_only_at_its_exact_path(gated):
    http = _client()
    assert http.get("/health").json() == {"status": "ok"}
    assert http.head("/health").status_code == 200
    assert http.get("/health/").status_code == 401
    assert http.get("/HEALTH").status_code == 401


def test_login_page_is_exempt_and_never_cached(gated, login_page):
    http = _client()
    page = http.get("/login")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    assert "<form>" in page.text
    assert http.head("/login").status_code == 200
    assert http.get("/login/").status_code == 401


def test_wrong_code_is_denied_without_cookie(gated):
    http = _client()
    for code in ("wrong-code-0123456789-xyz", FAKE_CODE.upper(), FAKE_CODE + " ", "x"):
        denied = _login(http, code)
        assert denied.status_code == 401
        assert _error_code(denied) == "access_denied"
        assert "set-cookie" not in denied.headers
        assert FAKE_CODE not in denied.text


@pytest.mark.parametrize("body,status,code", [
    ({"code": ""}, 422, "invalid_request"),
    ({"code": 123}, 422, "invalid_request"),
    ({"code": FAKE_CODE, "extra": 1}, 422, "invalid_request"),
    ({"code": "x" * 257}, 422, "invalid_request"),
    (["code"], 422, "invalid_request"),
])
def test_malformed_access_bodies_are_rejected(gated, body, status, code):
    response = _client().post("/api/access", json=body, headers={"Origin": ORIGIN})
    assert response.status_code == status
    assert _error_code(response) == code


def test_access_requires_json_and_bounded_body(gated):
    http = _client()
    wrong_type = http.post("/api/access", content=f"code={FAKE_CODE}",
                           headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"})
    assert wrong_type.status_code == 415
    too_big = http.post("/api/access", content=b"{" + b" " * 2048 + b"}",
                        headers={"Origin": ORIGIN, "Content-Type": "application/json"})
    assert too_big.status_code == 413
    bad_json = http.post("/api/access", content=b"{bad", headers={"Origin": ORIGIN, "Content-Type": "application/json"})
    assert bad_json.status_code == 400


def test_right_code_grants_lax_http_only_cookie_and_unlocks_presets(gated, login_page):
    http = _client()
    granted = _login(http)
    assert granted.status_code == 204
    set_cookie = granted.headers["set-cookie"].lower()
    assert set_cookie.startswith(f"{ACCESS_COOKIE}=v1.")
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "max-age=28800" in set_cookie
    assert "secure" not in set_cookie
    assert FAKE_CODE.lower() not in set_cookie

    home = http.get("/", headers={"Accept": "text/html"})
    assert home.status_code == 200
    assert home.headers["cache-control"] == "no-store"
    assert http.get("/static/app.js").status_code == 200

    result = _post_query(http, PRESET)
    assert result.status_code == 200, result.text
    assert result.json()["scope"]["bundle_id"] == "annual-2025-r1"
    assert result.headers["cache-control"] == "no-store"

    explained = _post_query(http, {"analysis": {"action": "explain"}, "context_result_id": result.json()["result_id"]})
    assert explained.status_code == 200, explained.text
    assert explained.json()["rows"] == result.json()["rows"]


def test_logout_clears_access_and_context(gated):
    http = _client()
    assert _login(http).status_code == 204
    assert _post_query(http, PRESET).status_code == 200
    out = http.post("/api/logout", headers={"Origin": ORIGIN})
    assert out.status_code == 204
    cleared = out.headers.get_list("set-cookie")
    assert any(item.startswith(f"{ACCESS_COOKIE}=") and "max-age=0" in item.lower() for item in cleared)
    assert any(item.startswith(f"{main.CONTEXT_COOKIE}=") and "max-age=0" in item.lower() for item in cleared)
    assert _post_query(http, PRESET).status_code == 401


@pytest.mark.parametrize("cookie_factory", [
    lambda: AccessSigner(FAKE_KEY.encode(), FAKE_CODE, clock=lambda: 0).issue(),  # expired
    lambda: AccessSigner(b"z" * 32, FAKE_CODE).issue(),  # other key
    lambda: AccessSigner(FAKE_KEY.encode(), "previous-code-0123456789").issue(),  # before rotation
    lambda: _tamper(AccessSigner(FAKE_KEY.encode(), FAKE_CODE).issue()),
    lambda: "v1.9999999999.forged",
])
def test_invalid_access_cookies_are_denied(gated, cookie_factory):
    http = _client()
    http.cookies.set(ACCESS_COOKIE, cookie_factory(), domain=HOST)
    response = _post_query(http, PRESET)
    assert response.status_code == 401
    assert _error_code(response) == "access_required"


@pytest.mark.parametrize("headers", [
    {},
    {"Origin": "null"},
    {"Origin": "http://evil.example.com"},
    {"Origin": f"http://{HOST}:8443"},
    {"Origin": f"{ORIGIN}/path"},
    {"Origin": f"http://user@{HOST}"},
    {"Origin": "ftp://demo.example.com"},
])
def test_gated_posts_require_exact_same_origin(gated, headers):
    http = _client()
    for path, body in (("/api/access", {"code": FAKE_CODE}), ("/api/query", PRESET), ("/api/logout", None)):
        response = http.post(path, json=body, headers=headers)
        assert response.status_code == 400, (path, headers)
        assert _error_code(response) == "invalid_request"
        assert "set-cookie" not in response.headers


def test_unlisted_host_is_rejected(gated):
    http = TestClient(main.app, base_url="http://attacker.example.net")
    assert http.get("/login").status_code == 400
    assert http.post("/api/access", json={"code": FAKE_CODE},
                     headers={"Origin": "http://attacker.example.net"}).status_code == 400
    assert http.get("/health").status_code == 200


def test_malformed_host_header_is_rejected(gated):
    http = _client()
    assert http.get("/login", headers={"Host": "demo.example.com:notaport"}).status_code == 400
    assert http.get("/login", headers={"Host": "demo.example.com@evil"}).status_code == 400


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_api_docs_are_never_served(gated, path):
    http = _client()
    assert _login(http).status_code == 204
    assert http.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_not_served_locally_either(path):
    assert TestClient(main.app).get(path).status_code == 404


def test_gating_without_secrets_fails_closed_except_health(monkeypatch, login_page):
    monkeypatch.setenv("ALLOWED_HOSTS", HOST)
    http = _client()
    assert http.get("/health").status_code == 200
    for response in (http.get("/"), http.get("/login"), _post_query(http, PRESET), _login(http)):
        assert response.status_code == 503
        assert _error_code(response) == "internal_error"
        assert response.headers["cache-control"] == "no-store"


def test_weak_access_code_fails_closed(monkeypatch):
    monkeypatch.setenv("ACCESS_CODE", "short-code")
    monkeypatch.setenv("APP_SIGNING_KEY", FAKE_KEY)
    response = TestClient(main.app).post("/api/query", json=PRESET, headers={"Origin": "http://testserver"})
    assert response.status_code == 503
    assert "short-code" not in response.text


def test_vercel_mode_uses_secure_cookies_and_https_origin(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_URL", "airport-demo-abc.vercel.app")
    monkeypatch.setenv("ACCESS_CODE", FAKE_CODE)
    monkeypatch.setenv("APP_SIGNING_KEY", FAKE_KEY)
    https_origin = "https://airport-demo-abc.vercel.app"
    http = TestClient(main.app, base_url=https_origin, follow_redirects=False)
    assert _login(http, origin="http://airport-demo-abc.vercel.app").status_code == 400
    granted = _login(http, origin=https_origin)
    assert granted.status_code == 204
    assert "secure" in granted.headers["set-cookie"].lower()
    result = _post_query(http, PRESET, origin=https_origin)
    assert result.status_code == 200, result.text
    assert "secure" in result.headers["set-cookie"].lower()


def test_ungated_loopback_run_needs_no_code(login_page):
    http = TestClient(main.app)
    assert http.get("/", headers={"Accept": "text/html"}).status_code == 200
    assert http.get("/static/app.js").headers.get("cache-control") != "no-store"
    assert http.post("/api/access", json={"code": "anything"}).status_code == 204
    assert "set-cookie" not in http.post("/api/access", json={"code": "anything"}).headers
    assert http.get("/login").status_code == 200
    assert http.post("/api/query", json=PRESET, headers={"Origin": "http://evil.example.com"}).status_code == 400


def test_gated_explain_via_cookie_and_bad_context_cookie_on_preset(gated, monkeypatch):
    http = _client()
    assert _login(http).status_code == 204
    http.cookies.set(main.CONTEXT_COOKIE, "garbage", domain=HOST)
    fresh = _post_query(http, PRESET)
    assert fresh.status_code == 200, fresh.text


def test_gated_model_path_passes_signed_context(gated, monkeypatch):
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
    assert _login(http).status_code == 204
    first = _post_query(http, PRESET)
    followup = _post_query(http, {"message": "Why?", "context_result_id": first.json()["result_id"]})
    assert followup.status_code == 200, followup.text
    assert contexts == [{
        "action": "metric", "airports": ["ANC"], "metric": "long_haul_share", "year": 2025,
        "bundle_id": "annual-2025-r1", "threshold_miles": 3000,
    }]
