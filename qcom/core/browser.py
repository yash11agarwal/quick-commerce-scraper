"""Playwright lifecycle: launch, device profile, proxy, session jars, failure artifacts.

Adapters receive a ``Page`` and nothing else. Everything here is owned by the run loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError, Page, Playwright, TimeoutError as PlaywrightTimeout, sync_playwright
from tenacity import retry, stop_after_attempt, wait_exponential

from qcom.core.config import BrowserCfg, ProxyCfg
from qcom.core.errors import ErrorCode, NetworkTimeoutError, ProxyError, QcomError
from qcom.core.logging import get_logger

log = get_logger(__name__)

_PROXY_MARKERS = ("ERR_PROXY", "ERR_TUNNEL_CONNECTION_FAILED", "ERR_NO_SUPPORTED_PROXIES", "407")


def classify_playwright_error(exc: BaseException) -> ErrorCode | None:
    """Generic classification for browser-level failures. Adapters get first say; this is the fallback."""
    if isinstance(exc, QcomError):
        return exc.code
    if isinstance(exc, PlaywrightTimeout):
        return ErrorCode.NETWORK_TIMEOUT
    if isinstance(exc, PlaywrightError):
        text = str(exc)
        if any(m in text for m in _PROXY_MARKERS):
            return ErrorCode.PROXY_ERROR
        if "net::ERR_" in text:
            return ErrorCode.NETWORK_TIMEOUT
    return None


def wrap_playwright_error(exc: BaseException) -> BaseException:
    code = classify_playwright_error(exc)
    if code == ErrorCode.NETWORK_TIMEOUT:
        return NetworkTimeoutError(str(exc))
    if code == ErrorCode.PROXY_ERROR:
        return ProxyError(str(exc))
    return exc


@dataclass
class ContextHandle:
    context: BrowserContext
    page: Page
    platform: str
    pincode: str
    loaded_from_jar: bool


class BrowserManager:
    """One per worker thread. Not shared."""

    def __init__(self, browser_cfg: BrowserCfg, proxy_cfg: ProxyCfg, sessions_dir: Path) -> None:
        self.cfg = browser_cfg
        self.proxy = proxy_cfg
        self.sessions_dir = sessions_dir
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._proxy_servers = [proxy_cfg.server, *proxy_cfg.fallback_servers] if proxy_cfg.server else [None]
        self._proxy_index = 0

    # ---------------------------------------------------------------- lifecycle

    def __enter__(self) -> "BrowserManager":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def current_proxy_server(self) -> str | None:
        return self._proxy_servers[self._proxy_index]

    def _launch_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"headless": self.cfg.headless}
        if self.cfg.executable_path:
            kwargs["executable_path"] = self.cfg.executable_path
        server = self.current_proxy_server()
        if server:
            proxy: dict[str, Any] = {"server": server}
            if self.proxy.username:
                proxy["username"] = self.proxy.username
            if self.proxy.password:
                proxy["password"] = self.proxy.password
            kwargs["proxy"] = proxy
        return kwargs

    def start(self) -> None:
        if self._pw is None:
            self._pw = sync_playwright().start()

        attempts = self.cfg.launch_attempts

        @retry(stop=stop_after_attempt(attempts), wait=wait_exponential(multiplier=1, min=1, max=20), reraise=True)
        def _launch() -> Browser:
            assert self._pw is not None
            log.info("browser.launch", headless=self.cfg.headless, proxy=bool(self.current_proxy_server()))
            return self._pw.chromium.launch(**self._launch_kwargs())

        self._browser = _launch()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    def rotate_proxy(self) -> bool:
        """Move to the next configured proxy server and relaunch. False if none is left."""
        if self._proxy_index + 1 >= len(self._proxy_servers):
            return False
        self._proxy_index += 1
        log.warning("proxy.rotate", server_index=self._proxy_index)
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        self.start()
        return True

    # ---------------------------------------------------------------- contexts

    def _jar_path(self, platform: str, pincode: str) -> Path:
        return self.sessions_dir / f"{platform}_{pincode}.json"

    def new_context(self, platform: str, pincode: str, *, use_jar: bool = True) -> ContextHandle:
        assert self._browser is not None and self._pw is not None, "start() first"
        device = self._pw.devices.get(self.cfg.device)
        if device is None:
            raise ValueError(f"unknown Playwright device profile {self.cfg.device!r}")
        kwargs: dict[str, Any] = {**device, "locale": "en-IN", "timezone_id": "Asia/Kolkata"}
        jar = self._jar_path(platform, pincode)
        loaded = False
        if use_jar and jar.exists():
            kwargs["storage_state"] = str(jar)
            loaded = True
        context = self._browser.new_context(**kwargs)
        context.set_default_timeout(self.cfg.navigation_timeout_s * 1000)
        context.set_default_navigation_timeout(self.cfg.navigation_timeout_s * 1000)
        page = context.new_page()
        return ContextHandle(context=context, page=page, platform=platform, pincode=pincode, loaded_from_jar=loaded)

    def save_session(self, handle: ContextHandle) -> Path:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self._jar_path(handle.platform, handle.pincode)
        state = handle.context.storage_state()
        path.write_text(json.dumps(state), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def discard_session(self, platform: str, pincode: str) -> None:
        jar = self._jar_path(platform, pincode)
        if jar.exists():
            jar.unlink()

    @staticmethod
    def close_context(handle: ContextHandle) -> None:
        handle.context.close()

    @staticmethod
    def save_artifacts(handle: ContextHandle, stem: Path) -> str | None:
        """Screenshot plus HTML next to each other. Returns the screenshot path, or None if the page is gone."""
        stem.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle.page.screenshot(path=str(stem.with_suffix(".png")), full_page=True)
            stem.with_suffix(".html").write_text(handle.page.content(), encoding="utf-8")
            stem.with_suffix(".url.txt").write_text(handle.page.url, encoding="utf-8")
        except PlaywrightError as exc:
            log.warning("artifact.failed", error=str(exc))
            return None
        return str(stem.with_suffix(".png"))
