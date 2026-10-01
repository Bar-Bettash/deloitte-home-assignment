"""Shared-password access gate: token unit tests and the HTTP behaviour of the gate."""

import logging

import pytest
from app import main
from app.access import ACCESS_COOKIE, ACCESS_TTL_SECONDS, AccessSigner, AccessTokenError, password_matches
from app.context_token import ContextSigner, ContextTokenError
from fastapi.testclient import TestClient

KEY = b"k" * 32
FAKE_KEY = "fake-signing-key-for-tests-only-0123456789"
FAKE_PASSWORD = "test-access-password-only"
HOST = "demo.example.com"
ORIGIN = f"http://{HOST}"
PRESET = {"analysis": {"action": "metric", "airports": ["ANC"], "metric": "long_haul_share"}}


# --- Token unit tests -------------------------------------------------------

def test_issued_token_verifies_and_reveals_nothing_about_the_password():
    signer = AccessSigner(KEY, FAKE_PASSWORD, clock=lambda: 1_000)
    token = signer.issue()
    assert signer.verify(token) == 1_000 + ACCESS_TTL_SECONDS
    assert FAKE_PASSWORD not in token
    assert token.startswith("a1.")


def test_changing_the_password_invalidates_issued_tokens():
    token = AccessSigner(KEY, FAKE_PASSWORD).issue()
    with pytest.raises(AccessTokenError):
        AccessSigner(KEY, "a-new-access-password").verify(token)


def test_changing_the_signing_key_invalidates_issued_tokens():
    token = AccessSigner(KEY, FAKE_PASSWORD).issue()
    with pytest.raises(AccessTokenError):
        AccessSigner(b"j" * 32, FAKE_PASSWORD).verify(token)


def test_expired_token_is_rejected():
    token = AccessSigner(KEY, FAKE_PASSWORD, clock=lambda: 1_000).issue()
    later = AccessSigner(KEY, FAKE_PASSWORD, clock=lambda: 1_000 + ACCESS_TTL_SECONDS)
    with pytest.raises(AccessTokenError):
        later.verify(token)


def _tampered_expiry(token: str) -> str:
    version, expiry, mac = token.split(".")
    return f"{version}.{int(expiry) + 1}.{mac}"


@pytest.mark.parametrize("mutate", [
    lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"),
    _tampered_expiry,
    lambda t: "a2" + t[2:],
    lambda t: t + ".x",
    lambda t: "",
    lambda t: None,
    lambda t: 42,
    lambda t: "a1." + "9" * 300 + ".x",
    lambda t: "a1.-5.x",
    lambda t: "a1.1e9.x",
    lambda t: t.replace("a1.", "a1.é"),
])
def test_forged_or_malformed_tokens_are_rejected(mutate):
    signer = AccessSigner(KEY, FAKE_PASSWORD)
    with pytest.raises(AccessTokenError):
        signer.verify(mutate(signer.issue()))


def test_access_and_context_tokens_cannot_be_swapped():
    """Same key material, separate purposes: neither token verifies as the other."""
    access = AccessSigner(KEY, FAKE_PASSWORD)
    context = ContextSigner(KEY)
    with pytest.raises(ContextTokenError):
        context.verify(access.issue())
    with pytest.raises(AccessTokenError):
        access.verify("v1.e30.AAAA")


def test_password_comparison():
    assert password_matches(FAKE_PASSWORD, FAKE_PASSWORD)
    assert not password_matches("", FAKE_PASSWORD)
    assert not password_matches(FAKE_PASSWORD + " ", FAKE_PASSWORD)
    assert not password_matches(FAKE_PASSWORD.upper(), FAKE_PASSWORD)


def test_signer_refuses_weak_inputs():
    with pytest.raises(ValueError):
        AccessSigner(b"short", FAKE_PASSWORD)
    with pytest.raises(ValueError):
        AccessSigner(KEY, "")


# --- HTTP gate ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_slots():
    main.query_slots.reset()
    yield
    main.query_slots.reset()


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("APP_SIGNING_KEY", FAKE_KEY)
    monkeypatch.setenv("APP_ACCESS_PASSWORD", FAKE_PASSWORD)
    monkeypatch.setenv("ALLOWED_HOSTS", HOST)
    monkeypatch.setattr(main, "FAILED_SIGN_IN_DELAY_SECONDS", 0)


def _client(base_url: str = ORIGIN) -> TestClient:
    return TestClient(main.app, base_url=base_url, follow_redirects=False)


def _sign_in(http: TestClient, password: str = FAKE_PASSWORD, origin: str = ORIGIN):
    return http.post("/auth/login", data={"password": password}, headers={"Origin": origin})


def _query(http: TestClient, body: dict):
    return http.post("/api/query", json=body, headers={"Origin": ORIGIN})


def _assert_access_required(response):
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "access_required"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


def test_unauthenticated_visitor_gets_the_sign_in_page_not_the_app(hosted):
    http = _client()
    home = http.get("/", headers={"Accept": "text/html"})
    assert home.status_code == 303
    assert home.headers["location"] == "/login"
    assert home.headers["cache-control"] == "no-store"
    page = http.get("/login")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert '<label for="password">' in page.text
    assert 'action="/auth/login"' in page.text
    assert "<!--LOGIN_ERROR-->" in page.text and 'role="alert"' not in page.text
    assert "<script" not in page.text
    assert http.get("/static/login.css").status_code == 200
    assert http.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/static/app.js", "/static/globe.js", "/static/styles.css", "/static/index.html",
                                  "/static/login.html", "/static/assets/airport-coordinates.json"])
def test_application_assets_need_access(hosted, path):
    _assert_access_required(_client().get(path))


def test_api_cannot_be_called_without_access(hosted):
    http = _client()
    _assert_access_required(_query(http, PRESET))
    _assert_access_required(_query(http, {"message": "What is the long-haul share at ANC?"}))
    _assert_access_required(_query(http, {"analysis": {"action": "explain"},
                                          "context_result_id": "11111111-1111-4111-8111-111111111111"}))


def test_a_context_cookie_alone_does_not_grant_access(hosted):
    http = _client()
    assert _sign_in(http).status_code == 303
    result = _query(http, PRESET)
    assert result.status_code == 200
    http.cookies.delete(ACCESS_COOKIE)
    _assert_access_required(_query(http, {"analysis": {"action": "explain"}, "context_result_id": result.json()["result_id"]}))


def test_correct_password_issues_a_strict_finite_cookie_and_unlocks_the_app(hosted):
    http = _client()
    response = _sign_in(http)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    flags = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert cookie.startswith(f"{ACCESS_COOKIE}=a1.")
    assert {"httponly", "samesite=strict", "path=/", f"max-age={ACCESS_TTL_SECONDS}"} <= flags
    assert FAKE_PASSWORD not in cookie
    assert http.get("/").status_code == 200
    preset = _query(http, PRESET)
    assert preset.status_code == 200
    explained = _query(http, {"analysis": {"action": "explain"}, "context_result_id": preset.json()["result_id"]})
    assert explained.status_code == 200


def test_vercel_access_cookie_is_secure(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_URL", "airport-demo-abc.vercel.app")
    monkeypatch.setenv("APP_SIGNING_KEY", FAKE_KEY)
    monkeypatch.setenv("APP_ACCESS_PASSWORD", FAKE_PASSWORD)
    origin = "https://airport-demo-abc.vercel.app"
    response = _sign_in(_client(origin), origin=origin)
    assert response.status_code == 303
    assert "secure" in {part.strip().lower() for part in response.headers["set-cookie"].split(";")}


@pytest.mark.parametrize("password", ["wrong-password-12345", "", FAKE_PASSWORD + " ", FAKE_PASSWORD[:-1]])
def test_wrong_password_is_refused_generically(hosted, password):
    http = _client()
    response = _sign_in(http, password)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert 'role="alert"' in response.text and 'aria-invalid="true"' in response.text
    assert "That password is not correct" in response.text
    assert FAKE_PASSWORD not in response.text
    if password:
        assert password not in response.text
    _assert_access_required(_query(http, PRESET))


@pytest.mark.parametrize("body,content_type", [
    (b"", "application/x-www-form-urlencoded"),
    (b"pass=abc", "application/x-www-form-urlencoded"),
    (b"password=a&password=b", "application/x-www-form-urlencoded"),
    (b"password=%ff%fe", "application/x-www-form-urlencoded"),
    (b"\xff\xfe", "application/x-www-form-urlencoded"),
    (b"a=1&b=2&c=3&d=4&password=x", "application/x-www-form-urlencoded"),
    (b'{"password": "test-access-password-only"}', "application/json"),
    (b"password=test-access-password-only", "text/plain"),
])
def test_malformed_sign_in_is_rejected(hosted, body, content_type):
    response = _client().post("/auth/login", content=body, headers={"Origin": ORIGIN, "Content-Type": content_type})
    assert response.status_code == 400
    assert "set-cookie" not in response.headers
    assert FAKE_PASSWORD not in response.text


def test_oversized_sign_in_is_rejected(hosted):
    body = b"password=" + b"x" * 2000
    response = _client().post("/auth/login", content=body,
                              headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 413
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://evil.example.com"}, {"Origin": "null"}])
def test_sign_in_and_sign_out_require_same_origin(hosted, headers):
    http = _client()
    assert http.post("/auth/login", data={"password": FAKE_PASSWORD}, headers=headers).status_code == 400
    assert http.post("/auth/logout", headers=headers).status_code == 400


def test_modified_or_expired_access_cookie_is_refused(hosted):
    http = _client()
    _sign_in(http)
    token = http.cookies.get(ACCESS_COOKIE)
    http.cookies.set(ACCESS_COOKIE, token[:-2] + "AA", domain=HOST)
    _assert_access_required(_query(http, PRESET))
    expired = AccessSigner(FAKE_KEY.encode(), FAKE_PASSWORD, clock=lambda: 0).issue()
    http.cookies.set(ACCESS_COOKIE, expired, domain=HOST)
    _assert_access_required(_query(http, PRESET))
    assert http.get("/").headers["location"] == "/login"


def test_sign_out_clears_both_cookies_and_closes_the_app(hosted):
    http = _client()
    _sign_in(http)
    assert _query(http, PRESET).status_code == 200
    response = http.post("/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    cleared = response.headers.get_list("set-cookie")
    assert any(c.startswith(f"{ACCESS_COOKIE}=") and "max-age=0" in c.lower() for c in cleared)
    assert any(c.startswith(f"{main.CONTEXT_COOKIE}=") and "max-age=0" in c.lower() for c in cleared)
    _assert_access_required(_query(http, PRESET))


def test_rotating_the_password_invalidates_existing_sessions(hosted, monkeypatch):
    http = _client()
    _sign_in(http)
    assert _query(http, PRESET).status_code == 200
    monkeypatch.setenv("APP_ACCESS_PASSWORD", "a-rotated-access-password")
    _assert_access_required(_query(http, PRESET))
    assert _sign_in(http).status_code == 401
    assert _sign_in(http, "a-rotated-access-password").status_code == 303
    assert _query(http, PRESET).status_code == 200


def test_hosted_without_access_password_fails_closed_except_health(monkeypatch):
    monkeypatch.setenv("APP_SIGNING_KEY", FAKE_KEY)
    monkeypatch.setenv("ALLOWED_HOSTS", HOST)
    http = _client()
    assert http.get("/health").status_code == 200
    for response in (http.get("/"), http.get("/login"), _sign_in(http), _query(http, PRESET)):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "internal_error"


def test_password_is_never_logged_or_served(hosted, caplog):
    http = _client()
    with caplog.at_level(logging.DEBUG):
        _sign_in(http, "wrong-password-12345")
        _sign_in(http)
        pages = [http.get("/").text, http.get("/static/app.js").text, http.get("/login").text,
                 _query(http, PRESET).text]
    assert "access sign-in failed" in caplog.text and "access sign-in succeeded" in caplog.text
    assert FAKE_PASSWORD not in caplog.text and "wrong-password-12345" not in caplog.text
    assert all(FAKE_PASSWORD not in page for page in pages)


def test_local_run_enforces_the_gate_when_a_password_is_set(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_PASSWORD", FAKE_PASSWORD)
    monkeypatch.setattr(main, "FAILED_SIGN_IN_DELAY_SECONDS", 0)
    http = TestClient(main.app, follow_redirects=False)
    assert http.get("/").headers["location"] == "/login"
    assert http.post("/api/query", json=PRESET).status_code == 401
    assert http.post("/auth/login", data={"password": FAKE_PASSWORD}).status_code == 303
    assert http.post("/api/query", json=PRESET).status_code == 200


def test_local_run_without_a_password_has_no_gate():
    http = TestClient(main.app, follow_redirects=False)
    assert http.get("/login").headers["location"] == "/"
    assert http.post("/auth/login", data={"password": "anything"}).headers["location"] == "/"
    assert http.post("/api/query", json=PRESET).status_code == 200
