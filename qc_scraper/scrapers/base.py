"""BaseScraper: shared Playwright lifecycle + internal-API interception.

Design
------
Every platform here is a JS-rendered SPA whose product data arrives via
internal JSON APIs (XHR/fetch). Instead of parsing rendered HTML — which
churns with every UI redesign — each adapter:

1. drives the real site with Playwright (so cookies, tokens, and
   location-scoping all happen exactly as they do for a normal user), and
2. passively intercepts network responses whose URLs match the platform's
   ``API_URL_PATTERNS`` and parses the raw JSON payloads.

REVERSE-ENGINEERING WARNING: all ``API_URL_PATTERNS`` and payload parsers in
the subclasses target *undocumented internal endpoints* observed in browser
devtools. None of these platforms offer a public API. Endpoints, params, and
payload shapes change without notice — when an adapter suddenly returns
nothing, re-open devtools on the live site and update the patterns/parsers.

Adapter contract (implemented per platform):
    await set_location(pincode)          -> None (must be called first)
    await search_product(query)          -> list[ProductRecord]
    await get_inventory(product_id)      -> ProductRecord | None
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Response,
    async_playwright,
)

from ..config import AppConfig
from ..schema import ProductRecord
from ..utils import DomainRateLimiter, random_user_agent, retry_async

log = logging.getLogger(__name__)


@dataclass
class CapturedResponse:
    """One intercepted internal-API JSON response."""

    url: str
    status: int
    data: Any
    captured_at: float = field(default_factory=time.monotonic)


class LocationNotSetError(RuntimeError):
    """Raised when product data is requested before set_location() succeeded."""


class BaseScraper(ABC):
    #: Platform key used in output rows and the config file.
    PLATFORM: str = "base"
    #: Landing URL of the platform.
    BASE_URL: str = ""
    #: Regexes matched against response URLs; matching JSON bodies are captured.
    #: These identify REVERSE-ENGINEERED internal endpoints — see module docstring.
    API_URL_PATTERNS: list[str] = []

    def __init__(self, config: AppConfig, rate_limiter: DomainRateLimiter):
        self.config = config
        self.rate_limiter = rate_limiter
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._responses: list[CapturedResponse] = []
        self._compiled_patterns = [re.compile(p) for p in self.API_URL_PATTERNS]
        self.current_pincode: Optional[str] = None
        self.timeout_ms = int(config.browser.timeout_seconds * 1000)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {"headless": self.config.browser.headless}
        if self.config.browser.proxy:
            launch_kwargs["proxy"] = {"server": self.config.browser.proxy}
        if self.config.browser.executable_path:
            launch_kwargs["executable_path"] = self.config.browser.executable_path
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            user_agent=random_user_agent(),  # one UA per session (see user_agents.py)
            viewport={"width": 1366, "height": 850},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            geolocation=None,
        )
        self._context.set_default_timeout(self.timeout_ms)
        self.page = await self._context.new_page()
        self.page.on("response", self._on_response)
        log.info("[%s] browser session started", self.PLATFORM)

    async def stop(self) -> None:
        for closer in (self._context, self._browser):
            if closer:
                try:
                    await closer.close()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    pass
        if self._playwright:
            await self._playwright.stop()
        self._context = self._browser = self._playwright = self.page = None
        log.info("[%s] browser session closed", self.PLATFORM)

    async def __aenter__(self) -> "BaseScraper":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    # ------------------------------------------------------------------ #
    # Network interception
    # ------------------------------------------------------------------ #

    async def _on_response(self, response: Response) -> None:
        """Passively capture JSON bodies from matching internal API calls."""
        url = response.url
        if not any(p.search(url) for p in self._compiled_patterns):
            return
        try:
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype and "javascript" not in ctype:
                return
            body = await response.text()
            data = json.loads(body)
        except Exception:  # noqa: BLE001 - redirects, aborted bodies, non-JSON
            return
        self._responses.append(CapturedResponse(url=url, status=response.status, data=data))
        log.debug("[%s] captured %s (%d)", self.PLATFORM, url, response.status)

    def clear_captured(self) -> float:
        """Drop the capture buffer; returns a monotonic watermark for wait_for_json."""
        self._responses.clear()
        return time.monotonic()

    async def wait_for_json(
        self,
        url_pattern: str,
        *,
        since: float = 0.0,
        timeout: Optional[float] = None,
        min_responses: int = 1,
        settle_seconds: float = 2.0,
    ) -> list[CapturedResponse]:
        """Wait until >= min_responses captures matching ``url_pattern`` arrive.

        After the threshold is met, waits a short ``settle_seconds`` grace
        period to pick up trailing paginated/parallel responses.
        """
        pattern = re.compile(url_pattern)
        timeout = timeout or self.config.browser.timeout_seconds
        deadline = time.monotonic() + timeout

        def matching() -> list[CapturedResponse]:
            return [
                r for r in self._responses
                if r.captured_at >= since and pattern.search(r.url)
            ]

        while time.monotonic() < deadline:
            found = matching()
            if len(found) >= min_responses:
                await asyncio.sleep(settle_seconds)
                return matching()
            await asyncio.sleep(0.25)
        raise TimeoutError(
            f"[{self.PLATFORM}] no response matching {url_pattern!r} within {timeout}s "
            "— the internal endpoint may have changed (see module docstring)"
        )

    # ------------------------------------------------------------------ #
    # Navigation helpers
    # ------------------------------------------------------------------ #

    @property
    def domain(self) -> str:
        return urlparse(self.BASE_URL).netloc

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        """Rate-limited, retried navigation."""
        assert self.page is not None, "call start() first"

        async def _nav() -> None:
            await self.rate_limiter.wait(self.domain)
            await self.page.goto(url, wait_until=wait_until, timeout=self.timeout_ms)

        await retry_async(
            _nav,
            max_attempts=self.config.retry.max_attempts,
            backoff_base_seconds=self.config.retry.backoff_base_seconds,
            label=f"{self.PLATFORM} goto {url}",
        )

    async def click_first_available(self, selectors: list[str], *, timeout: float = 6.0) -> bool:
        """Try a list of fallback selectors (UI churn tolerance); click first match."""
        assert self.page is not None
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                await el.wait_for(state="visible", timeout=timeout * 1000)
                await el.click()
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def fill_first_available(
        self, selectors: list[str], text: str, *, timeout: float = 6.0
    ) -> bool:
        assert self.page is not None
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                await el.wait_for(state="visible", timeout=timeout * 1000)
                await el.click()
                await el.fill("")
                # Typing with delay triggers the sites' autosuggest XHRs the
                # same way a human does; fill() alone often doesn't.
                await el.type(text, delay=90)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def require_location(self) -> str:
        if not self.current_pincode:
            raise LocationNotSetError(
                f"[{self.PLATFORM}] set_location(pincode) must succeed before "
                "search_product()/get_inventory(); catalogs are hyperlocal."
            )
        return self.current_pincode

    # ------------------------------------------------------------------ #
    # Debug capture — for diagnosing adapter breakage without live access
    # ------------------------------------------------------------------ #

    async def dump_debug(self, label: str, reason: str = "") -> None:
        """Save a screenshot + recently captured JSON to ./debug/<platform>/.

        Call this whenever a step fails, or succeeds in a way that looks
        wrong (e.g. zero results). Since these sites actively block
        non-residential IPs, whoever maintains this code long-term often
        can't reproduce a breakage live — these artifacts are what lets a
        precise fix happen from screenshots/payloads alone.
        """
        if self.page is None:
            return
        debug_dir = Path("debug") / self.PLATFORM
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        stem = debug_dir / f"{safe_label}_{ts}"

        try:
            await self.page.screenshot(path=str(stem) + ".png", full_page=True)
        except Exception:  # noqa: BLE001 - best-effort diagnostics, never fatal
            pass
        try:
            payloads = [
                {"url": r.url, "status": r.status, "data": r.data}
                for r in self._responses[-20:]
            ]
            (Path(str(stem) + ".json")).write_text(
                json.dumps(payloads, indent=2, default=str)
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            (Path(str(stem) + ".url.txt")).write_text(self.page.url)
        except Exception:  # noqa: BLE001
            pass
        if reason:
            log.warning("[%s] debug artifacts saved to %s* (%s)",
                       self.PLATFORM, stem, reason)
        else:
            log.warning("[%s] debug artifacts saved to %s*", self.PLATFORM, stem)

    # ------------------------------------------------------------------ #
    # Adapter contract
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def set_location(self, pincode: str) -> None:
        """Set the delivery location. MUST be called before any product call —
        every platform scopes its catalog, pricing, and stock to a dark store
        chosen by this location."""

    @abstractmethod
    async def search_product(self, query: str) -> list[ProductRecord]:
        """Search the catalog and return normalized records."""

    @abstractmethod
    async def get_inventory(self, product_id: str) -> Optional[ProductRecord]:
        """Fetch current availability for a single known product id."""
