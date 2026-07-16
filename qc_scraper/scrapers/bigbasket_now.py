"""BigBasket Now (bigbasket.com quick-delivery / "BB Now") adapter.

REVERSE-ENGINEERED INTERNAL ENDPOINTS — no public API exists. Observed via
browser devtools (subject to change without notice):

- Search: ``bigbasket.com/listing-svc/v2/products?type=ps&q=...``
- PDP:    ``bigbasket.com/product-svc/v2/products/...``

Notes:
- BB Now shares the bigbasket.com catalog; quick-commerce eligibility shows
  up per-SKU (express/"bbnow" tags on availability blocks). Availability is
  exposed as coded strings: ``avail_status == "001"`` means in stock.
- Prices arrive as strings inside ``pricing.discount`` blocks.
- No unit counts are exposed — boolean granularity only, except occasional
  scarcity labels.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

from ..schema import ProductRecord, StockGranularity, to_float
from .base import BaseScraper
from .parsing import dig, estimate_from_label, find_stock_label, first_key, iter_dicts_with_keys

log = logging.getLogger(__name__)


class BigBasketNowScraper(BaseScraper):
    PLATFORM = "bigbasket_now"
    BASE_URL = "https://www.bigbasket.com"
    API_URL_PATTERNS = [
        r"bigbasket\.com/listing-svc/v\d+/products",
        r"bigbasket\.com/product-svc/v\d+/products",
    ]

    async def set_location(self, pincode: str) -> None:
        await self.goto(self.BASE_URL)

        # Header location picker ("Select Location" / current city chip). This
        # click MUST succeed before we touch any input box: a live test run
        # showed that when this click silently failed, the code fell through
        # to the generic `input[type="text"]` fallback below and typed the
        # pincode into the main product-search bar instead of the location
        # box (BigBasket's search bar happily accepts free text, so nothing
        # errored — it just silently searched for "700048" as a product).
        opened = await self.click_first_available(
            [
                'text="Select Location"',
                'button:has-text("Select Location")',
                '[data-testid="delivery-location"]',
                'div[class*="DeliveryLocation"]',
                'div:has-text("Delivery in") >> nth=0',
            ],
        )
        if not opened:
            raise RuntimeError(
                "[bigbasket_now] could not open the location picker — "
                "UI likely changed (see README maintenance checklist)"
            )

        # No generic `input[type="text"]` fallback here on purpose — that was
        # the bug. If none of these specific selectors match, we want a loud
        # failure, not a silent mis-fill into the wrong box.
        filled = await self.fill_first_available(
            [
                'input[placeholder*="pincode" i]',
                'input[placeholder*="location" i]',
                'input[placeholder*="Search for area" i]',
                'input[placeholder*="Search delivery" i]',
            ],
            pincode,
        )
        if not filled:
            raise RuntimeError("[bigbasket_now] location input not found — UI likely changed")

        # Pick the first suggestion. Same rule as blinkit.py: NEVER use a
        # `has-text("<pincode>")` fallback here — it matches the container
        # of the box the pincode was just typed into, "succeeds" without
        # selecting anything, and the run silently proceeds on the wrong
        # (default) store location.
        picked = await self.click_first_available(
            [
                'ul[class*="suggestion" i] li',
                '[data-testid="location-suggestion"] >> nth=0',
                'div[class*="LocationModal" i] li',
            ],
            timeout=8,
        )
        if not picked:
            # Selector-independent fallback: pick the first autosuggest
            # entry via keyboard. Verified below.
            await self.page.keyboard.press("ArrowDown")
            await self.page.wait_for_timeout(300)
            await self.page.keyboard.press("Enter")

        await self.page.wait_for_load_state("networkidle", timeout=self.timeout_ms)

        # Verify the location actually took: the header chip should no longer
        # say "Select Location" (it shows the chosen area once set).
        still_unset = await self.page.locator('text="Select Location"').count()
        if still_unset:
            raise RuntimeError(
                f"[bigbasket_now] header still says 'Select Location' after "
                f"picking a suggestion for {pincode} — location not applied"
            )
        self.current_pincode = pincode
        log.info("[bigbasket_now] location set to %s", pincode)

    async def search_product(self, query: str) -> list[ProductRecord]:
        pincode = self.require_location()
        since = self.clear_captured()
        await self.goto(f"{self.BASE_URL}/ps/?q={quote(query)}")
        captured = await self.wait_for_json(r"listing-svc/v\d+/products", since=since)

        records: dict[str, ProductRecord] = {}
        for resp in captured:
            for rec in parse_search_payload(resp.data, pincode=pincode, query=query):
                records.setdefault(rec.product_id or rec.product_name, rec)
        out = list(records.values())[: self.config.max_results_per_search]
        log.info("[bigbasket_now] %r @ %s -> %d products", query, pincode, len(out))
        return out

    async def get_inventory(self, product_id: str) -> Optional[ProductRecord]:
        pincode = self.require_location()
        since = self.clear_captured()
        # PDP path is /pd/<id>/<slug>/; slug is cosmetic.
        await self.goto(f"{self.BASE_URL}/pd/{product_id}/p/")
        try:
            captured = await self.wait_for_json(
                r"(product-svc|listing-svc)/v\d+/products", since=since
            )
        except TimeoutError:
            return None
        for resp in captured:
            for rec in parse_search_payload(resp.data, pincode=pincode, query=None):
                if rec.product_id == str(product_id):
                    return rec
        return None


# ---------------------------------------------------------------------- #

_IN_STOCK_CODES = {"001"}  # observed: "001" in stock, "002"/"003" unavailable


def parse_search_payload(payload: Any, *, pincode: str, query: Optional[str]) -> list[ProductRecord]:
    """Normalize BigBasket ``listing-svc`` product nodes.

    Nodes carry ``desc`` (name), ``brand {name}``, ``pricing.discount
    {mrp, prim_price {sp}}``, ``w`` (pack size), ``availability
    {avail_status}``.
    """
    records: list[ProductRecord] = []
    seen: set[str] = set()

    for node in iter_dicts_with_keys(payload, {"avail_status", "availability"}):
        # Product nodes have desc + pricing; availability sub-dicts alone don't.
        name = first_key(node, "desc", "p_desc", "name")
        if not isinstance(name, str) or not name.strip():
            continue
        pid = str(first_key(node, "id", "sku", "sku_id") or name)
        if pid in seen:
            continue
        seen.add(pid)

        price = to_float(
            dig(node, "pricing", "discount", "prim_price", "sp")
            or dig(node, "pricing", "sp")
        )
        mrp = to_float(dig(node, "pricing", "discount", "mrp") or dig(node, "pricing", "mrp"))

        avail = node.get("availability") if isinstance(node.get("availability"), dict) else {}
        status = str(first_key(avail, "avail_status", "status") or "")
        in_stock = status in _IN_STOCK_CODES if status else bool(avail)
        label = find_stock_label(avail) or find_stock_label(node)
        estimate = estimate_from_label(label)
        granularity = (
            StockGranularity.ESTIMATE if estimate is not None else StockGranularity.BOOLEAN
        )

        records.append(
            ProductRecord(
                platform="bigbasket_now",
                product_name=name.strip(),
                brand=dig(node, "brand", "name") or first_key(node, "brand_name"),
                price=price,
                mrp=mrp,
                discount_pct=None,
                quantity_unit=first_key(node, "w", "weight", "pack_desc"),
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
