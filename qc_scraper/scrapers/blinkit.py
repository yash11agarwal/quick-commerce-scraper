"""Blinkit (blinkit.com) adapter.

REVERSE-ENGINEERED INTERNAL ENDPOINTS — no public API exists. Observed via
browser devtools (subject to change without notice):

- Search:   ``blinkit.com/v6/search/products?...`` and the newer
            ``blinkit.com/v1/layout/search?...`` (widget/"snippet" layout API).
- PDP:      ``blinkit.com/v2/product/...`` / layout calls fired from the
            product page (``/prn/<slug>/prid/<id>``).

Location model: Blinkit resolves the pincode to lat/lng and picks a dark
store ("merchant"); all subsequent API calls carry that store context via
cookies/headers, which is why we set location through the real UI instead
of forging headers.

Inventory granularity: Blinkit's internal search payloads historically
include an integer ``inventory`` field per product. It is CAPPED by the
backend (values like 3/5/10 recur suspiciously often) — treat it as an
ESTIMATE / lower bound, never a warehouse count. When absent, we fall back
to the boolean in-stock flag.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

from ..schema import ProductRecord, StockGranularity, to_float, to_int
from .base import BaseScraper
from .parsing import estimate_from_label, find_stock_label, first_key, iter_dicts_with_keys

log = logging.getLogger(__name__)


class BlinkitScraper(BaseScraper):
    PLATFORM = "blinkit"
    BASE_URL = "https://blinkit.com"
    API_URL_PATTERNS = [
        r"blinkit\.com/v\d+/search",          # /v6/search/products, /v1/layout/search
        r"blinkit\.com/v\d+/layout/search",
        r"blinkit\.com/v\d+/product",         # PDP data
        r"blinkit\.com/v\d+/layout/product",
    ]

    # ------------------------------------------------------------------ #

    async def set_location(self, pincode: str) -> None:
        await self.goto(self.BASE_URL)

        # Blinkit forces a location modal on first visit. Selectors are
        # fragile by nature; try several known variants.
        filled = await self.fill_first_available(
            [
                'input[name="select-locality"]',
                'input[placeholder*="delivery location" i]',
                'input[placeholder*="search delivery location" i]',
                '[data-testid="address-search-input"]',
            ],
            pincode,
        )
        if not filled:
            # Modal may be behind a "detect location / search manually" bar.
            await self.click_first_available(
                ['button:has-text("search delivery location")',
                 'div:has-text("Delivery in")'],
            )
            filled = await self.fill_first_available(
                ['input[name="select-locality"]',
                 'input[placeholder*="location" i]'],
                pincode,
            )
        if not filled:
            raise RuntimeError("[blinkit] location input not found — UI likely changed")

        # Pick the first autosuggest result for the typed pincode.
        picked = await self.click_first_available(
            [
                '[class*="LocationSearchList"] div[role="button"]',
                '[class*="address-list"] > div',
                'div[class*="LocationSearchBox"] li',
                # generic: first suggestion containing the pincode
                f'div:has-text("{pincode}") >> nth=0',
            ],
            timeout=8,
        )
        if not picked:
            raise RuntimeError(f"[blinkit] no location suggestion for pincode {pincode}")

        await self.page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        self.current_pincode = pincode
        log.info("[blinkit] location set to %s", pincode)

    async def search_product(self, query: str) -> list[ProductRecord]:
        pincode = self.require_location()
        since = self.clear_captured()
        await self.goto(f"{self.BASE_URL}/s/?q={quote(query)}")
        captured = await self.wait_for_json(r"blinkit\.com/v\d+/(layout/)?search", since=since)

        records: dict[str, ProductRecord] = {}
        for resp in captured:
            for rec in parse_search_payload(resp.data, pincode=pincode, query=query):
                key = rec.product_id or rec.product_name
                records.setdefault(key, rec)
        out = list(records.values())[: self.config.max_results_per_search]
        log.info("[blinkit] %r @ %s -> %d products", query, pincode, len(out))
        return out

    async def get_inventory(self, product_id: str) -> Optional[ProductRecord]:
        pincode = self.require_location()
        since = self.clear_captured()
        # PDP URL tolerates a placeholder slug; the numeric prid is what matters.
        await self.goto(f"{self.BASE_URL}/prn/p/prid/{product_id}")
        try:
            captured = await self.wait_for_json(
                r"blinkit\.com/v\d+/(layout/)?product", since=since
            )
        except TimeoutError:
            return None
        for resp in captured:
            for rec in parse_search_payload(resp.data, pincode=pincode, query=None):
                if rec.product_id == str(product_id):
                    return rec
        return None


# ---------------------------------------------------------------------- #
# Payload parsing (pure functions — unit-testable without a browser)
# ---------------------------------------------------------------------- #

def parse_search_payload(payload: Any, *, pincode: str, query: Optional[str]) -> list[ProductRecord]:
    """Normalize a Blinkit internal search/PDP payload.

    Handles both the legacy ``{"products": [...]}`` shape and the newer
    snippet/layout shape by recursively hunting for product-looking dicts.
    """
    records: list[ProductRecord] = []
    seen: set[str] = set()

    for node in iter_dicts_with_keys(payload, {"product_id", "prid"}):
        name = first_key(node, "name", "display_name", "title", "product_name")
        if not isinstance(name, str) or not name.strip():
            continue
        pid = str(first_key(node, "product_id", "prid"))
        if pid in seen:
            continue
        seen.add(pid)

        price = to_float(first_key(node, "price", "selling_price", "normalized_price"))
        mrp = to_float(first_key(node, "mrp", "max_retail_price"))
        inventory = to_int(node.get("inventory"))
        label = find_stock_label(node)
        label_estimate = estimate_from_label(label)

        in_stock = bool(first_key(node, "in_stock", "is_available"))
        if node.get("inventory") is not None:
            in_stock = inventory is not None and inventory > 0

        # Granularity: Blinkit's capped `inventory` int → ESTIMATE;
        # scarcity label → ESTIMATE; otherwise boolean-only.
        estimate = inventory if inventory is not None else label_estimate
        granularity = (
            StockGranularity.ESTIMATE if estimate is not None else StockGranularity.BOOLEAN
        )

        records.append(
            ProductRecord(
                platform="blinkit",
                product_name=name.strip(),
                brand=first_key(node, "brand", "brand_name"),
                price=price,
                mrp=mrp,
                discount_pct=None,  # derived in finalize()
                quantity_unit=first_key(node, "unit", "quantity", "weight"),
                in_stock=in_stock,
                stock_estimate=estimate,
                stock_granularity=granularity.value,
                pincode=pincode,
                product_id=pid,
                raw_stock_label=label,
                search_query=query,
            ).finalize()
        )
    return records
