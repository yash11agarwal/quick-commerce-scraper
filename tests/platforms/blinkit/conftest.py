from __future__ import annotations

import os

import pytest
from playwright.sync_api import Error as PlaywrightError, sync_playwright


@pytest.fixture(scope="session")
def chromium():
    """One Chromium for the browser-driven Blinkit tests. Skipped when none is installed.
    QCOM_CHROMIUM_PATH points at a specific binary, as in tests/core/test_browser.py."""
    with sync_playwright() as pw:
        kwargs = {"headless": True}
        if os.environ.get("QCOM_CHROMIUM_PATH"):
            kwargs["executable_path"] = os.environ["QCOM_CHROMIUM_PATH"]
        try:
            browser = pw.chromium.launch(**kwargs)
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
                pytest.skip(f"no chromium installed: {exc}")
            raise
        try:
            yield pw, browser
        finally:
            browser.close()
