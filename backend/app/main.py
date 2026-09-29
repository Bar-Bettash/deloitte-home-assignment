import asyncio
import json
import logging
import secrets
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
from app.settings import HostingConfig, HostingConfigError, load_hosting, load_settings
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError


class HealthResponse(BaseModel):
    status: Literal["ok"]


app = FastAPI()
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
CONTEXT_COOKIE = "airport_context"
CONTEXT_TTL_SECONDS = 3600
QUERY_DEADLINE_SECONDS = 30
ALLOWED_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})
# Used only by an ungated loopback run without APP_SIGNING_KEY: context tokens then
# stop verifying when this process restarts, which a local demo can tolerate.
_LOCAL_SIGNING_KEY = secrets.token_bytes(32)
query_slots = QuerySlots()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


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
    logger.warning("query failed request_id=%s code=%s", request_id, code)
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


def _safe_local_origin(request: Request) -> bool:
    host_header = request.headers.get("host", "").lower()
    host_name = (request.url.hostname or "").lower()
    if host_name not in ALLOWED_LOOPBACK_HOSTS:
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return parsed.scheme == request.url.scheme and parsed.netloc.lower() == host_header


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


async def _interpret_and_dispatch(
    message: str, request_id: UUID, claims: ContextClaims | None, settings,
) -> tuple[AnalysisResult, AnalysisRequest]:
    interpreted = await interpret_message(
        message, settings=settings,
        context=claims.request.model_dump(mode="json", exclude_none=True) if claims is not None else None,
    )
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
    if not _safe_local_origin(request):
        return _error_response(request_id, 400, "invalid_request", "Use this local application from its own origin.")
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
    try:
        hosting = load_hosting()
    except HostingConfigError:
        return _error_response(request_id, 503, "internal_error", "The service is not configured.")
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
