"""Per-domain async rate limiter.

Enforces a configurable minimum delay (plus random jitter) between
navigations to the same domain, regardless of which adapter triggers them.
Share ONE instance across all scrapers in a run.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict


class DomainRateLimiter:
    def __init__(self, min_delay_seconds: float = 8.0, jitter_seconds: float = 4.0):
        self.min_delay = min_delay_seconds
        self.jitter = jitter_seconds
        self._last_request: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def wait(self, domain: str) -> None:
        """Block until the domain is allowed another request, then claim the slot."""
        async with self._locks[domain]:
            now = time.monotonic()
            last = self._last_request.get(domain)
            if last is not None:
                delay = self.min_delay + random.uniform(0, self.jitter)
                remaining = (last + delay) - now
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request[domain] = time.monotonic()
