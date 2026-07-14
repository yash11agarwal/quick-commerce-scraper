"""Zepto (zeptonow.com) adapter.

REVERSE-ENGINEERED INTERNAL ENDPOINTS — no public API exists. Observed via
browser devtools (subject to change without notice):

- Search: ``api.zeptonow.com/api/v3/search`` (POST, paginated widget layout)
- PDP:    ``api.zeptonow.com/api/v*/...product...`` variants

Payload quirks:
- Monetary fields (``mrp``, ``sellingPrice``, ``discountedSellingPrice``)
  are in PAISE — divide by 100 for INR.
- Stock is exposed as ``outOfStock`` booleans; some payloads carry
  ``availableQuantity`` (capped, treat as ESTIMATE at best).

Location model: store ("storeId") is resolved from the chosen address;
we set it through the real location picker UI so all cookies/headers are
consistent.

KNOWN ISSUE (as of the first live test pass): location succeeds, but no
response ever matches the ``api.zeptonow.com/api/v\\d+/search`` pattern
within the timeout — the real search endpoint has likely moved (different
path, version, or even a different subdomain). API_URL_PATTERNS below has
been broadened to also capture any ``.../api/`` traffic on zeptonow.com so
the next failed run's ./debug/zepto/*.json dump shows every API call the
page actually made — that list is what a real fix should be based on,
rather than another guess.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

from ..schema import ProductRecord, StockGranularity, to_float, to_int
from .base import BaseScraper
from .parsing import dig, estimate_from_label, find_stock_label, first_key, iter_dicts_with_keys

log = logging.getLogger(__name__)

_PAISE = 100.0


class ZeptoScraper(BaseScraper):
    PLATFORM = "zepto"
    BASE_URL = "https://www.zeptonow.com"
    API_URL_PATTERNS = [
        r"api\.zeptonow\.com/api/v\d+/search",
        r"api\.zeptonow\.com/api/v\d+.*product",
        r"api\.zeptonow\.com/api/v\d+/inventory",
        # Diagnostic fallback (broad on purpose): captures ANY zeptonow.com
        # API call so a failed run's debug dump shows what the real search
        # endpoint is actually named now, instead of an empty dump.
        r"zeptonow\.com/api/",
    ]

    async def set_location(self, pincode: str) -> None:
        await self.goto(self.BASE_URL)

        # Open the location picker (header button or first-visit modal).
        await self.click_first_available(
            [
                'button:has-text("Select Location")',
                '[data-testid="user-address"]',
                'button[aria-label*="location" i]',
                'span:has-text("Select Location")',
            ],
        )
        filled = await self.fill_first_available(
            [
                'input[placeholder*="search" i]',
                '[data-testid="address-search-input"]',
                'input[type="text"]',
            ],
            pincode,
        )
        if not filled:
            raise RuntimeError("[zepto] location search input not found — UI likely changed")

        picked = await self.click_first_available(
            [
                '[data-testid="address-search-item"]',
                'div[class*="searchresult" i] > div',
                f'div:has-text("{pincode}") >> nth=0',
            ],
            timeout=8,
        )
        if not picked:
            raise RuntimeError(f"[zepto] no location suggestion for pincode {pincode}")

        # Zepto asks to confirm the pin on a map for new addresses.
        await self.click_first_available(
            ['button:has-text("Confirm & Continue")',
             'button:has-text("Confirm Location")'],
            timeout=8,
        )
        await self.page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        self.current_pincode = pincode
        log.info("[zepto] location set to %s", pincode)

    async def search_product(self, query: str) -> list[ProductRecord]:
        pincode = self.require_location()
        since = self.clear_captured()
        await self.goto(f"{self.BASE_URL}/search?query={quote(query)}")
        captured = await self.wait_for_json(
            r"api\.zeptonow\.com/api/v\d+/search", since=since
        )

        records: dict[str, ProductRecord] = {}
        for resp in captured:
            for rec in parse_search_payload(resp.data, pincode=pincode, query=query):
                records.setdefault(rec.product_id or rec.product_name, rec)
        out = list(records.values())[: self.config.max_results_per_search]
        log.info("[zepto] %r @ %s -> %d products", query, pincode, len(out))
        return out

    async def get_inventory(self, product_id: str) -> Optional[ProductRecord]:
        pincode = self.require_location()
        since = self.clear_captured()
        # PDP URLs are /pn/<slug>/pvid/<variant-uuid>; slug is cosmetic.
        await self.goto(f"{self.BASE_URL}/pn/p/pvid/{product_id}")
        try:
            captured = await self.wait_for_json(
                r"api\.zeptonow\.com/api/v\d+", since=since
            )
        except TimeoutError:
            return None
        for resp in captured:
            for rec in parse_search_payload(resp.data, pincode=pincode, query=None):
                if rec.product_id == str(product_id):
                    return rec
        return None


# ---------------------------------------------------------------------- #

def parse_search_payload(payload: Any, *, pincode: str, query: Optional[str]) -> list[ProductRecord]:
    """Normalize Zepto's widget-layout search payload.

    Product nodes look like ``{"productResponse": {"product": {...},
    "productVariant": {...}, "sellingPrice": ..., "outOfStock": bool}}``,
    but nesting above that layer changes often — so we hunt recursively.
    """
    records: list[ProductRecord] = []
    seen: set[str] = set()

    for node in iter_dicts_with_keys(payload, {"productResponse"}):
        pr = node.get("productResponse")
        if not isinstance(pr, dict):
            continue
        product = pr.get("product") or {}
        variant = pr.get("productVariant") or {}

        name = first_key(product, "name", "brandDisplayName") or pr.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        pid = str(first_key(pr, "id", "productVariantId") or first_key(variant, "id") or name)
        if pid in seen:
            continue
        seen.add(pid)

        # Prices arrive in paise.
        selling = to_float(first_key(pr, "discountedSellingPrice", "sellingPrice"))
        mrp = to_float(first_key(pr, "mrp") or first_key(variant, "mrp"))
        price = selling / _PAISE if selling is not None else None
        mrp = mrp / _PAISE if mrp is not None else None

        out_of_stock = bool(first_key(pr, "outOfStock") or False)
        qty = to_int(first_key(pr, "availableQuantity") or first_key(variant, "availableQuantity"))
        label = find_stock_label(pr) or find_stock_label(variant)
        estimate = qty if qty is not None else estimate_from_label(label)
        granularity = (
            StockGranularity.ESTIMATE if estimate is not None else StockGranularity.BOOLEAN
        )

        records.append(
            ProductRecord(
                platform="zepto",
                product_name=name.strip(),
                brand=first_key(product, "brand") or dig(product, "brand", "name"),
                price=price,
                mrp=mrp,
                discount_pct=None,
                quantity_unit=first_key(variant, "formattedPacksize", "packsize", "weightInGms"),
                in_stock=not out_of_stock,
                stock_estimate=estimate,
                stock_granularity=granularity.value,
                pincode=pincode,
                product_id=pid,
                raw_stock_label=label,
                search_query=query,
            ).finalize()
        )
    return records
