"""The shared adapter contract suite. Every registered adapter must pass every test here."""

from __future__ import annotations

import inspect

import pytest

from qcom.core.errors import ErrorCode, ParseError, SchemaDriftError
from qcom.core.models import CaptureSource, HealthReport, Probe, RawCapture
from qcom.core.clock import now_utc
from qcom.platforms.base import PlatformAdapter
from qcom.platforms.registry import REGISTRY

ADAPTERS = sorted(REGISTRY.items())


def _normal_capture(adapter: PlatformAdapter) -> RawCapture:
    """A parseable capture for the adapter, from its own fixture set (adapters ship fixtures under fixtures/)."""
    from qcom.core.runner import NoBrowserPage

    page = NoBrowserPage(adapter.probe.pincode) if not adapter.needs_browser else None
    if page is None:
        pytest.skip("browser adapters get their contract fixtures in their own phase")
    caps = adapter.search(page, adapter.probe.term, 5)
    return next(c for c in caps if c.parse)


@pytest.mark.parametrize("name,cls", ADAPTERS)
def test_declares_identity(name, cls):
    assert cls.name == name and isinstance(cls.version, str) and cls.version
    assert cls.hosts and all(isinstance(h, str) for h in cls.hosts)
    assert isinstance(cls.probe, Probe) and len(cls.probe.pincode) == 6
    assert isinstance(cls.stock_depth, bool)


@pytest.mark.parametrize("name,cls", ADAPTERS)
def test_implements_exactly_the_contract(name, cls):
    for method in ("set_location", "search", "parse", "classify_failure", "health_check"):
        assert callable(getattr(cls, method))
    assert list(inspect.signature(cls.parse).parameters) == ["self", "raw"]
    assert list(inspect.signature(cls.search).parameters) == ["self", "page", "term", "max_results"]


@pytest.mark.parametrize("name,cls", ADAPTERS)
def test_parse_is_pure_and_typed(name, cls):
    adapter = cls()
    cap = _normal_capture(adapter)
    a, b = adapter.parse(cap), adapter.parse(cap)
    assert [x.model_dump() for x in a] == [x.model_dump() for x in b]
    assert a, "the probe must return at least one row"
    for row in a:
        assert isinstance(row.platform_product_id, str) and row.platform_product_id
        assert row.platform == name
        for money in (row.mrp_paise, row.selling_price_paise, row.base_selling_price_paise):
            assert money is None or isinstance(money, int)
        assert row.currency == "INR"
        if not cls.stock_depth:
            assert row.stock_qty is None
        assert row.discount_pct is None and row.match_score is None  # derived by core, never by the parser


@pytest.mark.parametrize("name,cls", ADAPTERS)
def test_corrupted_payload_is_parse_error(name, cls):
    adapter = cls()
    cap = RawCapture(platform=name, strategy="t", source=CaptureSource.FIXTURE, url="u", body=b"\x00not json{", captured_at_utc=now_utc())
    with pytest.raises(ParseError):
        adapter.parse(cap)


@pytest.mark.parametrize("name,cls", ADAPTERS)
def test_reshaped_payload_is_schema_drift_naming_the_path(name, cls):
    adapter = cls()
    cap = RawCapture(platform=name, strategy="t", source=CaptureSource.FIXTURE, url="u", body=b"{}", captured_at_utc=now_utc())
    with pytest.raises(SchemaDriftError) as exc:
        adapter.parse(cap)
    assert exc.value.path


@pytest.mark.parametrize("name,cls", ADAPTERS)
def test_classify_failure_returns_code_or_none(name, cls):
    adapter = cls()
    out = adapter.classify_failure(RuntimeError("x"))
    assert out is None or isinstance(out, ErrorCode)


@pytest.mark.parametrize("name,cls", ADAPTERS)
def test_health_check_returns_a_report(name, cls):
    adapter = cls()
    if adapter.needs_browser:
        pytest.skip("live health for browser adapters runs via `python -m qcom health`")
    from qcom.core.runner import NoBrowserPage

    report = adapter.health_check(NoBrowserPage(adapter.probe.pincode))
    assert isinstance(report, HealthReport) and report.platform == name and report.checks
