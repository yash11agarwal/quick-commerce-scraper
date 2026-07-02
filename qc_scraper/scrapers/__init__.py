"""Platform adapter registry.

Keys match the ``platforms:`` section of config.yaml.
"""

from .base import BaseScraper, LocationNotSetError
from .amazon_now import AmazonNowScraper
from .bigbasket_now import BigBasketNowScraper
from .blinkit import BlinkitScraper
from .flipkart_minutes import FlipkartMinutesScraper
from .swiggy_instamart import SwiggyInstamartScraper
from .zepto import ZeptoScraper

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    BlinkitScraper.PLATFORM: BlinkitScraper,
    SwiggyInstamartScraper.PLATFORM: SwiggyInstamartScraper,
    ZeptoScraper.PLATFORM: ZeptoScraper,
    BigBasketNowScraper.PLATFORM: BigBasketNowScraper,
    AmazonNowScraper.PLATFORM: AmazonNowScraper,
    FlipkartMinutesScraper.PLATFORM: FlipkartMinutesScraper,
}

__all__ = [
    "BaseScraper",
    "LocationNotSetError",
    "SCRAPER_REGISTRY",
    "BlinkitScraper",
    "SwiggyInstamartScraper",
    "ZeptoScraper",
    "BigBasketNowScraper",
    "AmazonNowScraper",
    "FlipkartMinutesScraper",
]
