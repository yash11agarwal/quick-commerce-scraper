"""Multi-platform Indian quick-commerce availability/price scraper.

IMPORTANT MAINTENANCE NOTE
--------------------------
None of the platforms covered here (Blinkit, Swiggy Instamart, Zepto,
BigBasket Now, Amazon Now, Flipkart Minutes) publish official public APIs.
Every adapter in ``qc_scraper.scrapers`` works by driving the real website
with Playwright and intercepting the site's *internal* (reverse-engineered)
JSON API responses. Those endpoints, URL shapes, and payload schemas can
change without notice; expect to re-verify the ``API_URL_PATTERNS`` and the
``parse_*`` functions in each adapter periodically.
"""

__version__ = "0.1.0"
