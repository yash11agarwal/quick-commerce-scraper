"""The retry policy table and the per-platform circuit breaker.

The loop that applies the policy lives in the runner and is deliberately explicit: on each
failure it asks this module "may this code try again, and how long do I wait?".
"""

from __future__ import annotations

import random
import threading

from qcom.core.config import RetryCfg, RetryEntry
from qcom.core.errors import RETRYABLE, ErrorCode

_ENTRY_BY_CODE = {
    ErrorCode.NETWORK_TIMEOUT: "network_timeout",
    ErrorCode.RATE_LIMITED: "rate_limited",
    ErrorCode.PROXY_ERROR: "proxy_error",
    ErrorCode.LOCATION_NOT_SET: "location_not_set",
    ErrorCode.UNKNOWN: "unknown",
}


class RetryPolicy:
    def __init__(self, cfg: RetryCfg, *, rng: random.Random | None = None) -> None:
        self._cfg = cfg
        self._rng = rng or random.Random()

    def entry(self, code: ErrorCode) -> RetryEntry | None:
        if code not in RETRYABLE:
            return None
        return getattr(self._cfg, _ENTRY_BY_CODE[code])

    def max_attempts(self, code: ErrorCode) -> int:
        e = self.entry(code)
        return e.attempts if e else 1

    def should_retry(self, code: ErrorCode, attempts_so_far: int) -> bool:
        e = self.entry(code)
        return e is not None and attempts_so_far < e.attempts

    def backoff_seconds(self, code: ErrorCode, attempts_so_far: int) -> float:
        e = self.entry(code)
        if e is None:
            return 0.0
        base = e.backoff_base_s * (2 ** max(attempts_so_far - 1, 0))
        return base + self._rng.uniform(0, e.jitter_s)


class CircuitBreaker:
    """Counts consecutive failed jobs for one platform within one run."""

    def __init__(self, threshold: int) -> None:
        self.threshold = threshold
        self.consecutive = 0
        self.open = False
        self._lock = threading.Lock()

    def record(self, *, failed: bool) -> bool:
        """Record a job outcome. Returns True if this outcome tripped the breaker."""
        with self._lock:
            if failed:
                self.consecutive += 1
                if self.consecutive >= self.threshold and not self.open:
                    self.open = True
                    return True
            else:
                self.consecutive = 0
            return False
