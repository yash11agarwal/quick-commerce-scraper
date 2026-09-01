"""The whole run loop with the Blinkit adapter: a real browser context per pincode group, session
jar saved after verification and read back on resume, raw captures persisted before parsing,
listings and the workbook produced from the database. Routed to the fixture site; no network."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

from qcom.core.browser import BrowserManager
from qcom.core.config import BrowserCfg
from qcom.core.runner import execute_run, plan_run
from qcom.core.storage import Storage
from qcom.core.summary import EXIT_FAILURES, EXIT_OK
from qcom.io.excel_out import write_workbook
from tests.conftest import fast_config, spec_for
from tests.platforms.blinkit import fixture_site


@pytest.fixture
def routed_browser(monkeypatch):
    """Every context the run loop opens gets the fixture-site route. Skips without a Chromium."""
    site_cfg = {"value": fixture_site.default_config()}
    original = BrowserManager.new_context

    def new_context(self, platform, pincode, *, use_jar=True):
        handle = original(self, platform, pincode, use_jar=use_jar)
        fixture_site.install(handle.context, site_cfg["value"])
        return handle

    monkeypatch.setattr(BrowserManager, "new_context", new_context)
    return site_cfg


def _cfg(tmp_path: Path):
    cfg = fast_config(tmp_path)
    cfg.browser = BrowserCfg(headless=True, launch_attempts=1, navigation_timeout_s=15, executable_path=os.environ.get("QCOM_CHROMIUM_PATH") or None)
    return cfg


def _run(cfg, spec):
    run_id = plan_run(cfg, spec)
    try:
        return run_id, execute_run(cfg, run_id)
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip(f"no chromium installed: {exc}")
        raise


def test_blinkit_end_to_end_through_the_run_loop(tmp_path, routed_browser):
    cfg = _cfg(tmp_path)
    run_id, summary = _run(cfg, spec_for(["Mango", "Chaunsa Mango"], ["700048"], platforms=["blinkit"], max_results=4))
    if summary.platform_states and summary.platform_states[0]["status"] == "STOPPED_BROWSER":
        pytest.skip(f"browser could not launch: {summary.platform_states[0]['reason']}")
    assert summary.exit_code == EXIT_OK, summary.as_dict()
    with Storage(cfg.storage.path) as s:
        jobs = s.job_rows(run_id)
        rows = s.results_rows(run_id)
        caps = s.capture_rows(run_id)
    assert {j["status"] for j in jobs} == {"OK"}
    assert all(j["effective_pincode"] == "700048" and j["eta_minutes"] == 20 and j["strategy"] == "redux_store" for j in jobs)
    assert len(rows) == 2 * 4 and all(r["effective_pincode"] == "700048" for r in rows)
    assert {c["strategy"] for c in caps} >= {"redux_store", "location_evidence", "network_capture"}
    assert all(r["capture_id"] in {c["capture_id"] for c in caps} for r in rows)
    jar = Path(cfg.storage.sessions_dir) / "blinkit_700048.json"
    assert jar.exists()
    out = write_workbook(Storage(cfg.storage.path), run_id, tmp_path / "out.xlsx")
    assert Path(out).exists()

    # second run: the jar is loaded and the header read back again, nothing assumed
    run_id2, summary2 = _run(cfg, spec_for(["Mango"], ["700048"], platforms=["blinkit"], max_results=4))
    assert summary2.exit_code == EXIT_OK
    with Storage(cfg.storage.path) as s:
        (job,) = s.job_rows(run_id2)
    assert '"header_already_verified"' in job["location_evidence_json"]


def test_unverifiable_pincode_fails_every_job_in_the_group_and_discards_the_jar(tmp_path, routed_browser):
    routed_browser["value"] = fixture_site.default_config(suggestions=[fixture_site.DEFAULT_SUGGESTIONS[0]])
    cfg = _cfg(tmp_path)
    run_id, summary = _run(cfg, spec_for(["Mango", "Frooti"], ["700048"], platforms=["blinkit"]))
    if summary.platform_states and summary.platform_states[0]["status"] == "STOPPED_BROWSER":
        pytest.skip(summary.platform_states[0]["reason"])
    assert summary.exit_code == EXIT_FAILURES
    with Storage(cfg.storage.path) as s:
        jobs = s.job_rows(run_id)
    assert {j["status"] for j in jobs} == {"FAILED"} and {j["final_code"] for j in jobs} == {"LOCATION_NOT_SET"}
    assert all("Madhya Pradesh" in j["final_reason"] for j in jobs)
    assert not (Path(cfg.storage.sessions_dir) / "blinkit_700048.json").exists()
