"""End to end through the fake adapter. No network, no browser."""

from __future__ import annotations

import threading

import pytest

from qcom.core.clock import now_utc
from qcom.core.config import RetryCfg, RetryEntry
from qcom.core.errors import ErrorCode
from qcom.core.models import CaptureSource, EffectiveLocation, Job, JobStatus, ProductListing, RawCapture
from qcom.core.runner import execute_run, plan_run, resume_run
from qcom.core.storage import Storage
from qcom.core.summary import EXIT_ABORTED, EXIT_FAILURES, EXIT_OK
from qcom.platforms.fake.adapter import FakeAdapter
from qcom.platforms import registry
from tests.conftest import fast_config, spec_for


def _jobs(cfg, run_id):
    with Storage(cfg.storage.path) as s:
        return {j["search_term"] + "@" + j["requested_pincode"]: j for j in s.job_rows(run_id)}, s.results_rows(run_id), s.platform_states(run_id), s.dq_counts(run_id)


def test_normal_run_writes_rows_and_records_every_capture_before_parse(cfg):
    run_id = plan_run(cfg, spec_for(["amul butter", "maggi"], ["700048", "110001"], max_results=8))
    summary = execute_run(cfg, run_id)
    assert summary.exit_code == EXIT_OK and summary.status_counts["OK"] == 4
    jobs, rows, states, dq = _jobs(cfg, run_id)
    assert len(rows) == 4 * 7  # 5 + 3 fixture rows, one duplicate across pages
    assert {r["result_rank"] for r in rows if r["job_id"] == jobs["amul butter@700048"]["job_id"]} == set(range(1, 8))
    assert all(r["effective_pincode"] == r["requested_pincode"] for r in rows)
    with Storage(cfg.storage.path) as s:
        caps = s.capture_rows(run_id)
        assert len(caps) == 4 * 3  # two pages plus one evidence capture per job
        assert {c["strategy"] for c in caps} == {"fixture_search", "fixture_location_evidence"}
        assert all(r["capture_id"] in {c["capture_id"] for c in caps} for r in rows)
    assert dq["pack_size_unparsed"] == 4 and dq["mrp_below_selling"] == 4 and dq["missing_mrp"] == 4 and dq["duplicate_product_id"] == 4
    assert states[0]["status"] == "ACTIVE"
    assert jobs["amul butter@700048"]["strategy"] == "fixture_search" and jobs["amul butter@700048"]["store_id"] == "FAKE-STORE-700048"


def test_max_results_caps_rows_and_pages(cfg):
    run_id = plan_run(cfg, spec_for(["amul butter"], ["700048"], max_results=3))
    execute_run(cfg, run_id)
    _, rows, _, _ = _jobs(cfg, run_id)
    assert [r["result_rank"] for r in rows] == [1, 2, 3]
    with Storage(cfg.storage.path) as s:
        assert len([c for c in s.capture_rows(run_id) if c["strategy"] == "fixture_search"]) == 1


def test_the_three_empties_are_three_different_codes(cfg):
    run_id = plan_run(cfg, spec_for(["nothing here", "drifted payload", "corrupt body"], ["700048"]))
    summary = execute_run(cfg, run_id)
    jobs, rows, _, _ = _jobs(cfg, run_id)
    assert rows == []
    assert jobs["nothing here@700048"]["status"] == "NO_RESULTS" and jobs["nothing here@700048"]["final_code"] == "NO_RESULTS"
    assert jobs["drifted payload@700048"]["status"] == "FAILED" and jobs["drifted payload@700048"]["final_code"] == "SCHEMA_DRIFT"
    assert "path: products" in jobs["drifted payload@700048"]["final_reason"]
    assert jobs["corrupt body@700048"]["final_code"] == "PARSE_ERROR"
    # raw was persisted before the parser failed, and the failure row points at it
    for key in ("drifted payload@700048", "corrupt body@700048"):
        assert jobs[key]["attempts"] == 1 and jobs[key]["artifact_path"].startswith("captures=")
    assert summary.exit_code == EXIT_FAILURES  # 2 of 3 failed


def test_retry_policy_per_code(cfg):
    run_id = plan_run(cfg, spec_for(["flaky one", "timeout always", "mystery thing", "ratelimit me", "after ratelimit"], ["700048"]))
    summary = execute_run(cfg, run_id)
    jobs, _, states, _ = _jobs(cfg, run_id)
    assert jobs["flaky one@700048"]["status"] == "OK" and jobs["flaky one@700048"]["attempts"] == 2
    assert jobs["timeout always@700048"]["final_code"] == "NETWORK_TIMEOUT" and jobs["timeout always@700048"]["attempts"] == 3
    assert jobs["mystery thing@700048"]["final_code"] == "UNKNOWN" and jobs["mystery thing@700048"]["attempts"] == 1
    assert jobs["ratelimit me@700048"]["final_code"] == "RATE_LIMITED" and jobs["ratelimit me@700048"]["attempts"] == 2
    assert jobs["after ratelimit@700048"]["status"] == "SKIPPED"
    assert states[0]["status"] == "STOPPED_RATE_LIMIT"
    assert any("UNKNOWN" in n for n in summary.notes)
    with Storage(cfg.storage.path) as s:
        attempts = s.attempt_rows(jobs["timeout always@700048"]["job_id"])
        assert [a["error_code"] for a in attempts] == ["NETWORK_TIMEOUT"] * 3 and attempts[0]["traceback"]


def test_blocked_stops_the_platform_and_skips_the_rest(cfg):
    run_id = plan_run(cfg, spec_for(["fine", "blocked wall", "never runs"], ["700048", "110001"]))
    summary = execute_run(cfg, run_id)
    jobs, _, states, _ = _jobs(cfg, run_id)
    assert jobs["fine@700048"]["status"] == "OK"
    assert jobs["blocked wall@700048"]["final_code"] == "BLOCKED" and jobs["blocked wall@700048"]["attempts"] == 1
    for key in ("never runs@700048", "fine@110001", "blocked wall@110001", "never runs@110001"):
        assert jobs[key]["status"] == "SKIPPED" and jobs[key]["final_code"] == "SKIPPED_PLATFORM_BLOCKED"
    assert states[0]["status"] == "STOPPED_BLOCKED"
    assert summary.exit_code == EXIT_FAILURES and any("blocked on: fake" in n for n in summary.notes)


def test_circuit_breaker(tmp_path):
    cfg = fast_config(tmp_path)
    cfg.retry = RetryCfg(network_timeout=RetryEntry(attempts=1), unknown=RetryEntry(attempts=1))
    cfg.circuit_breaker.consecutive_failures = 3
    run_id = plan_run(cfg, spec_for(["timeout a", "timeout b", "timeout c", "fine"], ["700048"]))
    execute_run(cfg, run_id)
    jobs, _, states, _ = _jobs(cfg, run_id)
    assert jobs["fine@700048"]["status"] == "SKIPPED" and "circuit" in jobs["fine@700048"]["final_reason"]
    assert states[0]["status"] == "STOPPED_CIRCUIT"


def test_location_failures_fail_the_whole_pincode_group_and_nothing_else(cfg):
    run_id = plan_run(cfg, spec_for(["a", "b"], ["000000", "700048", "999999"]))
    summary = execute_run(cfg, run_id)
    jobs, rows, _, _ = _jobs(cfg, run_id)
    for term in ("a", "b"):
        j = jobs[f"{term}@000000"]
        assert j["status"] == "FAILED" and j["final_code"] == "LOCATION_NOT_SET" and "2 attempt(s)" in j["final_reason"]
        j = jobs[f"{term}@999999"]
        assert j["final_code"] == "LOCATION_NOT_SET" and "does not carry the requested pincode" in j["final_reason"]
        assert jobs[f"{term}@700048"]["status"] == "OK"
    assert {r["requested_pincode"] for r in rows} == {"700048"}
    assert summary.exit_code == EXIT_FAILURES


def test_a_bad_pincode_counts_once_toward_the_breaker(tmp_path):
    cfg = fast_config(tmp_path)
    cfg.circuit_breaker.consecutive_failures = 3
    run_id = plan_run(cfg, spec_for(["a", "b", "c", "d"], ["000000", "700048"]))
    execute_run(cfg, run_id)
    jobs, _, states, _ = _jobs(cfg, run_id)
    assert all(jobs[f"{t}@000000"]["final_code"] == "LOCATION_NOT_SET" for t in "abcd")
    assert all(jobs[f"{t}@700048"]["status"] == "OK" for t in "abcd")
    assert states[0]["status"] == "ACTIVE"


class SimulatedCrash(BaseException):
    """Not an Exception on purpose: the run loop must not catch it, exactly like a kill signal."""


class CrashyAdapter(FakeAdapter):
    name = "crashy"
    crash_on_call = 3
    calls = 0
    lock = threading.Lock()

    def search(self, page, term, max_results):
        with CrashyAdapter.lock:
            CrashyAdapter.calls += 1
            n = CrashyAdapter.calls
        if n == CrashyAdapter.crash_on_call:
            raise SimulatedCrash("process died mid-job")
        return super().search(page, term, max_results)


def test_resume_after_a_crash_finishes_without_duplicates(cfg, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "crashy", CrashyAdapter)
    CrashyAdapter.calls = 0
    run_id = plan_run(cfg, spec_for(["p1", "p2", "p3", "p4", "p5"], ["700048"], platforms=["crashy"]))
    with pytest.raises(SimulatedCrash):
        execute_run(cfg, run_id)
    with Storage(cfg.storage.path) as s:
        counts = s.status_counts(run_id)
        assert counts["OK"] == 2 and counts["IN_PROGRESS"] == 1 and counts["PENDING"] == 2
        assert s.get_run(run_id)["status"] == "IN_PROGRESS"
    summary = resume_run(cfg, run_id)
    assert summary.exit_code == EXIT_OK and summary.status_counts["OK"] == 5
    jobs, rows, _, _ = _jobs(cfg, run_id)
    assert len(rows) == 5 * 7
    assert sorted(jobs[k]["attempts"] for k in jobs) == [1, 1, 1, 1, 2]  # the crashed job counts its lost attempt
    assert len({(r["job_id"], r["result_rank"]) for r in rows}) == len(rows)
    # resuming a finished run is a no-op
    again = resume_run(cfg, run_id)
    assert again.status_counts["OK"] == 5 and len(_jobs(cfg, run_id)[1]) == 35


def test_price_move_is_flagged_not_fixed(cfg):
    prev_run = "20200101-000000-aaaaaa"
    with Storage(cfg.storage.path) as s:
        s.create_run(prev_run, started_at=now_utc(), code_version="t", git_sha=None, config_hash="h", config_json={}, input_path="i", input_sha256="s", run_label=None, proxy_label=None, adapter_versions={})
        job = Job(job_id=Job.make_id(prev_run, "fake", "700048", 2), run_id=prev_run, platform="fake", requested_pincode="700048", search_term="amul butter", input_row_id=2, pincode_row_id=2)
        s.insert_jobs([job])
        cap = RawCapture(platform="fake", strategy="x", source=CaptureSource.FIXTURE, url="u", body=b"{}", captured_at_utc=now_utc())
        s.save_captures(prev_run, job.job_id, 1, [cap])
        loc = EffectiveLocation(platform="fake", requested_pincode="700048", effective_pincode="700048", verified_at_utc=now_utc())
        s.finish_job(job, status=JobStatus.OK, finished_at=now_utc(), code=None, reason=None, strategy="x", location=loc, listings=[ProductListing(platform="fake", result_rank=1, platform_product_id="F001", product_name="n", selling_price_paise=10000, capture_id=cap.capture_id)], dq_events=[], duration_ms=1)
        s.finish_run(prev_run, ended_at=now_utc(), status="COMPLETED", exit_code=0, summary={})
    run_id = plan_run(cfg, spec_for(["amul butter"], ["700048"]))
    execute_run(cfg, run_id)
    _, rows, _, dq = _jobs(cfg, run_id)
    assert dq.get("price_moved_gt_threshold") == 1
    assert next(r for r in rows if r["platform_product_id"] == "F001")["selling_price_paise"] == 27500  # value untouched


def test_proxy_error_without_fallback_aborts_the_run(cfg):
    run_id = plan_run(cfg, spec_for(["proxyfail", "later"], ["700048", "110001"]))
    summary = execute_run(cfg, run_id)
    assert summary.exit_code == EXIT_ABORTED and summary.run_status == "ABORTED"
    jobs, _, states, _ = _jobs(cfg, run_id)
    assert jobs["proxyfail@700048"]["final_code"] == "PROXY_ERROR" and jobs["proxyfail@700048"]["attempts"] == 2
    assert jobs["later@110001"]["status"] == "SKIPPED"
    assert states[0]["status"] == "STOPPED_RUN_ABORTED"


def test_planning_rejects_unimplemented_platform(cfg):
    # Phase 2 made Blinkit real; the Phase 3 platforms are still planned-only.
    with pytest.raises(ValueError, match="not implemented yet"):
        plan_run(cfg, spec_for(["x"], ["700048"], platforms=["zepto"]))
    with pytest.raises(ValueError, match="unknown platform"):
        plan_run(cfg, spec_for(["x"], ["700048"], platforms=["amazon_now"]))


def test_blank_platforms_means_every_implemented_real_platform(cfg):
    from qcom.platforms.registry import implemented_platforms

    run_id = plan_run(cfg, spec_for(["x"], ["700048"]), platforms=[])
    with Storage(cfg.storage.path) as s:
        assert sorted({j["platform"] for j in s.job_rows(run_id)}) == implemented_platforms() == ["blinkit"]
