"""Budget guard for the public, unauthenticated LLM fallback.

The demo endpoint is open to the internet, so a stranger could otherwise drain the
owner's Gemini quota. Two limits, both read from the environment on every call:
a sliding 60-minute window per client IP and a rolling 24-hour global counter.

ponytail: counters live in this process's memory. Ceiling: a restart clears them,
and they are not shared across uvicorn workers. Upgrade path: move both windows to
Redis (INCR + EXPIRE) if this ever runs multi-worker or multi-host.
"""

import os
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
    def __init__(self) -> None:
        self._per_ip: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()

    @property
    def per_ip_limit(self) -> int:
        return _limit("LLM_RATE_PER_IP_HOUR", 30)

    @property
    def global_limit(self) -> int:
        return _limit("LLM_RATE_GLOBAL_DAY", 800)

    def try_consume(self, client_ip: str) -> bool:
        """True and recorded when both limits allow, False and unrecorded otherwise."""
        now = time.time()
        window = self._per_ip.setdefault(client_ip, deque())
        _prune(window, now - HOUR)
        _prune(self._global, now - DAY)
        if len(window) >= self.per_ip_limit or len(self._global) >= self.global_limit:
            return False
        window.append(now)
        self._global.append(now)
        return True

    def used_today(self) -> int:
        _prune(self._global, time.time() - DAY)
        return len(self._global)

    def reset(self) -> None:
        """Drop every counter. Test hook."""
        self._per_ip.clear()
        self._global.clear()


guard = BudgetGuard()
