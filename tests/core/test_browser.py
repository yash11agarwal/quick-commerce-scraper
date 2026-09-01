"""Launches the local Chromium on about:blank. No network. Skipped when no browser is installed."""

from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

from qcom.core.browser import BrowserManager, classify_playwright_error
from qcom.core.config import BrowserCfg, ProxyCfg
from qcom.core.errors import ErrorCode, NetworkTimeoutError


def test_classifier_maps_browser_errors():
    assert classify_playwright_error(NetworkTimeoutError("x")) == ErrorCode.NETWORK_TIMEOUT
    assert classify_playwright_error(PlaywrightError("net::ERR_TUNNEL_CONNECTION_FAILED at http://x")) == ErrorCode.PROXY_ERROR
    assert classify_playwright_error(PlaywrightError("net::ERR_CONNECTION_RESET")) == ErrorCode.NETWORK_TIMEOUT
    assert classify_playwright_error(RuntimeError("x")) is None


def test_context_lifecycle_and_session_jar(tmp_path: Path):
    import os

    cfg = BrowserCfg(headless=True, launch_attempts=1, executable_path=os.environ.get("QCOM_CHROMIUM_PATH") or None)
    mgr = BrowserManager(cfg, ProxyCfg(), tmp_path / "sessions")
    try:
        mgr.start()
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip(f"no chromium installed: {exc}")
        raise
    try:
        h = mgr.new_context("fake", "700048")
        assert not h.loaded_from_jar
        h.page.goto("about:blank")
        jar = mgr.save_session(h)
        assert jar.exists() and jar.name == "fake_700048.json"
        shot = mgr.save_artifacts(h, tmp_path / "runs" / "art")
        assert shot and Path(shot).exists() and (tmp_path / "runs" / "art.html").exists()
        mgr.close_context(h)
        h2 = mgr.new_context("fake", "700048")
        assert h2.loaded_from_jar
        mgr.close_context(h2)
        mgr.discard_session("fake", "700048")
        assert not jar.exists()
    finally:
        mgr.close()
