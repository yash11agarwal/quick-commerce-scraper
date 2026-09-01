"""Per-host politeness: a minimum gap plus jitter between requests to the same host."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable


class HostThrottle:
    """Thread-safe. Every worker shares one instance so a host is never hit from two threads at once."""

    def __init__(
        self,
        min_gap_s: float,
        jitter_s: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.min_gap_s = float(min_gap_s)
        self.jitter_s = float(jitter_s)
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._next_free: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> float:
        """Block until the host may be contacted. Returns the seconds actually slept."""
        with self._lock:
            now = self._clock()
            gap = self.min_gap_s + self._rng.uniform(0, self.jitter_s)
            target = max(now, self._next_free.get(host, 0.0))
            self._next_free[host] = target + gap
        delay = target - now
        if delay > 0:
            self._sleep(delay)
        return max(delay, 0.0)

    def wait_all(self, hosts: tuple[str, ...]) -> float:
        return sum(self.wait(h) for h in hosts)
