"""Parser tests against a fixture mirroring LinkedIn's guest search markup."""

from job_hunter.parser import parse_job_description, parse_search_page

# Trimmed-down copy of what the seeMoreJobPostings endpoint actually
# returns: an <li> list of base-card divs with data-entity-urn ids.
SEARCH_PAGE = """
<li>
  <div class="base-card base-search-card job-search-card"
       data-entity-urn="urn:li:jobPosting:4012345678">
    <a class="base-card__full-link"
       href="https://in.linkedin.com/jobs/view/python-developer-at-acme-4012345678?refId=abc&amp;trackingId=xyz">
      <span class="sr-only">Python Developer</span>
    </a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">
        Python Developer
      </h3>
      <h4 class="base-search-card__subtitle">
        <a class="hidden-nested-link" href="https://in.linkedin.com/company/acme">
          Acme Corp
        </a>
      </h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location">Bengaluru, Karnataka, India</span>
        <time class="job-search-card__listdate" datetime="2026-07-14">2 days ago</time>
      </div>
    </div>
  </div>
</li>
<li>
  <div class="base-card base-search-card job-search-card"
       data-entity-urn="urn:li:jobPosting:4098765432">
    <a class="base-card__full-link"
       href="https://in.linkedin.com/jobs/view/data-engineer-at-globex-4098765432">
      <span class="sr-only">Data Engineer</span>
    </a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">Data Engineer</h3>
      <h4 class="base-search-card__subtitle">Globex</h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location">Remote, India</span>
        <time class="job-search-card__listdate--new" datetime="2026-07-16">1 hour ago</time>
      </div>
    </div>
  </div>
</li>
<li>
  <div class="base-card base-search-card">
    <!-- no urn attribute: id must come from the link href -->
    <a class="base-card__full-link"
       href="https://in.linkedin.com/jobs/view/backend-intern-at-initech-4055555555?position=3">
    </a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">Backend Intern</h3>
    </div>
  </div>
</li>
<li>
  <div class="base-card">
    <!-- decorative/broken card: no id and no title; must be skipped -->
    <div class="base-search-card__info"></div>
  </div>
</li>
"""

DETAIL_PAGE = """
<section class="show-more-less-html">
  <div class="show-more-less-html__markup">
    <p>We are hiring a <strong>Python Developer</strong>.</p>
    <br>
    <ul><li>Build scrapers</li><li>Ship APIs</li></ul>
  </div>
</section>
"""


def test_parse_search_page_extracts_cards():
    records = parse_search_page(SEARCH_PAGE)
    assert [r.job_id for r in records] == [
        "4012345678", "4098765432", "4055555555"]

    first = records[0]
    assert first.title == "Python Developer"
    assert first.company == "Acme Corp"
    assert first.location == "Bengaluru, Karnataka, India"
    assert first.posted_date == "2026-07-14"
    # Canonical, tracking-free permalink regardless of the messy card href.
    assert first.url == "https://www.linkedin.com/jobs/view/4012345678"

    # "new" listdate variant still yields the datetime.
    assert records[1].posted_date == "2026-07-16"
    assert records[1].company == "Globex"

    # Fallback id extraction from href; missing fields stay None.
    assert records[2].company is None
    assert records[2].posted_date is None


def test_parse_search_page_empty():
    assert parse_search_page("") == []
    assert parse_search_page("<html><body>no jobs</body></html>") == []


def test_parse_job_description():
    text = parse_job_description(DETAIL_PAGE)
    assert "We are hiring a Python Developer." in text
    assert "- Build scrapers" in text
    assert "- Ship APIs" in text


def test_parse_job_description_missing():
    assert parse_job_description("<div>gone</div>") is None
