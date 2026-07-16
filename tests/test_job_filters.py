from job_hunter.config import FilterConfig
from job_hunter.filters import passes_filters, score_job
from job_hunter.schema import JobRecord


def _job(title, company="Acme") -> JobRecord:
    return JobRecord(job_id="1", title=title, company=company,
                     location=None, url="u", posted_date=None)


def test_title_exclude_is_case_insensitive():
    filters = FilterConfig(title_exclude=["principal", "staff "])
    ok, reason = passes_filters(_job("Principal Engineer"), filters)
    assert not ok and "principal" in reason
    assert passes_filters(_job("Python Developer"), filters)[0]


def test_company_exclude():
    filters = FilterConfig(company_exclude=["StaffCo"])
    assert not passes_filters(_job("Dev", company="staffco Solutions"), filters)[0]
    assert passes_filters(_job("Dev", company="Acme"), filters)[0]


def test_title_include_any():
    filters = FilterConfig(title_include_any=["python", "backend"])
    assert passes_filters(_job("Senior Python Engineer"), filters)[0]
    assert not passes_filters(_job("Sales Manager"), filters)[0]
    # Empty include list means "everything passes".
    assert passes_filters(_job("Sales Manager"), FilterConfig())[0]


def test_score_sums_matching_keywords():
    weights = {"python": 3, "backend": 2, "remote": 1}
    assert score_job(_job("Remote Python Backend Developer"), weights) == 6
    assert score_job(_job("Frontend Developer"), weights) == 0
    assert score_job(_job("Python Developer"), {}) == 0
