import asyncio
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from app.context_token import (
    ContextClaims,
    ContextSigner,
    ContextTokenError,
    result_digest,
)
from app.contracts import (
    MAX_REQUEST_BYTES,
    AnalysisRequest,
    AnalysisResult,
    ErrorCode,
    ErrorResponse,
    QueryRequest,
    validate_request_body_size,
)
from app.dispatch import DispatchFailure, dispatch_analysis
from app.model_adapter import ADAPTER_SHA256, ModelAdapterError, interpret_message
from app.query_slots import QuerySlots
from app.settings import HostingConfig, HostingConfigError, load_hosting, load_local_env, load_settings
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class HealthResponse(BaseModel):
    status: Literal["ok"]


logger = logging.getLogger(__name__)
# A local run reads backend/.env (for GEMINI_API_KEY); Vercel uses its own env settings.
load_local_env()
# Uvicorn and Vercel configure only their own loggers; give the app package an
# INFO handler once so the per-call model metadata line is actually emitted.
_app_logger = logging.getLogger("app")
if not _app_logger.handlers:
    _app_logger.setLevel(logging.INFO)
    _app_logger.addHandler(logging.StreamHandler())
STATIC_DIR = Path(__file__).resolve().parents[2] / "frontend"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})
_ALWAYS_OPEN = frozenset({("GET", "/health"), ("HEAD", "/health")})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CONTEXT_COOKIE = "airport_context"
CONTEXT_TTL_SECONDS = 3600
QUERY_DEADLINE_SECONDS = 30
# Used only by a local loopback run without APP_SIGNING_KEY: context tokens then
# stop verifying when this process restarts, which a local demo can tolerate.
_LOCAL_SIGNING_KEY = secrets.token_bytes(32)
query_slots = QuerySlots()


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

    Hosted mode requires an Origin header whose scheme and host:port exactly match
    the request (HTTPS on Vercel). A loopback run tolerates a missing Origin.
    """
    if origin is None:
        return not hosting.hosted
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


def _no_store(send: Send) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"cache-control"]
            headers.append((b"cache-control", b"no-store"))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


class HostGuard:
    """Pure ASGI guard in front of every route, including the static mount.

    Order: always-open health check; hosting configuration (503 when unsafe);
    Host allowlist; same-origin check for unsafe methods. Hosted responses are
    marked ``Cache-Control: no-store``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        if (scope["method"], scope["path"]) in _ALWAYS_OPEN:
            await self.app(scope, receive, send)
            return
        try:
            hosting = load_hosting()
        except HostingConfigError:
            logger.warning("request refused: hosting configuration is invalid")
            await _guard_error(503, "internal_error", "The service is not configured.")(scope, receive, send)
            return
        scope.setdefault("state", {})["hosting"] = hosting
        request = Request(scope)
        host_header = request.headers.get("host", "")
        hostname = request_hostname(host_header)
        allowed = hosting.allowed_hosts if hosting.hosted else LOOPBACK_HOSTS
        if hostname is None or hostname not in allowed:
            await _guard_error(400, "invalid_request", "Use this application from its own address.")(
                scope, receive, send)
            return
        if scope["method"] not in _SAFE_METHODS and not origin_allowed(
                request.headers.get("origin"), host_header, hosting):
            await _guard_error(400, "invalid_request", "Use this application from its own origin.")(
                scope, receive, send)
            return
        await self.app(scope, receive, _no_store(send) if hosting.hosted else send)


def _guard_error(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
    response = _error_response(uuid4(), status_code, code, message)
    response.headers["Cache-Control"] = "no-store"
    return response


# Interactive API docs and the OpenAPI schema are never served (local or hosted).
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(HostGuard)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


def _hosting(request: Request) -> HostingConfig:
    """Configuration validated by HostGuard for this request."""
    return request.state.hosting


def _context_signer(hosting: HostingConfig) -> ContextSigner:
    key = (
        hosting.signing_key.get_secret_value().encode("utf-8")
        if hosting.signing_key is not None else _LOCAL_SIGNING_KEY
    )
    return ContextSigner(key, ttl_seconds=CONTEXT_TTL_SECONDS)


def _set_context_cookie(response: JSONResponse, token: str, hosting: HostingConfig) -> None:
    response.set_cookie(
        CONTEXT_COOKIE, token, httponly=True, samesite="strict", path="/",
        max_age=CONTEXT_TTL_SECONDS, secure=hosting.secure_cookies,
    )


def _error_response(
    request_id: UUID,
    status_code: int,
    code: ErrorCode,
    message: str,
    *,
    clear_context: bool = False,
    hosting: HostingConfig | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        success=False,
        error={"code": code, "message": message, "request_id": request_id},
    )
    logger.warning("request failed request_id=%s code=%s", request_id, code)
    response = JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers={"X-Request-ID": str(request_id)},
    )
    if clear_context:
        response.delete_cookie(
            CONTEXT_COOKIE, path="/", httponly=True, samesite="strict",
            secure=hosting.secure_cookies if hosting is not None else False,
        )
    return response


def _release_abandoned_query(task: asyncio.Task) -> None:
    try:
        # Retrieve late worker exceptions without accepting or storing a late result.
        task.exception()
    except asyncio.CancelledError:
        pass
    finally:
        query_slots.release()


class _ModelOutcome(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message


def _model_error(exc: ModelAdapterError) -> _ModelOutcome:
    if exc.code == "model_timeout":
        return _ModelOutcome("query_timeout", "The analysis took too long. Your previous result is unchanged.")
    if exc.code == "model_prompt_too_large":
        return _ModelOutcome("invalid_request", "Question or context is too large.")
    return _ModelOutcome("ai_unavailable", "AI interpretation is unavailable. Try a preset or choose a supported analysis.")


def _resolved_request(analysis: AnalysisRequest, result: AnalysisResult) -> AnalysisRequest:
    """Pin the period and bundle the server chose so recomputation cannot drift to a new default."""
    return AnalysisRequest.model_validate({
        **analysis.model_dump(mode="python", exclude_none=True),
        "year": result.scope.year,
        "bundle_id": result.scope.bundle_id,
        "threshold_miles": result.scope.threshold_miles if analysis.metric == "long_haul_share" else None,
    })


def _recompute_previous(claims: ContextClaims, request_id: UUID) -> AnalysisResult:
    """Rebuild the referenced result from its signed request and prove it is unchanged."""
    result = dispatch_analysis(claims.request, request_id)
    result = result.model_copy(update={"result_id": claims.result_id})
    if not secrets.compare_digest(result_digest(result), claims.digest):
        raise DispatchFailure(
            "result_mismatch", 409, "That result can no longer be reproduced. Start a new analysis.",
        )
    return result


def _run_structured(
    analysis: AnalysisRequest, request_id: UUID, claims: ContextClaims | None,
) -> tuple[AnalysisResult, AnalysisRequest]:
    if analysis.action == "explain":
        if claims is None:
            raise DispatchFailure("session_expired", 409, "Start a new analysis before asking for an explanation.")
        previous = _recompute_previous(claims, request_id)
        return dispatch_analysis(analysis, request_id, previous=previous), analysis
    return dispatch_analysis(analysis, request_id), analysis


def _log_model_call(
    request_id: UUID, settings, started: float, *, outcome: str, follow_up: bool,
    input_tokens: int | None = None, output_tokens: int | None = None,
) -> None:
    """One metadata line per provider call; never the question, context or provider text."""
    logger.info(
        "model call request_id=%s model=%s outcome=%s follow_up=%s latency_ms=%d input_tokens=%s output_tokens=%s",
        request_id, settings.model_name, outcome, follow_up,
        round((time.perf_counter() - started) * 1000), input_tokens, output_tokens,
    )


async def _interpret_and_dispatch(
    message: str, request_id: UUID, claims: ContextClaims | None, settings,
) -> tuple[AnalysisResult, AnalysisRequest]:
    started = time.perf_counter()
    try:
        interpreted = await interpret_message(
            message, settings=settings,
            context=claims.request.model_dump(mode="json", exclude_none=True) if claims is not None else None,
        )
    except ModelAdapterError as exc:
        outcome = exc.code if exc.provider_status is None else f"{exc.code}:http_{exc.provider_status}"
        _log_model_call(request_id, settings, started, outcome=outcome, follow_up=claims is not None)
        raise
    except asyncio.CancelledError:
        _log_model_call(request_id, settings, started, outcome="cancelled", follow_up=claims is not None)
        raise
    _log_model_call(request_id, settings, started, outcome=interpreted.kind, follow_up=claims is not None,
                    input_tokens=interpreted.usage.input_tokens, output_tokens=interpreted.usage.output_tokens)
    if interpreted.analysis is None:
        code = interpreted.kind if interpreted.kind in {"clarification_required", "unsupported_scope"} else "ai_unavailable"
        raise _ModelOutcome(code, (
            "Please name the airport, metric, and supported period you want to analyze."
            if code == "clarification_required" else
            "That question is outside the supported airport analyses and periods."
            if code == "unsupported_scope" else
            "AI interpretation is unavailable. Try a preset."
        ))
    analysis = AnalysisRequest.model_validate(interpreted.analysis)
    if analysis.action == "explain" and claims is None:
        raise _ModelOutcome("clarification_required", "Choose a previous result to explain.")
    return await asyncio.to_thread(_run_structured, analysis, request_id, claims)


@app.post("/api/query", response_model=AnalysisResult)
async def query(request: Request) -> AnalysisResult | JSONResponse:
    request_id = uuid4()
    hosting = _hosting(request)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return _error_response(
            request_id, 415, "unsupported_media_type", "Send this request as JSON.",
        )

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            return _error_response(
                request_id, 413, "request_too_large", "Request is too large. Send a shorter question.",
            )
        body.extend(chunk)
    raw_body = bytes(body)
    try:
        validate_request_body_size(raw_body)
    except ValueError:
        return _error_response(
            request_id, 413, "request_too_large", "Request is too large. Send a shorter question.",
        )
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error_response(request_id, 400, "invalid_json", "Request JSON is invalid.")
    try:
        query_request = QueryRequest.model_validate(payload)
    except ValidationError:
        return _error_response(
            request_id, 422, "invalid_request", "Request fields or analysis scope are invalid.",
        )
    signer = _context_signer(hosting)

    analysis = query_request.analysis
    claims = None
    # Only a request that references a previous result reads the context cookie;
    # an independent request ignores (and replaces) whatever cookie it carries.
    if query_request.context_result_id is not None:
        try:
            claims = signer.verify(request.cookies.get(CONTEXT_COOKIE))
        except ContextTokenError:
            return _error_response(request_id, 409, "session_expired", "Your session expired. Start a new analysis.",
                                   clear_context=True, hosting=hosting)
        if claims.result_id != query_request.context_result_id:
            return _error_response(request_id, 409, "result_mismatch",
                                   "That result is no longer current. Start a new analysis.")

    if analysis is None:
        try:
            settings = load_settings()
            # Fail closed: a key alone never enables free text. The exact model
            # and this adapter file's hash must also be admitted.
            admitted = settings.model_runtime_admitted(ADAPTER_SHA256)
        except (ValueError, ValidationError):
            admitted = False
        if not admitted:
            return _error_response(request_id, 503, "ai_unavailable", "AI interpretation is unavailable. Try a preset or choose a supported analysis.")

    if not query_slots.try_acquire(hosting.max_concurrent_queries):
        return _error_response(request_id, 409, "busy", "Another analysis is running. Wait and try again.")
    if analysis is None:
        task = asyncio.create_task(_interpret_and_dispatch(query_request.message, request_id, claims, settings))
    else:
        task = asyncio.create_task(asyncio.to_thread(_run_structured, analysis, request_id, claims))
    worker_owns_slot = False
    try:
        result, analysis = await asyncio.wait_for(asyncio.shield(task), timeout=QUERY_DEADLINE_SECONDS)
        token = None if analysis.action == "explain" else signer.sign(result, _resolved_request(analysis, result))
    except TimeoutError:
        worker_owns_slot = True
        task.add_done_callback(_release_abandoned_query)
        return _error_response(request_id, 504, "query_timeout", "The analysis took too long. Your previous result is unchanged.")
    except asyncio.CancelledError:
        worker_owns_slot = True
        task.add_done_callback(_release_abandoned_query)
        raise
    except DispatchFailure as exc:
        return _error_response(request_id, exc.status_code, exc.code, exc.message)
    except ModelAdapterError as exc:
        safe = _model_error(exc)
        status = 504 if safe.code == "query_timeout" else 422 if safe.code == "invalid_request" else 503
        return _error_response(request_id, status, safe.code, safe.message)
    except _ModelOutcome as exc:
        status = 422 if exc.code in {"clarification_required", "unsupported_scope", "invalid_request"} else 503
        return _error_response(request_id, status, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected dispatcher failures at the HTTP boundary.
        logger.warning("query failed request_id=%s code=internal_error exception=%s", request_id, type(exc).__name__)
        return _error_response(request_id, 500, "internal_error", "The analysis failed. Try again or choose another request.")
    finally:
        if not worker_owns_slot:
            query_slots.release()

    response = JSONResponse(status_code=200, content=result.model_dump(mode="json"),
                            headers={"X-Request-ID": str(request_id)})
    if token is not None:
        _set_context_cookie(response, token, hosting)
    return response
