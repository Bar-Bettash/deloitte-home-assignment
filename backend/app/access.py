"""Shared access code and the signed cookie that proves it was entered.

The access code is the only defence against strangers spending model budget or
compute, so gated mode requires at least 20 random characters (see settings).
There is deliberately no per-instance login limiter: a serverless limiter is not
shared between instances, and a strong code makes guessing impractical.

Cookie format: ``v1.<exp>.<b64url(HMAC-SHA256(key, "access|" + code_id + "|" + exp))>``
where ``code_id`` is the first 16 hex characters of SHA-256(code). Changing the
code or the signing key invalidates every issued cookie.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from collections.abc import Callable

ACCESS_COOKIE = "airport_access"
ACCESS_TTL_SECONDS = 8 * 60 * 60
MAX_CODE_CHARS = 256
MAX_COOKIE_CHARS = 256
_DOMAIN = b"access|"
_EXPIRY = re.compile(r"^[1-9][0-9]{0,11}$")


def _sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def code_matches(submitted: object, expected: str) -> bool:
    """Compare fixed-length digests in constant time; reject non-text or oversized input."""
    if not isinstance(submitted, str) or not submitted or len(submitted) > MAX_CODE_CHARS:
        return False
    return hmac.compare_digest(_sha256(submitted), _sha256(expected))


class AccessSigner:
    def __init__(
        self,
        key: bytes,
        access_code: str,
        *,
        ttl_seconds: int = ACCESS_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("access signing key must be at least 32 bytes")
        if not isinstance(access_code, str) or not access_code:
            raise ValueError("access code is required")
        self._key = key
        self._code_id = hashlib.sha256(access_code.encode("utf-8")).hexdigest()[:16].encode("ascii")
        self._ttl = ttl_seconds
        self._clock = clock

    def _mac(self, expiry: str) -> str:
        message = _DOMAIN + self._code_id + b"|" + expiry.encode("ascii")
        digest = hmac.new(self._key, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def issue(self) -> str:
        expiry = str(int(self._clock()) + self._ttl)
        return f"v1.{expiry}.{self._mac(expiry)}"

    def verify(self, cookie: object) -> bool:
        if not isinstance(cookie, str) or len(cookie) > MAX_COOKIE_CHARS or not cookie.isascii():
            return False
        parts = cookie.split(".")
        if len(parts) != 3 or parts[0] != "v1" or not _EXPIRY.fullmatch(parts[1]):
            return False
        if not hmac.compare_digest(self._mac(parts[1]), parts[2]):
            return False
        return int(parts[1]) > int(self._clock())
