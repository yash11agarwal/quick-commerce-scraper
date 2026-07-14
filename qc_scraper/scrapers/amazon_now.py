"""Amazon Now (amazon.in quick-commerce storefront) adapter.

REVERSE-ENGINEERED — no public API exists for the retail site. Amazon is the
odd one out in this suite: amazon.in is largely SERVER-RENDERED, not a pure
JSON-API SPA, so clean network interception is only partially possible:

- ``amazon.in/s/query`` fires for AJAX search refinements/pagination and
  returns JSON-wrapped HTML fragments (intercepted below when present).
- Initial search results arrive as server-rendered HTML, so this adapter
  falls back to extracting from stable ``data-*`` attributes in the DOM
  (``data-component-type="s-search-result"``, ``data-asin``) — the least
  churn-prone HTML hooks Amazon exposes.

The "Amazon Now" (15-minute delivery) storefront is a filtered view of the
main catalog; its store alias/param has changed during rollout and may need
updating in ``NOW_SEARCH_PARAMS`` below.

Inventory granularity: Amazon exposes booleans plus occasional "Only N left
in stock" strings on PDPs — never counts. Recorded as ESTIMATE when the
string is present, else BOOLEAN.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import quote

from ..schema import ProductRecord, StockGranularity, to_float
from .base import BaseScraper
from .parsing import estimate_from_label

log = logging.getLogger(__name__)


class AmazonNowScraper(BaseScraper):
    PLATFORM = "amazon_now"
    BASE_URL = "https://www.amazon.in"
    API_URL_PATTERNS = [
        r"amazon\.in/s/query",  # AJAX search fragments (JSON-wrapped HTML)
    ]

    # Store filter for the quick-commerce storefront. REVERSE-ENGINEERED and
    # rollout-dependent; verify in devtools if results look like the full
    # marketplace instead of the Now/Fresh catalog.
    NOW_SEARCH_PARAMS = "i=nowstore"

    async def set_location(self, pincode: str) -> None:
        await self.goto(self.BASE_URL)

        # The "glow" widget (top-left delivery location) — its element ids
        # have been stable for years, unlike most Amazon markup.
        opened = await self.click_first_available(
            ["#nav-global-location-popover-link", "#glow-ingress-block"],
            timeout=10,
        )
        if not opened:
            raise RuntimeError("[amazon_now] glow location widget not found — UI likely changed")

        filled = await self.fill_first_available(
            ["#GLUXZipUpdateInput", 'input[aria-label*="pin code" i]'],
            pincode,
            timeout=10,
        )
        if not filled:
            raise RuntimeError("[amazon_now] pincode input not found in glow modal")

        await self.click_first_available(
            ['#GLUXZipUpdate input[type="submit"]',
             "#GLUXZipUpdate",
             'span:has-text("Apply")'],
            timeout=8,
        )
        # Dismiss the "Done"/continue confirmation if shown.
        await self.click_first_available(
            ['button[name="glowDoneButton"]', "#GLUXConfirmClose", 'button:has-text("Done")'],
            timeout=5,
        )
        await self.page.wait_for_load_state("networkidle", timeout=self.timeout_ms)

        # Verify the header now shows our pincode; otherwise Amazon silently
        # kept the old location and every subsequent price/stock row would be
        # silently wrong. This used to only log a warning and continue —
        # that's exactly how a prior test run ended up quietly scraping the
        # regular Amazon marketplace instead of the pincode's Now catalog.
        header = await self.page.locator("#glow-ingress-line2").first.text_content()
        if not header or pincode not in header:
            raise RuntimeError(
                f"[amazon_now] location not applied — header shows {header!r}, "
                f"expected pincode {pincode} (glow modal may have changed)"
            )
        self.current_pincode = pincode
        log.info("[amazon_now] location set to %s", pincode)

    async def search_product(self, query: str) -> list[ProductRecord]:
        pincode = self.require_location()
        self.clear_captured()
        await self.goto(f"{self.BASE_URL}/s?k={quote(query)}&{self.NOW_SEARCH_PARAMS}")
        await self.page.wait_for_selector(
            '[data-component-type="s-search-result"]', timeout=self.timeout_ms
        )

        # KNOWN-UNVERIFIED: NOW_SEARCH_PARAMS ("i=nowstore") was a guess at
        # the quick-commerce storefront filter and has been observed to
        # silently fall through to the regular Amazon marketplace instead —
        # i.e. this whole adapter may currently be scraping the wrong
        # catalog without any error. Rather than trust the URL param, look
        # for an on-page signal that this is actually the fast-delivery
        # storefront; if it's not there, fail loudly instead of returning
        # mislabeled data. The exact selector/text below needs confirming
        # against a live browser (see README "Amazon Now" section) — this
        # is a best-effort placeholder, not a confirmed fix.
        is_now_storefront = await self.page.locator(
            'text=/now|15[- ]min|fast delivery/i'
        ).count() > 0
        if not is_now_storefront:
            raise RuntimeError(
                "[amazon_now] search results don't show any Now/15-min "
                "storefront signal — likely landed on the regular Amazon "
                "marketplace instead; NOW_SEARCH_PARAMS needs re-verifying "
                "against the live site"
            )

        records = await self._extract_search_results_from_dom(pincode, query)
        out = records[: self.config.max_results_per_search]
        log.info("[amazon_now] %r @ %s -> %d products", query, pincode, len(out))
        return out

    async def _extract_search_results_from_dom(
        self, pincode: str, query: Optional[str]
    ) -> list[ProductRecord]:
        # HTML-fallback path (see module docstring). Uses data-* attributes
        # and a11y markup, which churn far less than Amazon's class names.
        raw = await self.page.evaluate(
            """() => Array.from(
                document.querySelectorAll('[data-component-type="s-search-result"]')
            ).map(el => ({
                asin: el.getAttribute('data-asin'),
                title: el.querySelector('h2')?.innerText?.trim() ?? null,
                price: el.querySelector('.a-price > .a-offscreen')?.innerText ?? null,
                mrp: el.querySelector('.a-price.a-text-price > .a-offscreen')?.innerText ?? null,
                unavailable: /currently unavailable|out of stock/i.test(el.innerText),
                stockLabel: (el.innerText.match(/only\\s*\\d+\\s*left[^\\n]*/i) || [null])[0],
            }))"""
        )
        records = []
        for item in raw:
            if not item.get("asin") or not item.get("title"):
                continue
            label = item.get("stockLabel")
            estimate = estimate_from_label(label)
            records.append(
                ProductRecord(
                    platform="amazon_now",
                    product_name=item["title"],
                    brand=_brand_from_title(item["title"]),
                    price=to_float(item.get("price")),
                    mrp=to_float(item.get("mrp")) or to_float(item.get("price")),
                    discount_pct=None,
                    quantity_unit=_unit_from_title(item["title"]),
                    in_stock=not item.get("unavailable", False),
                    stock_estimate=estimate,
                    stock_granularity=(
                        StockGranularity.ESTIMATE if estimate is not None
                        else StockGranularity.BOOLEAN
                    ).value,
                    pincode=pincode,
                    product_id=item["asin"],
                    raw_stock_label=label,
                    search_query=query,
                ).finalize()
            )
        return records

    async def get_inventory(self, product_id: str) -> Optional[ProductRecord]:
        pincode = self.require_location()
        await self.goto(f"{self.BASE_URL}/dp/{product_id}")
        try:
            await self.page.wait_for_selector("#productTitle", timeout=self.timeout_ms)
        except Exception:  # noqa: BLE001
            return None
        data = await self.page.evaluate(
            """() => ({
                title: document.querySelector('#productTitle')?.innerText?.trim() ?? null,
                price: document.querySelector('#corePrice_feature_div .a-offscreen, .a-price > .a-offscreen')?.innerText ?? null,
                mrp: document.querySelector('.basisPrice .a-offscreen, .a-price.a-text-price > .a-offscreen')?.innerText ?? null,
                availability: document.querySelector('#availability')?.innerText?.trim() ?? null,
            })"""
        )
        if not data.get("title"):
            return None
        availability = data.get("availability") or ""
        in_stock = not re.search(r"unavailable|out of stock", availability, re.IGNORECASE)
        estimate = estimate_from_label(availability)
        return ProductRecord(
            platform="amazon_now",
            product_name=data["title"],
            brand=_brand_from_title(data["title"]),
            price=to_float(data.get("price")),
            mrp=to_float(data.get("mrp")) or to_float(data.get("price")),
            discount_pct=None,
            quantity_unit=_unit_from_title(data["title"]),
            in_stock=in_stock,
            stock_estimate=estimate,
            stock_granularity=(
                StockGranularity.ESTIMATE if estimate is not None else StockGranularity.BOOLEAN
            ).value,
            pincode=pincode,
            product_id=product_id,
            raw_stock_label=availability or None,
        ).finalize()


_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?\s?(?:x\s?\d+\s?)?(?:kg|g|gm|gms|ml|l|litre|liter|pcs|pieces|pack of \d+))",
    re.IGNORECASE,
)


def _unit_from_title(title: str) -> Optional[str]:
    m = _UNIT_RE.search(title)
    return m.group(1) if m else None


def _brand_from_title(title: str) -> Optional[str]:
    # Amazon search results don't carry a structured brand field; first token
    # of the title is right often enough for grocery SKUs.
    first = title.split(",")[0].split()[:1]
    return first[0] if first else None
