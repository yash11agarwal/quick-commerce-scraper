#!/usr/bin/env python3
"""Run one exact-SKU tracking sweep from targets.xlsx.

This is the alternative to main.py's keyword search: instead of searching
for a term and hoping the parser picks the right result, every row in
targets.xlsx's "Products" sheet is an exact product page URL you copied
from your own browser — so the scraper always fetches that specific SKU.

Usage:
    python track_products.py                       # uses targets.xlsx, config.yaml
    python track_products.py --targets other.xlsx
    python track_products.py --platform blinkit     # only this platform's rows
    python track_products.py --headed -v            # watch it work

Results append to the same SQLite database as main.py (data/observations.db
by default) — each row's `search_query` field is set to the Products
sheet's `label` column, so the dashboard groups/filters exact-SKU rows
exactly like keyword-search rows.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import defaultdict

from qc_scraper.config import load_config
from qc_scraper.scrapers import SCRAPER_REGISTRY, BaseScraper
from qc_scraper.storage import ObservationStore
from qc_scraper.targets import TargetProduct, extract_product_id, load_targets
from qc_scraper.utils import DomainRateLimiter

log = logging.getLogger("qc_scraper.track_products")


async def track_platform(
    scraper_cls: type[BaseScraper],
    products: list[TargetProduct],
    pincodes: list[str],
    config,
    rate_limiter: DomainRateLimiter,
    store: ObservationStore,
) -> int:
    total = 0
    async with scraper_cls(config, rate_limiter) as scraper:
        for pincode in pincodes:
            try:
                await scraper.set_location(pincode)
            except Exception as exc:  # noqa: BLE001
                log.error("[%s] set_location(%s) failed: %s — skipping pincode",
                          scraper.PLATFORM, pincode, exc)
                await scraper.dump_debug(f"location_{pincode}_FAILED", str(exc))
                continue

            for product in products:
                try:
                    product_id = extract_product_id(product.platform, product.url)
                except ValueError as exc:
                    log.error("[%s] bad URL for %r: %s",
                              scraper.PLATFORM, product.label, exc)
                    continue
                try:
                    record = await scraper.get_inventory(product_id)
                except Exception as exc:  # noqa: BLE001
                    log.error("[%s] get_inventory(%s) for %r @ %s failed: %s",
                              scraper.PLATFORM, product_id, product.label, pincode, exc)
                    await scraper.dump_debug(
                        f"inventory_{pincode}_{product.label}_FAILED", str(exc))
                    continue
                if record is None:
                    log.warning("[%s] %r @ %s -> not found (page loaded but no product data)",
                                scraper.PLATFORM, product.label, pincode)
                    await scraper.dump_debug(
                        f"inventory_{pincode}_{product.label}_EMPTY", "get_inventory returned None")
                    continue
                record.search_query = product.label
                total += store.insert_many([record])
                log.info("[%s] %r @ %s -> ₹%s (%s)",
                         scraper.PLATFORM, product.label, pincode,
                         record.price, "in stock" if record.in_stock else "out of stock")
    return total


async def run_sweep(config, products: list[TargetProduct], pincodes: list[str],
                    only_platforms: list[str] | None = None) -> None:
    rate_limiter = DomainRateLimiter(
        min_delay_seconds=config.rate_limit.min_delay_seconds,
        jitter_seconds=config.rate_limit.jitter_seconds,
    )
    by_platform: dict[str, list[TargetProduct]] = defaultdict(list)
    for p in products:
        by_platform[p.platform].append(p)

    platforms = only_platforms or sorted(by_platform)
    unknown = set(platforms) - set(SCRAPER_REGISTRY)
    if unknown:
        raise SystemExit(f"unknown platform(s) in targets.xlsx: {sorted(unknown)}; "
                         f"valid: {sorted(SCRAPER_REGISTRY)}")

    with ObservationStore(config.sqlite_path) as store:
        for name in platforms:
            rows = by_platform.get(name, [])
            if not rows:
                continue
            log.info("=== %s (%d product%s) ===", name, len(rows), "" if len(rows) == 1 else "s")
            try:
                count = await track_platform(
                    SCRAPER_REGISTRY[name], rows, pincodes, config, rate_limiter, store)
                log.info("[%s] stored %d observations", name, count)
            except Exception as exc:  # noqa: BLE001 - isolate platform failures
                log.error("[%s] platform run failed entirely: %s", name, exc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config.yaml", help="path to config YAML")
    parser.add_argument("--targets", default="targets.xlsx", help="path to targets workbook")
    parser.add_argument(
        "--platform", action="append", dest="platforms", metavar="NAME",
        help="run only this platform (repeatable); default: every platform in targets.xlsx",
    )
    parser.add_argument("--headed", action="store_true",
                        help="run with a visible browser (debugging selectors)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        stream=sys.stderr,
    )

    config = load_config(args.config)
    if args.headed:
        config.browser.headless = False

    pincodes, products = load_targets(args.targets)
    if not pincodes:
        raise SystemExit(f"{args.targets}: Pincodes sheet has no pincodes filled in")
    if not products:
        raise SystemExit(f"{args.targets}: Products sheet has no product rows filled in")

    log.info("Loaded %d product(s) across %d pincode(s) from %s",
              len(products), len(pincodes), args.targets)
    log.info("Any failures or missing products save a screenshot + captured "
             "data under ./debug/<platform>/ for troubleshooting.")
    asyncio.run(run_sweep(config, products, pincodes, args.platforms))


if __name__ == "__main__":
    main()
