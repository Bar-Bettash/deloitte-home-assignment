"""Shared-password access gate for the hosted demo.

This is a demo access gate, not user authentication: there is one shared
password (``APP_ACCESS_PASSWORD``), no accounts and no server-side session
store. A correct password earns a signed, expiring ``airport_access`` cookie.

Token format: ``a1.<expiry unix seconds>.<b64url(HMAC-SHA256)>``. The MAC key is
derived from ``APP_SIGNING_KEY`` with its own label, so an access token can never
be confused with an ``airport_context`` token. The MAC also covers a digest of
the current password, so changing ``APP_ACCESS_PASSWORD`` invalidates every
access cookie issued under the old one. The token carries nothing about the
password itself. Every failure raises the same ``AccessTokenError``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Callable

VERSION = "a1"
ACCESS_COOKIE = "airport_access"
ACCESS_TTL_SECONDS = 8 * 3600
MAX_TOKEN_BYTES = 256
_KEY_LABEL = b"airport-access-key|v1"
_PASSWORD_LABEL = b"airport-access-password|v1|"


class AccessTokenError(ValueError):
    """The access token is missing, malformed, forged, expired, or from an old password."""


def _password_digest(password: str) -> bytes:
    return hashlib.sha256(_PASSWORD_LABEL + password.encode("utf-8")).digest()


def password_matches(candidate: str, password: str) -> bool:
    """Constant-time comparison of fixed-length digests, so length is not leaked either."""
    return hmac.compare_digest(_password_digest(candidate), _password_digest(password))


class AccessSigner:
    """Issue and verify access tokens bound to one signing key and one password."""

    def __init__(
        self,
        signing_key: bytes,
        password: str,
        *,
        ttl_seconds: int = ACCESS_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("access signing key must be at least 32 bytes")
        if not password:
            raise ValueError("access password is required")
        self._key = hmac.new(signing_key, _KEY_LABEL, hashlib.sha256).digest()
        self._password_tag = _password_digest(password)
        self._ttl = ttl_seconds
        self._clock = clock

    def _mac(self, expiry: str) -> str:
        message = f"{VERSION}|{expiry}|".encode("ascii") + self._password_tag
        return base64.urlsafe_b64encode(hmac.new(self._key, message, hashlib.sha256).digest()).rstrip(b"=").decode("ascii")

    def issue(self) -> str:
        expiry = str(int(self._clock()) + self._ttl)
        return f"{VERSION}.{expiry}.{self._mac(expiry)}"

    def verify(self, token: object) -> int:
        """Return the token's expiry, or raise AccessTokenError."""
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_BYTES or not token.isascii():
            raise AccessTokenError("invalid access token")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != VERSION or not parts[1].isdigit() or len(parts[1]) > 12:
            raise AccessTokenError("invalid access token")
        if not hmac.compare_digest(self._mac(parts[1]), parts[2]):
            raise AccessTokenError("invalid access token")
        expiry = int(parts[1])
        if expiry <= int(self._clock()):
            raise AccessTokenError("invalid access token")
        return expiry
