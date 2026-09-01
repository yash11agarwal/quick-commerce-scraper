import zlib

import pytest

from qcom.core.clock import now_utc
from qcom.core.errors import ErrorCode
from qcom.core.models import CaptureSource, DataQualityEvent, EffectiveLocation, Job, JobStatus, ProductListing, RawCapture
from qcom.core.storage import Storage


def _run(storage: Storage, run_id="20260901-000000-abcdef"):
    storage.create_run(run_id, started_at=now_utc(), code_version="t", git_sha=None, config_hash="h", config_json={}, input_path="i", input_sha256="s", run_label=None, proxy_label=None, adapter_versions={"fake": "1"})
    jobs = [Job(job_id=Job.make_id(run_id, "fake", "700048", r), run_id=run_id, platform="fake", requested_pincode="700048", search_term=f"t{r}", input_row_id=r, pincode_row_id=2) for r in (2, 3, 4)]
    storage.insert_jobs(jobs)
    return run_id, jobs


def _loc():
    return EffectiveLocation(platform="fake", requested_pincode="700048", effective_pincode="700048", store_id="S", eta_minutes=9, verified_at_utc=now_utc())


def test_captures_are_stored_verbatim_and_checksummed(tmp_path):
    with Storage(tmp_path / "db.sqlite") as s:
        run_id, jobs = _run(s)
        cap = RawCapture(platform="fake", strategy="x", source=CaptureSource.FIXTURE, url="u", body=b'{"a": 1}' * 100, captured_at_utc=now_utc())
        s.save_captures(run_id, jobs[0].job_id, 1, [cap])
        assert cap.capture_id == f"{run_id}:000001" and cap.seq == 1
        assert s.capture_body(cap.capture_id) == cap.body
        rows = s.capture_rows(run_id)
        assert rows[0]["size_bytes"] == len(cap.body) and rows[0]["sha256"] == cap.sha256
        s._conn.execute("UPDATE raw_payloads SET body_zlib=? WHERE capture_id=?", (zlib.compress(b"tampered"), cap.capture_id))
        with pytest.raises(RuntimeError, match="checksum"):
            s.capture_body(cap.capture_id)


def test_job_lifecycle_and_resume_reset(tmp_path):
    with Storage(tmp_path / "db.sqlite") as s:
        run_id, jobs = _run(s)
        assert len(s.pending_jobs(run_id)) == 3
        a1 = s.start_attempt(jobs[0].job_id, now_utc())
        a2 = s.start_attempt(jobs[0].job_id, now_utc())
        assert (a1, a2) == (1, 2) and s.job_attempts(jobs[0].job_id) == 2
        s.close_attempt(jobs[0].job_id, 1, finished_at=now_utc(), outcome="FAILED", error_code=ErrorCode.NETWORK_TIMEOUT, error_message="t")
        cap = RawCapture(platform="fake", strategy="x", source=CaptureSource.FIXTURE, url="u", body=b"{}", captured_at_utc=now_utc())
        s.save_captures(run_id, jobs[0].job_id, 2, [cap])
        listing = ProductListing(platform="fake", result_rank=1, platform_product_id="P1", product_name="n", selling_price_paise=100, capture_id=cap.capture_id)
        s.finish_job(jobs[0], status=JobStatus.OK, finished_at=now_utc(), code=None, reason=None, strategy="x", location=_loc(), listings=[listing], dq_events=[DataQualityEvent(kind="k", detail="d", listing_index=0)], duration_ms=5)
        assert s.status_counts(run_id)["OK"] == 1 and len(s.results_rows(run_id)) == 1
        assert s.dq_counts(run_id) == {"k": 1}
        # simulate a crash inside job 2
        s.start_attempt(jobs[1].job_id, now_utc())
        assert [j.job_id for j in s.pending_jobs(run_id)] == [jobs[1].job_id, jobs[2].job_id]
        assert s.reset_in_progress(run_id) == 1
        assert s.status_counts(run_id)["PENDING"] == 2 and s.status_counts(run_id)["IN_PROGRESS"] == 0
        assert len(s.results_rows(run_id)) == 1  # the finished job kept its rows
        n = s.skip_pending(run_id, "fake", code=ErrorCode.SKIPPED_PLATFORM_BLOCKED, reason="blocked", at=now_utc())
        assert n == 2 and s.code_counts(run_id)["SKIPPED_PLATFORM_BLOCKED"] == 2


def test_previous_price_lookup_and_platform_state(tmp_path):
    with Storage(tmp_path / "db.sqlite") as s:
        old, jobs = _run(s, "20260801-000000-aaaaaa")
        cap = RawCapture(platform="fake", strategy="x", source=CaptureSource.FIXTURE, url="u", body=b"{}", captured_at_utc=now_utc())
        s.save_captures(old, jobs[0].job_id, 1, [cap])
        s.finish_job(jobs[0], status=JobStatus.OK, finished_at=now_utc(), code=None, reason=None, strategy="x", location=_loc(), listings=[ProductListing(platform="fake", result_rank=1, platform_product_id="P1", product_name="n", selling_price_paise=100, capture_id=cap.capture_id)], dq_events=[], duration_ms=1)
        s.finish_run(old, ended_at=now_utc(), status="COMPLETED", exit_code=0, summary={})
        new, _ = _run(s, "20260901-000000-bbbbbb")
        assert s.previous_run_id(new) == old
        assert s.previous_selling_price("fake", "700048", "P1", before_run_id=new) == (old, 100)
        assert s.previous_selling_price("fake", "700048", "P9", before_run_id=new) is None
        s.set_platform_state(new, "fake", status="STOPPED_BLOCKED", reason="wall", consecutive_failures=1)
        st = s.platform_states(new)
        assert st[0]["status"] == "STOPPED_BLOCKED" and st[0]["stopped_at_utc"]
