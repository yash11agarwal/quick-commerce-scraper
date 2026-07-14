"""Flipkart Minutes (flipkart.com hyperlocal storefront) adapter.

REVERSE-ENGINEERED INTERNAL ENDPOINTS — no public API exists. Observed via
browser devtools (subject to change without notice):

- Page data: ``1.rome.api.flipkart.com/api/4/page/fetch`` (POST) — the SPA
  fetches search results, listings, and PDPs through this single "page
  fetch" API; product data hides in widget slots inside the response.
- Search URL: ``flipkart.com/search?q=...&marketplace=HYPERLOCAL`` scopes
  results to the Minutes (hyperlocal) catalog. The marketplace param is
  rollout-dependent — re-verify in devtools if results look like regular
  marketplace listings.

Location model: Minutes availability is keyed off the delivery pincode set
in the header widget; without it the hyperlocal catalog is empty.

Inventory granularity: availability intents like ``IN_STOCK`` /
``OUT_OF_STOCK`` (boolean), plus occasional "Hurry, only N left!" strings —
never real counts.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

from ..schema import ProductRecord, StockGranularity, to_float
from .base import BaseScraper
from .parsing import dig, estimate_from_label, find_stock_label, first_key, iter_dicts_with_keys

log = logging.getLogger(__name__)


class FlipkartMinutesScraper(BaseScraper):
    PLATFORM = "flipkart_minutes"
    BASE_URL = "https://www.flipkart.com"
    API_URL_PATTERNS = [
        r"rome\.api\.flipkart\.com/api/\d+/page/fetch",
        r"rome\.api\.flipkart\.com/api/\d+/product",
    ]

    async def set_location(self, pincode: str) -> None:
        await self.goto(self.BASE_URL)

        # Close the login modal that flipkart pushes on first visit.
        await self.click_first_available(
            ['button:has-text("✕")', 'span[role="button"]:has-text("✕")'],
            timeout=5,
        )
        # Header pincode widget ("Deliver to ...").
        opened = await self.click_first_available(
            [
                'div:has-text("Select delivery location") >> nth=0',
                '[data-testid="delivery-pincode"]',
                'div[class*="pincode" i]',
                'span:has-text("Deliver to")',
            ],
        )
        filled = await self.fill_first_available(
            [
                'input[placeholder*="pincode" i]',
                'input[name="pincode"]',
                'input[type="tel"]',
            ],
            pincode,
        )
        if not opened and not filled:
            raise RuntimeError("[flipkart_minutes] pincode widget not found — UI likely changed")
        if filled:
            await self.click_first_available(
                ['button:has-text("Check")', 'span:has-text("Check")',
                 'button:has-text("Submit")'],
                timeout=8,
            )
        await self.page.wait_for_load_state("networkidle", timeout=self.timeout_ms)

        # This used to unconditionally assume success here regardless of
        # what the page actually did — exactly how a prior test run logged
        # "location set" while the pincode was never really accepted and
        # every subsequent search silently came back empty. Best-effort
        # verification: the pincode should now be visible somewhere on
        # the page (delivery banner, header chip, etc). The exact right
        # selector needs confirming against a live browser (see README
        # "Flipkart Minutes" section) — this text-anywhere check is a
        # placeholder that at least turns silent failure into a loud one.
        delivery_shown = await self.page.locator(f"text={pincode}").count() > 0
        if not delivery_shown:
            raise RuntimeError(
                f"[flipkart_minutes] pincode {pincode} not visible anywhere on "
                "the page after submitting — location was likely not accepted "
                "(selectors may be stale)"
            )
        self.current_pincode = pincode
        log.info("[flipkart_minutes] location set to %s", pincode)

    async def search_product(self, query: str) -> list[ProductRecord]:
        pincode = self.require_location()
        since = self.clear_captured()
        # marketplace=HYPERLOCAL scopes to the Minutes catalog (see docstring).
        await self.goto(
            f"{self.BASE_URL}/search?q={quote(query)}&marketplace=HYPERLOCAL"
        )
        captured = await self.wait_for_json(r"page/fetch", since=since)

        records: dict[str, ProductRecord] = {}
        for resp in captured:
            for rec in parse_page_fetch_payload(resp.data, pincode=pincode, query=query):
                records.setdefault(rec.product_id or rec.product_name, rec)
        out = list(records.values())[: self.config.max_results_per_search]
        log.info("[flipkart_minutes] %r @ %s -> %d products", query, pincode, len(out))
        return out

    async def get_inventory(self, product_id: str) -> Optional[ProductRecord]:
        pincode = self.require_location()
        since = self.clear_captured()
        # PDP URL tolerates a placeholder slug as long as pid is present.
        await self.goto(f"{self.BASE_URL}/p/p/itm?pid={product_id}&marketplace=HYPERLOCAL")
        try:
            captured = await self.wait_for_json(r"page/fetch|/product", since=since)
        except TimeoutError:
            return None
        for resp in captured:
            for rec in parse_page_fetch_payload(resp.data, pincode=pincode, query=None):
                if rec.product_id == str(product_id):
                    return rec
        return None


# ---------------------------------------------------------------------- #

def parse_page_fetch_payload(payload: Any, *, pincode: str, query: Optional[str]) -> list[ProductRecord]:
    """Normalize product widgets out of Flipkart's ``page/fetch`` response.

    Product nodes live inside widget slots as ``productInfo.value`` dicts
    with ``id``, ``titles {title}``, ``pricing {finalPrice {value},
    mrp {value}}``, ``availability {intent}``. Slot nesting varies per page
    type, so we hunt recursively for dicts that carry ``titles``+``pricing``.
    """
    records: list[ProductRecord] = []
    seen: set[str] = set()

    for node in iter_dicts_with_keys(payload, {"titles"}):
        titles = node.get("titles")
        pricing = node.get("pricing")
        if not isinstance(titles, dict) or not isinstance(pricing, dict):
            continue
        name = first_key(titles, "title", "newTitle")
        if not isinstance(name, str) or not name.strip():
            continue
        pid = str(first_key(node, "id", "productId", "pid") or name)
        if pid in seen:
            continue
        seen.add(pid)

        price = to_float(dig(pricing, "finalPrice", "value"))
        mrp = to_float(dig(pricing, "mrp", "value") or dig(pricing, "mrp"))

        avail = node.get("availability") if isinstance(node.get("availability"), dict) else {}
        intent = str(first_key(avail, "intent", "status") or "").upper()
        in_stock = intent != "OUT_OF_STOCK" if intent else True
        label = find_stock_label(avail) or find_stock_label(node) or first_key(titles, "subtitle")
        estimate = estimate_from_label(label if isinstance(label, str) else None)
        granularity = (
            StockGranularity.ESTIMATE if estimate is not None else StockGranularity.BOOLEAN
        )

        records.append(
            ProductRecord(
                platform="flipkart_minutes",
                product_name=name.strip(),
                brand=first_key(node, "brand", "brandName") or name.split()[0],
                price=price,
                mrp=mrp,
                discount_pct=None,
                quantity_unit=first_key(titles, "subtitle") if _looks_like_unit(first_key(titles, "subtitle")) else None,
                in_stock=in_stock,
                stock_estimate=estimate,
                stock_granularity=granularity.value,
                pincode=pincode,
                product_id=pid,
                raw_stock_label=label if isinstance(label, str) else None,
                search_query=query,
            ).finalize()
        )
    return records


def _looks_like_unit(text) -> bool:
    if not isinstance(text, str):
        return False
    return any(u in text.lower() for u in ("g", "kg", "ml", "l", "pack", "pcs", "piece"))
