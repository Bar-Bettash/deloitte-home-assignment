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
import logging
import re
import time
from collections.abc import Callable
from urllib.parse import urlsplit
from uuid import uuid4

from app.contracts import ErrorResponse
from app.settings import HostingConfig, HostingConfigError, load_hosting
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})
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


def access_signer(hosting: HostingConfig) -> AccessSigner:
    """Build the signer for a validated gated configuration."""
    if hosting.signing_key is None or hosting.access_code is None:
        raise ValueError("gated access requires a code and a signing key")
    return AccessSigner(
        hosting.signing_key.get_secret_value().encode("utf-8"),
        hosting.access_code.get_secret_value(),
    )


# Exact (method, path) pairs reachable without the access cookie. No prefix or
# normalisation: "/health/" or "/login/" are not exempt.
EXEMPT_ROUTES = frozenset({
    ("GET", "/health"), ("HEAD", "/health"),
    ("GET", "/login"), ("HEAD", "/login"),
    ("POST", "/api/access"), ("POST", "/api/logout"),
})
_ALWAYS_OPEN = frozenset({("GET", "/health"), ("HEAD", "/health")})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def request_hostname(host_header: str) -> str | None:
    """Return the lowercase hostname of a Host header, or None when malformed."""
    if not host_header or len(host_header) > 300 or any(char in host_header for char in "/?#@\\ "):
        return None
    try:
        parsed = urlsplit(f"//{host_header}")
        _ = parsed.port  # validates the port component
    except ValueError:
        return None
    return parsed.hostname.lower() if parsed.hostname else None


def origin_allowed(origin: str | None, host_header: str, hosting: HostingConfig) -> bool:
    """Same-origin check for state-changing requests.

    Gated mode requires an Origin header whose scheme and host:port exactly match
    the request (HTTPS on Vercel). A loopback run tolerates a missing Origin.
    """
    if origin is None:
        return not hosting.gated
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    allowed_schemes = {"https"} if hosting.on_vercel else {"http", "https"}
    if parsed.scheme not in allowed_schemes or parsed.path or parsed.query or parsed.fragment:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    return bool(parsed.netloc) and parsed.netloc.lower() == host_header.lower()


def _json_error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> Response:
    request_id = uuid4()
    body = ErrorResponse(success=False, error={"code": code, "message": message, "request_id": request_id})
    response = JSONResponse(
        status_code=status, content=body.model_dump(mode="json"),
        headers={"X-Request-ID": str(request_id), **(headers or {})},
    )
    return response


class AccessGate:
    """Pure ASGI gate in front of every route, including the static mount.

    Order: always-open health check; hosting configuration (503 when unsafe);
    Host allowlist; same-origin check for unsafe methods; then, in gated mode,
    the access cookie unless the exact route is exempt. Unauthenticated browser
    navigations are redirected to /login; everything else gets 401 JSON.
    Gated responses are marked ``Cache-Control: no-store`` so no shared cache
    can serve gated content to someone without the cookie.
    """

    def __init__(self, app: ASGIApp, *, load_config: Callable[[], HostingConfig] | None = None) -> None:
        self.app = app
        self._load_config = load_config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        if (method, path) in _ALWAYS_OPEN:
            await self.app(scope, receive, send)
            return
        loader = self._load_config or load_hosting
        try:
            hosting = loader()
        except HostingConfigError:
            logger.warning("request refused: hosting configuration is invalid")
            await _json_error(503, "internal_error", "The service is not configured.",
                              {"Cache-Control": "no-store"})(scope, receive, send)
            return
        scope.setdefault("state", {})["hosting"] = hosting
        request = Request(scope)
        host_header = request.headers.get("host", "")
        hostname = request_hostname(host_header)
        allowed = LOOPBACK_HOSTS | hosting.allowed_hosts
        if hostname is None or hostname not in allowed:
            await _json_error(400, "invalid_request", "Use this application from its own address.",
                              {"Cache-Control": "no-store"})(scope, receive, send)
            return
        if method not in _SAFE_METHODS and not origin_allowed(request.headers.get("origin"), host_header, hosting):
            await _json_error(400, "invalid_request", "Use this application from its own origin.",
                              {"Cache-Control": "no-store"})(scope, receive, send)
            return

        send = _no_store(send) if hosting.gated else send
        if not hosting.gated or (method, path) in EXEMPT_ROUTES:
            await self.app(scope, receive, send)
            return
        if access_signer(hosting).verify(request.cookies.get(ACCESS_COOKIE)):
            await self.app(scope, receive, send)
            return
        accept = request.headers.get("accept", "")
        if method in {"GET", "HEAD"} and "text/html" in accept.lower():
            response: Response = RedirectResponse("/login", status_code=303)
        else:
            response = _json_error(401, "access_required", "Enter the access code to use this application.")
        await response(scope, receive, send)


def _no_store(send: Send) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"cache-control"]
            headers.append((b"cache-control", b"no-store"))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped
