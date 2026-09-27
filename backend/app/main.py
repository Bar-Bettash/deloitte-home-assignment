import asyncio
import json
import logging
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

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
from app.model_budget import BudgetConfigurationError, BudgetExhausted, BudgetLedger
from app.session import SessionError, SessionStore
from app.settings import load_settings
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError


class HealthResponse(BaseModel):
    status: Literal["ok"]


app = FastAPI()
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "airport_session"
QUERY_DEADLINE_SECONDS = 30
ALLOWED_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})
session_store = SessionStore()
model_budget = BudgetLedger()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


def _error_response(
    request_id: UUID,
    status_code: int,
    code: ErrorCode,
    message: str,
    *,
    cookie_token: str | None = None,
    clear_cookie: bool = False,
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
    if clear_cookie:
        response.delete_cookie(SESSION_COOKIE, path="/")
    elif cookie_token is not None:
        response.set_cookie(SESSION_COOKIE, cookie_token, httponly=True, samesite="strict", path="/", max_age=3600)
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
        session_store.end_query()


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


async def _interpret_and_dispatch(
    message: str, request_id: UUID, previous: AnalysisResult | None,
    context: AnalysisRequest | None, settings,
) -> tuple[AnalysisResult, AnalysisRequest]:
    reservation = model_budget.reserve(settings)
    finalized = False
    try:
        interpreted = await interpret_message(
            message, settings=settings,
            context=context.model_dump(mode="json", exclude_none=True) if context is not None else None,
        )
        # Invalid or absent usage forfeits the full reserved amount.
        finalized = True
        try:
            reservation.settle(interpreted.usage)
        except ValueError as exc:
            raise _ModelOutcome("ai_unavailable", "AI interpretation is unavailable. Try a preset.") from exc
    except BaseException:
        if not finalized:
            reservation.forfeit()
        raise

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
    if analysis.action == "explain" and previous is None:
        raise _ModelOutcome("clarification_required", "Choose a previous result to explain.")
    result = await asyncio.to_thread(dispatch_analysis, analysis, request_id, previous=previous)
    return result, analysis


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

    analysis = query_request.analysis
    token = request.cookies.get(SESSION_COOKIE)
    created = False
    previous = None
    context = None
    try:
        active_session = session_store.lookup(token)
        if query_request.context_result_id is not None:
            if not active_session or token is None:
                raise SessionError("session_expired", "Your session expired. Start a new analysis.")
            previous = session_store.validate_result(token, query_request.context_result_id)
            if analysis is None:
                context = session_store.validate_request_context(token, query_request.context_result_id)
                if context is None:
                    raise SessionError("result_mismatch", "That result cannot provide follow-up context. Start a new analysis.")
        elif not active_session:
            if analysis is not None:
                token = session_store.create()
                created = True
    except SessionError as exc:
        return _error_response(request_id, 409, exc.code, exc.message,
                               clear_cookie=exc.code == "session_expired")

    if analysis is None:
        try:
            settings = load_settings()
            admitted = settings.model_runtime_admitted(ADAPTER_SHA256)
        except (ValueError, ValidationError):
            admitted = False
        if not admitted:
            return _error_response(request_id, 503, "ai_unavailable", "AI interpretation is unavailable. Try a preset or choose a supported analysis.")
        if not active_session:
            try:
                token = session_store.create()
                created = True
            except SessionError as exc:
                return _error_response(request_id, 409, exc.code, exc.message)

    if analysis is not None and analysis.action == "explain":
        try:
            result = dispatch_analysis(analysis, request_id, previous=previous)
        except DispatchFailure as exc:
            return _error_response(request_id, exc.status_code, exc.code, exc.message,
                                   cookie_token=token if created else None)
        response = JSONResponse(status_code=200, content=result.model_dump(mode="json"),
                                headers={"X-Request-ID": str(request_id)})
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", path="/", max_age=3600)
        return response

    if not session_store.try_begin_query():
        return _error_response(request_id, 409, "busy", "Another analysis is running. Wait and try again.",
                               cookie_token=token if created else None)
    if analysis is None:
        task = asyncio.create_task(_interpret_and_dispatch(query_request.message, request_id, previous, context, settings))
    else:
        task = asyncio.create_task(asyncio.to_thread(dispatch_analysis, analysis, request_id, previous=previous))
    worker_owns_busy_slot = False
    try:
        completed = await asyncio.wait_for(asyncio.shield(task), timeout=QUERY_DEADLINE_SECONDS)
        if analysis is None:
            result, analysis = completed
        else:
            result = completed
        if analysis.action != "explain":
            session_store.save_success(token, result, analysis)
    except TimeoutError:
        worker_owns_busy_slot = True
        task.add_done_callback(_release_abandoned_query)
        return _error_response(request_id, 504, "query_timeout", "The analysis took too long. Your previous result is unchanged.",
                               cookie_token=token if created else None)
    except asyncio.CancelledError:
        worker_owns_busy_slot = True
        task.add_done_callback(_release_abandoned_query)
        raise
    except DispatchFailure as exc:
        return _error_response(request_id, exc.status_code, exc.code, exc.message,
                               cookie_token=token if created else None)
    except SessionError as exc:
        return _error_response(request_id, 409, exc.code, exc.message,
                               cookie_token=token if created else None)
    except BudgetExhausted:
        return _error_response(request_id, 503, "budget_exhausted", "AI spending limit reached. Try a preset.",
                               cookie_token=token if created else None)
    except BudgetConfigurationError:
        return _error_response(request_id, 503, "ai_unavailable", "AI interpretation is unavailable. Try a preset.",
                               cookie_token=token if created else None)
    except ModelAdapterError as exc:
        safe = _model_error(exc)
        status = 504 if safe.code == "query_timeout" else 422 if safe.code == "invalid_request" else 503
        return _error_response(request_id, status, safe.code, safe.message,
                               cookie_token=token if created else None)
    except _ModelOutcome as exc:
        status = 422 if exc.code in {"clarification_required", "unsupported_scope", "invalid_request"} else 503
        return _error_response(request_id, status, exc.code, exc.message,
                               cookie_token=token if created else None)
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected dispatcher failures at the HTTP boundary.
        logger.warning("query failed request_id=%s code=internal_error exception=%s", request_id, type(exc).__name__)
        return _error_response(request_id, 500, "internal_error", "The analysis failed. Try again or choose another request.",
                               cookie_token=token if created else None)
    finally:
        if not worker_owns_busy_slot:
            session_store.end_query()

    response = JSONResponse(status_code=200, content=result.model_dump(mode="json"),
                            headers={"X-Request-ID": str(request_id)})
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", path="/", max_age=3600)
    return response
