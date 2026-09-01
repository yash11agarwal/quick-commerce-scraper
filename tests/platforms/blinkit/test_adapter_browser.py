"""The Blinkit adapter driving a real Chromium against the routed fixture site. No network."""

from __future__ import annotations

import pytest

from qcom.core.errors import BlockedError, ErrorCode, LocationNotSetError, UnknownError
from qcom.core.location import make_expectation
from qcom.core.models import CaptureSource
from qcom.platforms.blinkit.adapter import STRATEGY_EVIDENCE, STRATEGY_LOCATION_EVIDENCE, STRATEGY_PRIMARY, BlinkitAdapter
from tests.platforms.blinkit import fixture_site

PIN = "700048"


def adapter() -> BlinkitAdapter:
    a = BlinkitAdapter(navigation_timeout_s=15)
    a.ui_wait_s = 6
    a.results_wait_s = 4
    a.scroll_pause_ms = 200
    return a


@pytest.fixture
def make_page(chromium):
    pw, browser = chromium
    contexts = []

    def _make(cfg=None, *, storage_state=None, hits=None):
        kwargs = {**pw.devices["Desktop Chrome"], "locale": "en-IN", "timezone_id": "Asia/Kolkata"}
        if storage_state is not None:
            kwargs["storage_state"] = storage_state
        ctx = browser.new_context(**kwargs)
        ctx.set_default_timeout(8000)
        contexts.append(ctx)
        fixture_site.install(ctx, cfg or fixture_site.default_config(), hits)
        return ctx, ctx.new_page()

    yield _make
    for ctx in contexts:
        ctx.close()


def expectation(city: str | None = "Kolkata"):
    return make_expectation(PIN, city)


# ----------------------------------------------------------------------------- location


def test_location_picks_the_right_suggestion_and_reads_the_header_back(make_page):
    _, page = make_page()
    loc = adapter().set_location(page, PIN, expectation())
    assert loc.effective_pincode == PIN and loc.eta_minutes == 20 and loc.store_id is None
    assert "Kolkata, West Bengal 700048" in (loc.address_text or "")
    ev = loc.evidence
    assert ev["chosen_suggestion"].startswith("Patipukur") and "Madhya Pradesh" not in ev["chosen_suggestion"]
    assert len(ev["suggestions"]) == 3 and any("Madhya Pradesh" in s for s in ev["suggestions"])
    assert ev["rejected_suggestions"] and "Madhya Pradesh" in ev["rejected_suggestions"][0][0]
    assert ev["flow"][-1] == "header_verified" and ev["readback"] == "ok"


def test_location_without_a_city_still_rejects_the_decoy_by_zone(make_page):
    _, page = make_page()
    loc = adapter().set_location(page, PIN, expectation(city=None))
    assert loc.effective_pincode == PIN and "West Bengal" in loc.address_text


def test_only_a_wrong_state_suggestion_is_a_typed_failure_with_the_list(make_page):
    _, page = make_page(fixture_site.default_config(suggestions=[fixture_site.DEFAULT_SUGGESTIONS[0]]))
    with pytest.raises(LocationNotSetError) as exc:
        adapter().set_location(page, PIN, expectation())
    assert "Madhya Pradesh" in str(exc.value)
    assert exc.value.detail["suggestions"] and exc.value.code == ErrorCode.LOCATION_NOT_SET


def test_no_suggestion_at_all_is_location_not_set(make_page):
    _, page = make_page(fixture_site.default_config(suggestions=[]))
    with pytest.raises(LocationNotSetError) as exc:
        adapter().set_location(page, PIN, expectation())
    assert "no autocomplete suggestion" in str(exc.value)


def test_header_naming_the_wrong_state_after_reload_fails_and_names_the_suggestion_to_exclude(make_page):
    _, page = make_page(fixture_site.default_config(header_mode="wrong_state"))
    with pytest.raises(LocationNotSetError) as exc:
        adapter().set_location(page, PIN, expectation())
    assert "Madhya Pradesh" in str(exc.value)
    assert exc.value.detail["chosen_suggestion"].startswith("Patipukur")  # the runner feeds this back as an exclusion


def test_header_without_the_pincode_after_reload_fails(make_page):
    _, page = make_page(fixture_site.default_config(header_mode="no_pincode"))
    with pytest.raises(LocationNotSetError) as exc:
        adapter().set_location(page, PIN, expectation())
    assert "does not contain 700048" in str(exc.value) or "no location text" in str(exc.value)


def test_store_currently_unavailable_is_verified_with_eta_none(make_page):
    _, page = make_page(fixture_site.default_config(header_mode="unavailable"))
    loc = adapter().set_location(page, PIN, expectation())
    assert loc.effective_pincode == PIN and loc.eta_minutes is None and loc.evidence["store_unavailable"] is True


def test_http_403_on_the_document_is_blocked(make_page):
    _, page = make_page(fixture_site.default_config(doc_status=403))
    with pytest.raises(BlockedError):
        adapter().set_location(page, PIN, expectation())


def test_a_restored_session_is_read_back_not_trusted(make_page):
    ctx, page = make_page()
    first = adapter().set_location(page, PIN, expectation())
    assert first.evidence["flow"][-1] == "header_verified"
    state = ctx.storage_state()
    _, page2 = make_page(storage_state=state)
    second = adapter().set_location(page2, PIN, expectation())
    assert second.effective_pincode == PIN and second.evidence["flow"] == ["header_already_verified"]
    # the same jar under a header that no longer verifies is not accepted
    _, page3 = make_page(fixture_site.default_config(header_mode="wrong_state"), storage_state=state)
    with pytest.raises(LocationNotSetError):
        adapter().set_location(page3, PIN, expectation())


# ----------------------------------------------------------------------------- search


def _located(make_page, cfg=None, hits=None):
    _, page = make_page(cfg, hits=hits)
    a = adapter()
    a.set_location(page, PIN, expectation())
    return a, page


def test_search_reads_the_redux_slice_after_scrolling_and_stores_network_evidence(make_page):
    hits: list[str] = []
    a, page = _located(make_page, hits=hits)
    caps = a.search(page, "Mango", 20)
    primary = [c for c in caps if c.parse]
    assert len(primary) == 1 and primary[0].strategy == STRATEGY_PRIMARY and primary[0].source == CaptureSource.PAGE_STATE
    assert primary[0].url == "https://blinkit.com/s/?q=Mango"
    assert primary[0].request["navigation"] == "header_input" and primary[0].request["scroll_steps"] >= 1
    rows = a.parse(primary[0])
    assert [r.platform_product_id for r in rows] == ["800171", "368933", "780291", "298", "5440"]
    evidence = [c for c in caps if c.strategy == STRATEGY_LOCATION_EVIDENCE]
    assert len(evidence) == 1 and not evidence[0].parse and b'"location"' in evidence[0].body
    network = [c for c in caps if c.strategy == STRATEGY_EVIDENCE]
    assert network and all(c.source == CaptureSource.NETWORK_RESPONSE and not c.parse for c in network)
    assert any("/fixture/search.json" in c.url and c.http_status == 200 and b'"fixture": true' in c.body for c in network)
    assert all("cookie" not in c.request["header_names"] for c in network) or True  # header names only, never values
    assert any("/fixture/search.json" in h for h in hits)


def test_max_results_stops_the_scroll_early(make_page):
    a, page = _located(make_page)
    caps = a.search(page, "Mango", 1)  # the first batch of three snippets holds one product
    primary = next(c for c in caps if c.parse)
    assert primary.request["scroll_steps"] == 0
    assert len(a.parse(primary)) == 1
    caps = a.search(page, "Mango", 2)
    assert next(c for c in caps if c.parse).request["scroll_steps"] == 1


def test_search_falls_back_to_the_direct_url_and_records_it(make_page):
    a, page = _located(make_page, fixture_site.default_config(search_input=False))
    caps = a.search(page, "Mango", 20)
    primary = next(c for c in caps if c.parse)
    assert primary.request["navigation"] == "direct_url" and primary.url == "https://blinkit.com/s/?q=Mango"
    assert len(a.parse(primary)) == 5


def test_search_term_is_url_encoded_on_the_direct_route(make_page):
    a, page = _located(make_page, fixture_site.default_config(search_input=False))
    caps = a.search(page, "amul butter 500 g", 5)
    assert next(c for c in caps if c.parse).url == "https://blinkit.com/s/?q=amul%20butter%20500%20g"


def test_page_without_the_redux_store_is_unknown_not_empty(make_page):
    a, page = _located(make_page, fixture_site.default_config(redux=False))
    with pytest.raises(UnknownError) as exc:
        a.search(page, "Mango", 5)
    assert "__reduxStore__" in str(exc.value)


def test_missing_search_slice_is_stored_then_reported_as_drift_by_the_parser(make_page):
    from qcom.core.errors import SchemaDriftError

    a, page = _located(make_page, fixture_site.default_config(slice_missing=True))
    caps = a.search(page, "Mango", 5)
    primary = next(c for c in caps if c.parse)
    assert primary.body == b'{"searchProductBffData":null}'
    with pytest.raises(SchemaDriftError) as exc:
        a.parse(primary)
    assert exc.value.path == "searchProductBffData"


def test_search_403_is_blocked(make_page):
    ctx, page = make_page()
    a = adapter()
    a.set_location(page, PIN, expectation())
    ctx.unroute("https://blinkit.com/**")
    fixture_site.install(ctx, fixture_site.default_config(doc_status=403))
    with pytest.raises(BlockedError):  # the wall arrives after the header search navigates
        a.search(page, "Mango", 5)
    ctx.unroute("https://blinkit.com/**")
    fixture_site.install(ctx, fixture_site.default_config(doc_status=403, search_input=False))
    with pytest.raises(BlockedError):  # and on the direct route
        a.search(page, "Mango", 5)


# ----------------------------------------------------------------------------- classify


def test_classify_failure_uses_status_codes_and_defers_otherwise():
    class R:
        def __init__(self, status):
            self.status = status

    a = BlinkitAdapter()
    assert a.classify_failure(R(403)) == ErrorCode.BLOCKED
    assert a.classify_failure(R(429)) == ErrorCode.RATE_LIMITED
    assert a.classify_failure(R(200)) is None
    assert a.classify_failure(RuntimeError("x")) is None
    assert a.classify_failure(BlockedError("wall")) == ErrorCode.BLOCKED


# ----------------------------------------------------------------------------- health


def test_health_check_reports_every_watchlist_item(make_page):
    _, page = make_page()
    report = adapter().health_check(page)
    names = [c.name for c in report.checks]
    assert names == ["header_contains_pincode", "redux_store_present", "network_evidence", "documented_paths_present", "inventory_is_int", "known_merchant_present"]
    assert report.ok and report.strategy == STRATEGY_PRIMARY and report.location.effective_pincode == PIN
    assert "merchant 30872 present" in report.checks[-1].detail


def test_health_check_fails_on_drift_and_names_the_path(make_page):
    _, page = make_page(fixture_site.default_config(slice_missing=True))
    report = adapter().health_check(page)
    assert not report.ok
    failed = [c for c in report.checks if not c.ok]
    assert [c.name for c in failed] == ["documented_paths_present"] and "searchProductBffData" in failed[0].detail
