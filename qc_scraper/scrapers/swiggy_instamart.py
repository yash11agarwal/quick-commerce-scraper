"""Swiggy Instamart (swiggy.com/instamart) adapter.

REVERSE-ENGINEERED INTERNAL ENDPOINTS — no public API exists. Observed via
browser devtools (subject to change without notice):

- Search: ``swiggy.com/api/instamart/search?...`` (widget payload)
- Item:   ``swiggy.com/api/instamart/item/...`` / item-details variants

Location model: Swiggy resolves the address to lat/lng and a serviceable
Instamart store; context is carried in cookies set by the location picker,
so we drive the real UI.

Inventory granularity: item variations carry an ``inventory`` object which
usually only holds ``in_stock`` booleans; occasionally a small capped count
appears — recorded as ESTIMATE when present.

KNOWN ISSUE (as of the first live test pass): set_location() fails with
"location input not found" on both tested pincodes, and the tester observed
what looked like a "request blocked" page rather than the normal site. That
combination points at bot-detection kicking in before our selectors ever get
a chance to run — this is likely NOT a simple stale-selector fix, and may
need a residential proxy (see config.yaml `browser.proxy`) and/or slower,
more human-like interaction timing rather than a code change here. Next
diagnostic step: re-run with this platform only (`--platform
swiggy_instamart --headed -v`) and check ./debug/swiggy_instamart/ for the
screenshot captured at the failure point — if it shows a CAPTCHA or "unusual
traffic" page, that confirms bot-detection over a UI change.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

from ..schema import ProductRecord, StockGranularity, to_float, to_int
from .base import BaseScraper
from .parsing import dig, estimate_from_label, find_stock_label, first_key, iter_dicts_with_keys

log = logging.getLogger(__name__)


class SwiggyInstamartScraper(BaseScraper):
    PLATFORM = "swiggy_instamart"
    BASE_URL = "https://www.swiggy.com"
    API_URL_PATTERNS = [
        r"swiggy\.com/api/instamart/search",
        r"swiggy\.com/api/instamart/item",
        r"swiggy\.com/api/instamart/home",  # confirms store serviceability
    ]

    async def set_location(self, pincode: str) -> None:
        await self.goto(f"{self.BASE_URL}/instamart")

        # Open location picker (header or blocking modal on first visit).
        await self.click_first_available(
            [
                '[data-testid="set-gps-button"]',
                'div:has-text("Setup your delivery location") button',
                'span:has-text("Other")',
                'div[class*="address"] button',
                'button:has-text("Search Location")',
            ],
        )
        filled = await self.fill_first_available(
            [
                'input[placeholder*="search for area" i]',
                'input[placeholder*="Enter your delivery location" i]',
                '[data-testid="location-search-input"]',
                'input[type="text"]',
            ],
            pincode,
        )
        if not filled:
            raise RuntimeError("[swiggy_instamart] location input not found — UI likely changed")

        picked = await self.click_first_available(
            [
                '[data-testid="address-suggestion"] >> nth=0',
                'div[class*="LocationSearch"] li',
                f'div:has-text("{pincode}") >> nth=0',
            ],
            timeout=8,
        )
        if not picked:
            raise RuntimeError(f"[swiggy_instamart] no suggestion for pincode {pincode}")

        await self.click_first_available(
            ['button:has-text("Confirm Location")', 'button:has-text("Confirm")'],
            timeout=8,
        )
        await self.page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        self.current_pincode = pincode
        log.info("[swiggy_instamart] location set to %s", pincode)

    async def search_product(self, query: str) -> list[ProductRecord]:
        pincode = self.require_location()
        since = self.clear_captured()
        await self.goto(f"{self.BASE_URL}/instamart/search?custom_back=true&query={quote(query)}")
        captured = await self.wait_for_json(r"api/instamart/search", since=since)

        records: dict[str, ProductRecord] = {}
        for resp in captured:
            for rec in parse_search_payload(resp.data, pincode=pincode, query=query):
                records.setdefault(rec.product_id or rec.product_name, rec)
        out = list(records.values())[: self.config.max_results_per_search]
        log.info("[swiggy_instamart] %r @ %s -> %d products", query, pincode, len(out))
        return out

    async def get_inventory(self, product_id: str) -> Optional[ProductRecord]:
        pincode = self.require_location()
        since = self.clear_captured()
        await self.goto(f"{self.BASE_URL}/instamart/item/{product_id}")
        try:
            captured = await self.wait_for_json(r"api/instamart/(item|search)", since=since)
        except TimeoutError:
            return None
        for resp in captured:
            for rec in parse_search_payload(resp.data, pincode=pincode, query=None):
                if rec.product_id == str(product_id):
                    return rec
        return None


# ---------------------------------------------------------------------- #

def parse_search_payload(payload: Any, *, pincode: str, query: Optional[str]) -> list[ProductRecord]:
    """Normalize Instamart search widgets.

    Item nodes carry ``display_name`` + a ``variations`` list; each variation
    has ``price {mrp, offer_price}`` and ``inventory {in_stock}``. We take the
    first variation (the one the storefront shows by default).
    """
    records: list[ProductRecord] = []
    seen: set[str] = set()

    for node in iter_dicts_with_keys(payload, {"variations"}):
        name = first_key(node, "display_name", "name", "product_name_without_brand")
        variations = node.get("variations")
        if not isinstance(name, str) or not name.strip() or not isinstance(variations, list):
            continue
        if not variations or not isinstance(variations[0], dict):
            continue
        var = variations[0]

        pid = str(first_key(node, "product_id", "id") or first_key(var, "id") or name)
        if pid in seen:
            continue
        seen.add(pid)

        price = to_float(
            dig(var, "price", "offer_price") or dig(var, "price", "store_price")
        )
        mrp = to_float(dig(var, "price", "mrp"))
        inv = var.get("inventory") if isinstance(var.get("inventory"), dict) else {}
        in_stock = bool(first_key(inv, "in_stock", "available") or False)
        count_hint = to_int(first_key(inv, "remaining_count", "count", "quantity"))
        label = find_stock_label(var) or find_stock_label(node)
        estimate = count_hint if count_hint is not None else estimate_from_label(label)
        granularity = (
            StockGranularity.ESTIMATE if estimate is not None else StockGranularity.BOOLEAN
        )

        records.append(
            ProductRecord(
                platform="swiggy_instamart",
                product_name=name.strip(),
                brand=first_key(node, "brand", "brand_name"),
                price=price,
                mrp=mrp,
                discount_pct=None,
                quantity_unit=first_key(var, "quantity", "sku_quantity_with_combo", "weight"),
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
