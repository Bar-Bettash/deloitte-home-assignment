import typing

import pytest
from app.access import AccessSigner, code_matches
from app.contracts import ErrorCode

KEY = b"a" * 32
CODE = "fake-access-code-0123456789"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def test_code_comparison_is_exact():
    assert code_matches(CODE, CODE)
    assert not code_matches(CODE + "x", CODE)
    assert not code_matches(CODE.upper(), CODE)
    assert not code_matches("", CODE)
    assert not code_matches(None, CODE)
    assert not code_matches(123, CODE)
    assert not code_matches("x" * 257, CODE)


def test_cookie_round_trip_and_expiry():
    clock = Clock()
    signer = AccessSigner(KEY, CODE, ttl_seconds=100, clock=clock)
    cookie = signer.issue()
    assert cookie.startswith("v1.1000100.")
    assert CODE not in cookie
    assert signer.verify(cookie)
    clock.now += 99
    assert signer.verify(cookie)
    clock.now += 1
    assert not signer.verify(cookie)


def test_cookie_is_bound_to_key_and_code():
    cookie = AccessSigner(KEY, CODE).issue()
    assert AccessSigner(KEY, CODE).verify(cookie)
    assert not AccessSigner(b"b" * 32, CODE).verify(cookie)
    assert not AccessSigner(KEY, CODE + "-rotated").verify(cookie)


def test_extending_expiry_breaks_the_mac():
    clock = Clock()
    signer = AccessSigner(KEY, CODE, clock=clock)
    _, expiry, mac = signer.issue().split(".")
    assert not signer.verify(f"v1.{int(expiry) + 10_000}.{mac}")


@pytest.mark.parametrize("cookie", [
    None, "", "v1", "v1.1.2.3", "v2.9999999999.x", "v1.-5.x", "v1.0999.x", "v1.abc.x",
    "v1.9999999999." + "A" * 43, "é", "v1." + "9" * 300 + ".x",
])
def test_malformed_cookies_are_rejected(cookie):
    assert not AccessSigner(KEY, CODE).verify(cookie)


def test_constructor_rejects_short_key_or_empty_code():
    with pytest.raises(ValueError):
        AccessSigner(b"short", CODE)
    with pytest.raises(ValueError):
        AccessSigner(KEY, "")


def test_error_codes_add_access_and_drop_budget_and_rate_limit():
    codes = set(typing.get_args(ErrorCode))
    assert {"access_required", "access_denied"} <= codes
    assert "budget_exhausted" not in codes
    assert "rate_limited" not in codes
