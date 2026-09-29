"""Stateless, HMAC-signed follow-up context for serverless instances.

The token replaces a server-side session table. It carries only the latest
successful result reference, the resolved request that produced it, and a digest
of that result. Any instance holding the same signing key can verify it and
recompute the result from the request; nothing is stored between requests.

Format: ``v1.<b64url(canonical JSON payload)>.<b64url(HMAC-SHA256)>``. The MAC is
computed over the domain tag ``ctx|`` plus the exact received ``v1.<payload>``
bytes and compared in constant time before the payload is decoded. Every failure
raises the same ``ContextTokenError`` so callers cannot act as a decoding oracle.
Replay within the expiry window is accepted: the token grants no authority beyond
recomputing a public-data result the holder already received.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.contracts import AnalysisRequest, AnalysisResult
from pydantic import ValidationError

VERSION = "v1"
MAX_TOKEN_BYTES = 2048
MIN_KEY_BYTES = 32
DEFAULT_TTL_SECONDS = 3600
_DOMAIN = b"ctx|"
_PAYLOAD_KEYS = {"rid", "req", "dig", "exp"}
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ContextTokenError(ValueError):
    """The context token is missing, malformed, forged, or expired."""


@dataclass(frozen=True)
class ContextClaims:
    result_id: UUID
    request: AnalysisRequest
    digest: str
    expires_at: int


def result_digest(result: AnalysisResult) -> str:
    """Hash every result field except per-response identifiers."""
    canonical = result.model_dump_json(exclude={"result_id", "request_id"})
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    if not _B64URL.fullmatch(text):
        raise ContextTokenError("invalid context token")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ContextTokenError("invalid context token") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ContextTokenError("invalid context token")
        value[key] = item
    return value


class ContextSigner:
    """Sign and verify context tokens with one server-held key."""

    def __init__(
        self,
        key: bytes,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(key, bytes) or len(key) < MIN_KEY_BYTES:
            raise ValueError("context signing key must be at least 32 bytes")
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("context token lifetime must be a positive integer")
        self._key = key
        self._ttl = ttl_seconds
        self._clock = clock

    def _mac(self, signed_part: bytes) -> str:
        return _b64encode(hmac.new(self._key, _DOMAIN + signed_part, hashlib.sha256).digest())

    def sign(self, result: AnalysisResult, request: AnalysisRequest) -> str:
        """Bind the latest non-explain result to the resolved request that produced it."""
        if request.action == "explain":
            raise ValueError("explain results are not follow-up context")
        payload = {
            "rid": str(result.result_id),
            "req": request.model_dump(mode="json", exclude_none=True),
            "dig": result_digest(result),
            "exp": int(self._clock()) + self._ttl,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        signed_part = f"{VERSION}.{_b64encode(encoded.encode('ascii'))}"
        token = f"{signed_part}.{self._mac(signed_part.encode('ascii'))}"
        if len(token) > MAX_TOKEN_BYTES:
            raise ValueError("context token exceeds its size bound")
        return token

    def verify(self, token: object) -> ContextClaims:
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_BYTES or not token.isascii():
            raise ContextTokenError("invalid context token")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != VERSION:
            raise ContextTokenError("invalid context token")
        signed_part = f"{parts[0]}.{parts[1]}".encode("ascii")
        # Authenticate the exact received bytes before decoding anything.
        if not hmac.compare_digest(self._mac(signed_part), parts[2]):
            raise ContextTokenError("invalid context token")
        try:
            payload = json.loads(_b64decode(parts[1]), object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ContextTokenError("invalid context token") from exc
        if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
            raise ContextTokenError("invalid context token")
        rid, req, dig, exp = payload["rid"], payload["req"], payload["dig"], payload["exp"]
        if type(exp) is not int or exp <= int(self._clock()):
            raise ContextTokenError("invalid context token")
        if not isinstance(dig, str) or not _HEX64.fullmatch(dig):
            raise ContextTokenError("invalid context token")
        if not isinstance(rid, str) or not isinstance(req, dict):
            raise ContextTokenError("invalid context token")
        try:
            result_id = UUID(rid)
            request = AnalysisRequest.model_validate(req)
        except (ValueError, ValidationError) as exc:
            raise ContextTokenError("invalid context token") from exc
        if str(result_id) != rid or request.action == "explain":
            raise ContextTokenError("invalid context token")
        return ContextClaims(result_id, request, dig, exp)
