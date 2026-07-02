"""Retry-with-exponential-backoff for flaky navigations/waits."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    backoff_base_seconds: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    label: str = "operation",
) -> T:
    """Run ``fn`` up to ``max_attempts`` times with 2s/4s/8s-style backoff.

    Backoff = base * 2^(attempt-1) plus up to 1s of jitter, so concurrent
    retries don't synchronize into request bursts.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except exceptions as exc:  # noqa: PERF203
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = backoff_base_seconds * (2 ** (attempt - 1)) + random.uniform(0, 1)
            log.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                label, attempt, max_attempts, exc, delay,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
