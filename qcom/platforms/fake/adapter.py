"""The fake adapter: fixture-backed, deterministic, no network, no browser.

It exists so the run loop, retry policy, resume, circuit breaker and Excel output can be
proven end to end without touching the internet. The search term selects a behaviour:

    contains "nothing"    well-formed empty result       -> NO_RESULTS
    contains "drift"      payload missing a path         -> SCHEMA_DRIFT
    contains "corrupt"    body is not JSON               -> PARSE_ERROR
    contains "blocked"    bot wall                        -> BLOCKED
    contains "timeout"    every attempt times out         -> NETWORK_TIMEOUT
    contains "flaky"      first attempt times out, then succeeds
    contains "ratelimit"  429                             -> RATE_LIMITED
    contains "proxyfail"  proxy refused                   -> PROXY_ERROR
    contains "mystery"    untyped exception               -> UNKNOWN
    anything else         normal two-page result

Pincode "000000" cannot be set (LOCATION_NOT_SET). Pincode "999999" applies but the readback
does not carry the pincode, which the run loop must refuse.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from qcom.core.clock import now_utc
from qcom.core.errors import (
    BlockedError,
    ErrorCode,
    LocationNotSetError,
    NetworkTimeoutError,
    ProxyError,
    RateLimitedError,
    SchemaDriftError,
)
from qcom.core.location import check_readback
from qcom.core.models import (
    CaptureSource,
    EffectiveLocation,
    HealthCheck,
    HealthReport,
    LocationExpectation,
    Probe,
    ProductListing,
    RawCapture,
)
from qcom.core.normalise import rupee_text_to_paise
from qcom.platforms.base import PlatformAdapter, load_json, require

_FIXTURES = Path(__file__).parent / "fixtures"
_PAGE_SIZE = 5


def _fixture(name: str, *, term: str, pincode: str) -> bytes:
    text = (_FIXTURES / name).read_text(encoding="utf-8")
    return text.replace("{term}", term.strip().title()).replace("{pincode}", pincode).encode("utf-8")


class FakeAdapter(PlatformAdapter):
    name = "fake"
    version = "1.0"
    hosts = ("fake.example",)
    probe = Probe(pincode="700048", term="butter", city="Kolkata", state="West Bengal")
    needs_browser = False
    stock_depth = True

    def __init__(self, *, navigation_timeout_s: float = 45.0) -> None:
        super().__init__(navigation_timeout_s=navigation_timeout_s)
        self._flaky_seen: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ location

    def set_location(self, page: Any, pincode: str, expectation: LocationExpectation) -> EffectiveLocation:
        if pincode == "000000":
            raise LocationNotSetError("fake platform does not serve 000000", detail={"suggestions": []})
        address = f"Fake Street, Fake City, West Bengal {pincode}, India"
        if pincode == "999999":
            address = "Fake Street, Fake City, India"  # readback without a pincode
        readback = check_readback(address, expectation)
        return EffectiveLocation(
            platform=self.name,
            requested_pincode=pincode,
            effective_pincode=pincode if readback.pincode_found else None,
            store_id=f"FAKE-STORE-{pincode}",
            eta_minutes=12,
            address_text=address,
            evidence={"header_text": address, "readback": readback.reason},
            verified_at_utc=now_utc(),
        )

    # ------------------------------------------------------------------ search

    def search(self, page: Any, term: str, max_results: int) -> list[RawCapture]:
        t = term.lower()
        pincode = getattr(page, "pincode", "700048") if page is not None else "700048"
        if "blocked" in t:
            raise BlockedError("fake bot wall: HTTP 403 with challenge marker")
        if "timeout" in t:
            raise NetworkTimeoutError("fake navigation timed out after 45s")
        if "ratelimit" in t:
            raise RateLimitedError("fake HTTP 429")
        if "proxyfail" in t:
            raise ProxyError("fake net::ERR_TUNNEL_CONNECTION_FAILED")
        if "mystery" in t:
            raise RuntimeError("fake untyped failure")
        if "flaky" in t:
            key = f"{pincode}:{t}"
            with self._lock:
                first = key not in self._flaky_seen
                self._flaky_seen.add(key)
            if first:
                raise NetworkTimeoutError("fake flaky timeout on first attempt")

        def cap(body: bytes, *, page_no: int, parse: bool = True, strategy: str = "fixture_search") -> RawCapture:
            return RawCapture(
                platform=self.name,
                strategy=strategy,
                source=CaptureSource.FIXTURE,
                method="GET",
                url=f"https://fake.example/search?q={term}&page={page_no}",
                http_status=200,
                content_type="application/json",
                body=body,
                captured_at_utc=now_utc(),
                request={"query": term, "page": page_no, "max_results": max_results},
                parse=parse,
            )

        if "nothing" in t:
            return [cap(_fixture("empty.json", term=term, pincode=pincode), page_no=1)]
        if "corrupt" in t:
            return [cap(b'{"products": [{"id": "F001", "name": "truncated', page_no=1)]
        if "drift" in t:
            body = json.loads(_fixture("normal.json", term=term, pincode=pincode))
            body["items"] = body.pop("products")  # the key the spec requires has moved
            return [cap(json.dumps(body).encode("utf-8"), page_no=1)]

        captures = [cap(_fixture("normal.json", term=term, pincode=pincode), page_no=1)]
        if max_results > _PAGE_SIZE:
            captures.append(cap(_fixture("page2.json", term=term, pincode=pincode), page_no=2))
        evidence = json.dumps({"store": f"FAKE-STORE-{pincode}", "note": "location evidence, not parsed"}).encode("utf-8")
        captures.append(cap(evidence, page_no=0, parse=False, strategy="fixture_location_evidence"))
        return captures

    # ------------------------------------------------------------------ parse

    def parse(self, raw: RawCapture) -> list[ProductListing]:
        doc = load_json(raw)
        products = require(doc, "products", expect=list)
        require(doc, "store.id", expect=str)
        out: list[ProductListing] = []
        for i, p in enumerate(products):
            base = f"products[{i}]"
            pid = require(p, "id", expect=str)
            name = require(p, "name", expect=str)
            require(p, "mrp")
            require(p, "base_price")
            stock = require(p, "stock", expect=int)
            anomalies: list[str] = []
            try:
                mrp = rupee_text_to_paise(p["mrp"])
            except ValueError as exc:
                mrp, anomalies = None, [f"mrp_unparseable:{exc}"]
            try:
                price = rupee_text_to_paise(require(p, "price", expect=str))
            except ValueError as exc:
                price = None
                anomalies.append(f"price_unparseable:{exc}")
            base_price = rupee_text_to_paise(p["base_price"]) if p["base_price"] is not None else None
            out.append(
                ProductListing(
                    platform=self.name,
                    result_rank=i + 1,
                    platform_product_id=pid,
                    product_name=name,
                    brand=require(p, "brand"),
                    pack_size=require(p, "pack"),
                    mrp_paise=mrp,
                    selling_price_paise=price,
                    base_selling_price_paise=base_price,
                    in_stock=stock > 0,
                    stock_qty=stock,
                    store_or_seller_id=doc["store"]["id"],
                    category_path=require(p, "category"),
                    product_url=require(p, "url"),
                    image_url=require(p, "image"),
                    anomalies=anomalies,
                )
            )
            if not isinstance(p.get("stock"), int):
                raise SchemaDriftError("stock must be int", path=f"{base}.stock")
        return out

    # ------------------------------------------------------------------ misc

    def classify_failure(self, exc_or_response: Any) -> ErrorCode | None:
        return None

    def health_check(self, page: Any) -> HealthReport:
        from qcom.core.location import make_expectation

        checks: list[HealthCheck] = []
        loc = self.set_location(page, self.probe.pincode, make_expectation(self.probe.pincode, self.probe.city, self.probe.state))
        checks.append(HealthCheck(name="location_readback", ok=loc.effective_pincode == self.probe.pincode, detail=loc.address_text or ""))
        caps = self.search(page, self.probe.term, 5)
        parsed = [c for c in caps if c.parse]
        rows = self.parse(parsed[0])
        checks.append(HealthCheck(name="primary_capture", ok=len(parsed) >= 1, detail=f"{len(parsed)} parseable capture(s)"))
        checks.append(HealthCheck(name="rows_present", ok=len(rows) > 0, detail=f"{len(rows)} rows"))
        checks.append(HealthCheck(name="stock_is_int", ok=all(isinstance(r.stock_qty, int) for r in rows)))
        return HealthReport(
            platform=self.name,
            adapter_version=self.version,
            ok=all(c.ok for c in checks),
            strategy="fixture_search",
            checks=checks,
            location=loc,
            capture_ids=[],
            checked_at_utc=now_utc(),
        )
