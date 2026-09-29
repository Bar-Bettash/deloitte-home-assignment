"""Per-instance bound on concurrently running analyses.

This is admission control for one process, not shared state: each serverless
instance enforces its own limit. A request that times out or is cancelled leaves
its worker running in a thread; that worker keeps its slot until it finishes, so a
stuck calculation reduces this instance's capacity rather than being overrun.
"""

from __future__ import annotations

import threading


class QuerySlots:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0

    def try_acquire(self, limit: int) -> bool:
        """Take one slot if fewer than ``limit`` are active; never blocks."""
        if type(limit) is not int or limit < 1:
            raise ValueError("query slot limit must be a positive integer")
        with self._lock:
            if self._active >= limit:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("query slot released more times than acquired")
            self._active -= 1

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def reset(self) -> None:
        """Testing hook to isolate route cases."""
        with self._lock:
            self._active = 0
