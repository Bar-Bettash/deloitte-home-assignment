"""Bounded in-memory opaque-token sessions with one latest result each."""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.contracts import AnalysisRequest, AnalysisResult


class SessionError(RuntimeError):
    def __init__(self, code: Literal["session_expired", "result_mismatch", "busy"], message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class _Session:
    token: str
    last_activity: float
    latest_result: AnalysisResult | None = None
    latest_request: AnalysisRequest | None = None


class SessionStore:
    """In-memory session table; it intentionally stores no conversation history."""

    def __init__(
        self,
        *,
        capacity: int = 100,
        idle_seconds: float = 60 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1 or idle_seconds <= 0:
            raise ValueError("session capacity and idle timeout must be positive")
        self.capacity = capacity
        self.idle_seconds = idle_seconds
        self._clock = clock
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._lock = threading.RLock()
        self._query_busy = False

    def create(self) -> str:
        with self._lock:
            self._expire_idle()
            if len(self._sessions) >= self.capacity:
                raise SessionError("busy", "The local session limit has been reached. Retry later.")
            token = secrets.token_urlsafe(32)
            self._sessions[token] = _Session(token, self._clock())
            return token

    def lookup(self, token: str | None) -> bool:
        """Return whether a token is active and refresh its idle timer."""
        if not token:
            return False
        with self._lock:
            self._expire_idle()
            session = self._sessions.get(token)
            if session is None:
                return False
            session.last_activity = self._clock()
            self._sessions.move_to_end(token)
            return True

    def validate_result(self, token: str, result_id: UUID) -> AnalysisResult:
        with self._lock:
            if not self.lookup(token):
                raise SessionError("session_expired", "Your session expired. Start a new analysis.")
            session = self._sessions[token]
            result = session.latest_result
            if result is None or result.result_id != result_id:
                raise SessionError("result_mismatch", "That result is no longer current. Start a new analysis.")
            return result.model_copy(deep=True)

    def validate_request_context(self, token: str, result_id: UUID) -> AnalysisRequest | None:
        """Return the validated request for this session's current result, if stored."""
        with self._lock:
            self.validate_result(token, result_id)
            request = self._sessions[token].latest_request
            return request.model_copy(deep=True) if request is not None else None

    def latest(self, token: str) -> AnalysisResult | None:
        with self._lock:
            if not self.lookup(token):
                raise SessionError("session_expired", "Your session expired. Start a new analysis.")
            result = self._sessions[token].latest_result
            return result.model_copy(deep=True) if result is not None else None

    def save_success(
        self, token: str, result: AnalysisResult, analysis: AnalysisRequest | None = None,
    ) -> None:
        """Replace the latest successful result and its bounded request together."""
        request_copy = (
            AnalysisRequest.model_validate(analysis.model_dump(mode="python"))
            if analysis is not None else None
        )
        result_copy = result.model_copy(deep=True)
        with self._lock:
            if not self.lookup(token):
                raise SessionError("session_expired", "Your session expired. Start a new analysis.")
            self._sessions[token].latest_result = result_copy
            self._sessions[token].latest_request = request_copy

    def try_begin_query(self) -> bool:
        with self._lock:
            if self._query_busy:
                return False
            self._query_busy = True
            return True

    def end_query(self) -> None:
        with self._lock:
            self._query_busy = False

    @property
    def query_busy(self) -> bool:
        with self._lock:
            return self._query_busy

    def clear(self) -> None:
        """Testing hook to isolate route cases."""
        with self._lock:
            self._sessions.clear()
            self._query_busy = False

    def _expire_idle(self) -> None:
        now = self._clock()
        expired = [
            token for token, session in self._sessions.items()
            if now - session.last_activity >= self.idle_seconds
        ]
        for token in expired:
            self._sessions.pop(token, None)
