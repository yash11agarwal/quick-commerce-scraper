import pytest

from job_hunter.schema import JobRecord
from job_hunter.store import AmbiguousJobError, JobStore, UnknownJobError


def _job(job_id="4012345678", **overrides) -> JobRecord:
    base = dict(
        job_id=job_id, title="Python Developer", company="Acme Corp",
        location="Bengaluru", url=f"https://www.linkedin.com/jobs/view/{job_id}",
        posted_date="2026-07-14", search_name="python-bangalore", score=3,
    )
    base.update(overrides)
    return JobRecord(**base)


@pytest.fixture
def store(tmp_path):
    with JobStore(tmp_path / "jobs.db") as s:
        yield s


def test_add_jobs_dedupes_and_preserves_status(store):
    assert len(store.add_jobs([_job(), _job("4098765432", score=1)])) == 2
    store.set_status("4012345678", "applied")

    # Re-discovering both jobs on a later hunt: nothing is "new" and the
    # applied status must survive.
    again = store.add_jobs([_job(), _job("4098765432")])
    assert again == []
    assert store.get_job("4012345678")["status"] == "applied"


def test_status_flow_and_history(store):
    store.add_jobs([_job()])
    store.set_status("4012345678", "interested")
    store.set_status("4012345678", "applied", note="via referral")

    job = store.get_job("4012345678")
    assert job["status"] == "applied"
    assert "via referral" in job["notes"]

    history = store.history("4012345678")
    assert [(h["old_status"], h["new_status"]) for h in history] == [
        ("new", "interested"), ("interested", "applied")]

    with pytest.raises(ValueError):
        store.set_status("4012345678", "ghosted")


def test_id_prefix_resolution(store):
    store.add_jobs([_job("4012345678"), _job("4098765432")])
    assert store.resolve_id("4012") == "4012345678"
    with pytest.raises(AmbiguousJobError):
        store.resolve_id("40")
    with pytest.raises(UnknownJobError):
        store.resolve_id("999")


def test_list_hides_terminal_statuses_by_default(store):
    store.add_jobs([_job(), _job("4098765432", title="Data Engineer")])
    store.set_status("4098765432", "archived")

    assert [r["job_id"] for r in store.list_jobs()] == ["4012345678"]
    # Explicit filter and "all" both reach archived rows.
    assert len(store.list_jobs(statuses=["archived"])) == 1
    assert len(store.list_jobs(statuses=[])) == 2


def test_list_orders_by_score(store):
    store.add_jobs([
        _job("1111111111", score=1),
        _job("2222222222", score=9),
        _job("3333333333", score=5),
    ])
    assert [r["job_id"] for r in store.list_jobs()] == [
        "2222222222", "3333333333", "1111111111"]


def test_notes_append(store):
    store.add_jobs([_job()])
    store.add_note("4012", "recruiter: Priya")
    store.add_note("4012", "phone screen Fri")
    notes = store.get_job("4012345678")["notes"]
    assert notes.index("recruiter: Priya") < notes.index("phone screen Fri")


def test_stats_and_export(store, tmp_path):
    store.add_jobs([_job(), _job("4098765432")])
    store.set_status("4098765432", "applied")
    assert store.stats() == {"new": 1, "applied": 1}

    out = tmp_path / "export.csv"
    assert store.export_csv(out) == 2
    content = out.read_text()
    assert "job_id" in content and "4098765432" in content
