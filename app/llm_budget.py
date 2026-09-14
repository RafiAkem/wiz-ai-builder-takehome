"""Budget guard for the public, unauthenticated LLM fallback.

The demo endpoint is open to the internet, so a stranger could otherwise drain the
owner's Gemini quota. Two limits, both read from the environment on every call:
a sliding 60-minute window per client IP and a rolling 24-hour global counter.

Every real Gemini attempt (initial call and 5xx retry alike) charges one unit, so
the counters track actual upstream cost rather than an optimistic per-request cap.

ponytail: counters live in this process's memory. Ceiling: a restart clears them,
and they are not shared across uvicorn workers. Upgrade path: move both windows to
Redis (INCR + EXPIRE) if this ever runs multi-worker or multi-host.

Thread safety: FastAPI runs sync endpoints on a shared threadpool, so BudgetGuard
can be hit concurrently. All state mutations happen under one lock, held only for
the bookkeeping — never across a provider call, which would serialize the app.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

HOUR = 3600.0
DAY = 86_400.0


def _limit(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def _prune(timestamps: deque[float], cutoff: float) -> None:
    while timestamps and timestamps[0] < cutoff:
        timestamps.popleft()


class BudgetGuard:
    #: Sweep stale per-IP buckets once the dict grows past this many entries. Needed
    #: because refused requests still create a bucket: an attacker rotating IPs would
    #: otherwise grow the map without bound.
    SWEEP_THRESHOLD = 256

    def __init__(self) -> None:
        self._per_ip: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()
        self._lock = threading.RLock()

    @property
    def per_ip_limit(self) -> int:
        return _limit("LLM_RATE_PER_IP_HOUR", 30)

    @property
    def global_limit(self) -> int:
        return _limit("LLM_RATE_GLOBAL_DAY", 800)

    def try_consume(self, client_ip: str, now: float | None = None) -> bool:
        """Charge one unit for this IP if both limits allow.

        True and recorded when allowed, False and unrecorded otherwise. The caller
        invokes this once per real provider attempt, not once per HTTP request.
        `now` exists for tests; production always passes None.
        """
        moment = time.time() if now is None else now
        with self._lock:
            if len(self._per_ip) > self.SWEEP_THRESHOLD:
                self._sweep(moment)
            window = self._per_ip.setdefault(client_ip, deque())
            _prune(window, moment - HOUR)
            _prune(self._global, moment - DAY)
            if len(window) >= self.per_ip_limit or len(self._global) >= self.global_limit:
                return False
            window.append(moment)
            self._global.append(moment)
            return True

    def _sweep(self, now: float) -> None:
        """Reclaim per-IP buckets with nothing left inside the active hour window.

        A bucket is dropped only once pruning to `now - HOUR` has emptied it, which
        is exactly the point where keeping it could no longer affect any limit
        decision — so an active window is never broken by construction.
        """
        cutoff = now - HOUR
        for ip, window in list(self._per_ip.items()):
            _prune(window, cutoff)
            if not window:
                del self._per_ip[ip]

    def prune_stale(self, now: float | None = None) -> int:
        """Public reclaim hook: drop empty-window buckets now. Returns how many."""
        moment = time.time() if now is None else now
        with self._lock:
            before = len(self._per_ip)
            self._sweep(moment)
            return before - len(self._per_ip)

    def gate_for(self, client_ip: str) -> BudgetGate:
        """Per-client ProviderGate handle: each consume() charges `client_ip` once."""
        return BudgetGate(self, client_ip)

    def used_today(self) -> int:
        with self._lock:
            _prune(self._global, time.time() - DAY)
            return len(self._global)


    def reset(self) -> None:
        """Drop every counter. Test hook."""
        with self._lock:
            self._per_ip.clear()
            self._global.clear()

class BudgetGate:
    """One client's handle on the budget, matching the ProviderGate protocol in
    app.source_extraction: every `consume()` call charges that client for exactly
    one provider attempt. Keeps all accounting decisions inside this module."""

    __slots__ = ("_guard", "_client_ip")

    def __init__(self, guard: BudgetGuard, client_ip: str) -> None:
        self._guard = guard
        self._client_ip = client_ip

    def consume(self) -> bool:
        return self._guard.try_consume(self._client_ip)



guard = BudgetGuard()
