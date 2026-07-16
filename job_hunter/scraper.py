"""HTTP client for LinkedIn's guest (no-login) job search surface.

Two public endpoints, both of which serve HTML fragments to anonymous
visitors — the same data anyone sees on linkedin.com/jobs without an
account:

- search pages (25 cards per page, paginated via ``start``):
  https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
- a single posting's detail (description, criteria):
  https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<job_id>

Deliberately NOT here: anything requiring a login. Automating a
logged-in LinkedIn session (auto-apply, connection requests, InMail)
violates LinkedIn's User Agreement and is a fast way to get an account
restricted — a disaster mid-job-hunt. This client only reads public
listings, slowly, for personal use.

LinkedIn throttles anonymous traffic aggressively: expect HTTP 429 (and
sometimes the LinkedIn-specific 999) once you push past a few pages per
minute. The client retries with backoff and enforces a polite delay
between every request; keep max_pages small and run on a schedule rather
than hammering.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Iterator
from urllib.parse import urlencode

import requests

from qc_scraper.utils import random_user_agent

from .config import JobHunterConfig, SearchSpec

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
PAGE_SIZE = 25

#: Statuses worth retrying — transient throttling / server hiccups.
_RETRYABLE = {429, 500, 502, 503, 504, 999}


class FetchBlockedError(RuntimeError):
    """LinkedIn refused the request even after retries (throttled/blocked)."""


class PoliteDelay:
    """Enforce a minimum gap (+ jitter) between consecutive requests."""

    def __init__(self, min_delay_seconds: float, jitter_seconds: float):
        self._min = min_delay_seconds
        self._jitter = jitter_seconds
        self._last = 0.0

    def wait(self) -> None:
        target = self._min + random.uniform(0, self._jitter)
        elapsed = time.monotonic() - self._last
        if elapsed < target:
            time.sleep(target - elapsed)
        self._last = time.monotonic()


class LinkedInGuestClient:
    def __init__(self, config: JobHunterConfig):
        self._config = config
        self._delay = PoliteDelay(
            config.rate_limit.min_delay_seconds,
            config.rate_limit.jitter_seconds,
        )
        self._session = requests.Session()
        # One UA for the whole session; mid-session rotation is a bot signal.
        self._session.headers.update({
            "User-Agent": random_user_agent(),
            "Accept-Language": "en-US,en;q=0.9",
        })

    # -- low level -----------------------------------------------------

    def _get(self, url: str, label: str) -> str:
        retry = self._config.retry
        last_status: int | None = None
        for attempt in range(1, retry.max_attempts + 1):
            self._delay.wait()
            try:
                resp = self._session.get(
                    url, timeout=self._config.request_timeout_seconds)
            except requests.RequestException as exc:
                last_status = None
                log.warning("%s: request error (attempt %d/%d): %s",
                            label, attempt, retry.max_attempts, exc)
            else:
                if resp.status_code == 200:
                    return resp.text
                last_status = resp.status_code
                if resp.status_code not in _RETRYABLE:
                    raise FetchBlockedError(
                        f"{label}: LinkedIn returned HTTP {resp.status_code}")
                log.warning("%s: HTTP %d (attempt %d/%d)",
                            label, resp.status_code, attempt, retry.max_attempts)
            if attempt < retry.max_attempts:
                backoff = retry.backoff_base_seconds * (2 ** (attempt - 1))
                time.sleep(backoff + random.uniform(0, 1))
        raise FetchBlockedError(
            f"{label}: gave up after {retry.max_attempts} attempts "
            f"(last status: {last_status}). LinkedIn is throttling — "
            "increase rate_limit delays, lower max_pages, or try again later.")

    # -- public --------------------------------------------------------

    def iter_search_pages(self, spec: SearchSpec) -> Iterator[str]:
        """Yield raw HTML for each result page of one search, in order.

        Stops at spec.max_pages or on the first empty page, whichever
        comes first.
        """
        base_params = spec.query_params()
        for page in range(spec.max_pages):
            params = dict(base_params, start=str(page * PAGE_SIZE))
            url = f"{SEARCH_URL}?{urlencode(params)}"
            html = self._get(url, label=f"search[{spec.name}] page {page + 1}")
            if not html.strip():
                # Past the last page LinkedIn serves an empty body.
                return
            yield html

    def fetch_job_detail(self, job_id: str) -> str:
        """Raw HTML of one posting's public detail view (description etc.)."""
        return self._get(DETAIL_URL.format(job_id=job_id),
                         label=f"detail[{job_id}]")
