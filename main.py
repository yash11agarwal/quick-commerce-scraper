#!/usr/bin/env python3
"""Run one scrape sweep: every enabled platform x pincode x query.

Designed to be invoked from cron/systemd on a schedule, e.g.:

    */30 * * * *  cd /path/to/quick-commerce-scraper && .venv/bin/python main.py

Each sweep appends timestamped rows to SQLite, building the price/stock
time series. Platforms run sequentially and failures are isolated: one
broken adapter (or one un-serviceable pincode) never aborts the sweep.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from qc_scraper.config import load_config
from qc_scraper.scrapers import SCRAPER_REGISTRY, BaseScraper
from qc_scraper.storage import ObservationStore
from qc_scraper.utils import DomainRateLimiter

log = logging.getLogger("qc_scraper")


async def run_platform(
    scraper_cls: type[BaseScraper],
    config,
    rate_limiter: DomainRateLimiter,
    store: ObservationStore,
) -> int:
    """Scrape all (pincode x query) combos for one platform. Returns row count."""
    total = 0
    async with scraper_cls(config, rate_limiter) as scraper:
        for pincode in config.pincodes:
            try:
                # Required first step: catalogs/prices/stock are hyperlocal.
                await scraper.set_location(pincode)
            except Exception as exc:  # noqa: BLE001
                log.error("[%s] set_location(%s) failed: %s — skipping pincode",
                          scraper.PLATFORM, pincode, exc)
                continue
            for query in config.queries:
                try:
                    records = await scraper.search_product(query)
                except Exception as exc:  # noqa: BLE001
                    log.error("[%s] search %r @ %s failed: %s",
                              scraper.PLATFORM, query, pincode, exc)
                    continue
                total += store.insert_many(records)
    return total


async def run_sweep(config, only_platforms: list[str] | None = None) -> None:
    rate_limiter = DomainRateLimiter(
        min_delay_seconds=config.rate_limit.min_delay_seconds,
        jitter_seconds=config.rate_limit.jitter_seconds,
    )
    platforms = only_platforms or config.enabled_platforms
    unknown = set(platforms) - set(SCRAPER_REGISTRY)
    if unknown:
        raise SystemExit(f"unknown platform(s): {sorted(unknown)}; "
                         f"valid: {sorted(SCRAPER_REGISTRY)}")

    with ObservationStore(config.sqlite_path) as store:
        for name in platforms:
            scraper_cls = SCRAPER_REGISTRY[name]
            log.info("=== %s ===", name)
            try:
                rows = await run_platform(scraper_cls, config, rate_limiter, store)
                log.info("[%s] stored %d observations", name, rows)
            except Exception as exc:  # noqa: BLE001 - isolate platform failures
                log.error("[%s] platform run failed entirely: %s", name, exc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config.yaml", help="path to config YAML")
    parser.add_argument(
        "--platform", action="append", dest="platforms", metavar="NAME",
        help="run only this platform (repeatable); default: all enabled in config",
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
    if not config.pincodes or not config.queries:
        raise SystemExit("config needs at least one pincode and one query")

    asyncio.run(run_sweep(config, args.platforms))


if __name__ == "__main__":
    main()
