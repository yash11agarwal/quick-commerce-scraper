# quick-commerce-scraper

Modular Python tool that monitors **product availability, pricing, and stock
signals** across six Indian quick-commerce platforms:

| Platform | Adapter | Data source | Stock granularity exposed |
|---|---|---|---|
| Blinkit | `qc_scraper/scrapers/blinkit.py` | intercepted internal JSON (`/v*/search`, layout API) | capped `inventory` int → **estimate** |
| Swiggy Instamart | `swiggy_instamart.py` | intercepted internal JSON (`/api/instamart/search`) | mostly **boolean** `in_stock` |
| Zepto | `zepto.py` | intercepted internal JSON (`api.zeptonow.com/api/v3/search`) | **boolean** `outOfStock`; occasional capped qty → estimate |
| BigBasket Now | `bigbasket_now.py` | intercepted internal JSON (`listing-svc/v2/products`) | **boolean** (`avail_status` codes) |
| Amazon Now | `amazon_now.py` | hybrid: `/s/query` interception + DOM `data-*` fallback (see below) | **boolean**; "Only N left" strings → estimate |
| Flipkart Minutes | `flipkart_minutes.py` | intercepted internal JSON (`rome.api.flipkart.com .../page/fetch`) | **boolean** intents; "only N left" labels → estimate |

## ⚠️ Read this first

- **No official APIs.** Every adapter drives the real website with Playwright
  and intercepts *reverse-engineered internal endpoints* observed in browser
  devtools. These change without notice. Each adapter's module docstring
  lists exactly which endpoints it relies on — expect periodic maintenance,
  and treat the parser unit tests as canaries.
- **"Inventory levels" are mostly not a thing publicly.** These platforms
  almost never expose true unit counts. What you actually get is a boolean
  in-stock flag, sometimes a scarcity label ("Only 2 left"), and on Blinkit a
  backend-**capped** integer. The schema records `stock_granularity`
  (`boolean` / `estimate` / `count`) per row so you always know which kind of
  signal you're looking at; `stock_estimate` is only populated when the
  platform gave something beyond a boolean, and is a *hint/lower bound*, not
  a warehouse count.
- **Location first, always.** Catalogs, prices, and stock are hyperlocal
  (per dark store). Every adapter's `set_location(pincode)` must succeed
  before `search_product()` / `get_inventory()` — the base class enforces it.
- **Terms of service.** Automated access likely violates these platforms'
  ToS. Keep rate limits conservative, scrape only what you need, and make
  your own call on legal/ethical use. Datacenter IPs are commonly blocked;
  in practice a residential proxy (`browser.proxy` in config) is often
  required.

## Architecture

The end-to-end flow, as a diagram: **[docs/flowchart.md](docs/flowchart.md)**
(GitHub renders it inline).

```
main.py                      # CLI: one sweep = platforms × pincodes × queries
config.yaml                  # pincodes, queries, platforms, rate limits, retry
qc_scraper/
  schema.py                  # ProductRecord + StockGranularity (normalized output)
  config.py                  # YAML → typed config
  storage.py                 # SQLite append-only time series
  utils/
    rate_limiter.py          # per-domain min-delay + jitter (shared per run)
    retry.py                 # exponential backoff (2s/4s/8s…)
    user_agents.py           # UA rotation (one per browser session)
  scrapers/
    base.py                  # BaseScraper ABC: Playwright lifecycle +
                             #   network-response interception + goto/retry
    parsing.py               # defensive recursive JSON hunting, label parsing
    blinkit.py … flipkart_minutes.py   # one adapter per platform
tests/                       # parser fixtures (canaries) + storage roundtrip
```

**Why interception instead of HTML parsing:** all six sites are JS-rendered
SPAs whose product data arrives via internal JSON APIs. Listening for those
responses (`page.on("response")` with per-platform URL regexes) survives UI
redesigns far better than CSS selectors. The one partial exception is
Amazon, which is largely server-rendered; its adapter intercepts the
`/s/query` AJAX endpoint where possible and otherwise falls back to stable
`data-*` DOM attributes (documented in the adapter).

**Shared adapter interface** (`BaseScraper`):

```python
await scraper.set_location(pincode)        # required first step
await scraper.search_product(query)        # -> list[ProductRecord]
await scraper.get_inventory(product_id)    # -> ProductRecord | None
```

**Normalized schema** (every platform → same row shape):

```python
{platform, product_name, brand, price, mrp, discount_pct, quantity_unit,
 in_stock, stock_estimate, stock_granularity, pincode, timestamp,
 product_id, raw_stock_label, search_query}
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Usage

Edit `config.yaml` (pincodes, queries, platform toggles, rate limits), then:

```bash
python main.py                       # full sweep, all enabled platforms
python main.py --platform blinkit    # one platform only
python main.py --headed -v          # visible browser + debug logs (selector triage)
```

Results append to `data/observations.db` (SQLite, WAL). Because every sweep
*appends* timestamped rows, price/stock history falls out of a simple query:

```sql
SELECT timestamp, price, in_stock, stock_estimate, stock_granularity
FROM observations
WHERE platform = 'blinkit' AND product_id = '101' AND pincode = '110001'
ORDER BY timestamp;
```

### Dashboard (frontend)

A local web dashboard for browsing the collected data — no extra installs
needed (Python standard library only):

```bash
python dashboard.py           # serves data/observations.db and opens the browser
python dashboard.py --demo    # preview with synthetic data before your first scrape
```

It opens `http://127.0.0.1:8000` with: filters (product / pincode / 7-30-90
days), stat tiles, a "cheapest in-stock price by platform" daily chart, a
latest-snapshot table (with each row's `stock_granularity` badge), and a
click-through per-product price history chart. The page lives in
`web/index.html`; the JSON API in `dashboard.py`.

### Scheduling

```cron
# every 30 min
*/30 * * * * cd /path/to/quick-commerce-scraper && .venv/bin/python main.py >> scrape.log 2>&1
```

## Tests

```bash
pytest
```

Parser tests run against synthetic payloads shaped like each platform's real
responses. When an adapter starts returning nothing, the fix loop is: open
the site in devtools → find the new endpoint/payload shape → update the
adapter's `API_URL_PATTERNS` / parser → update the fixture.

## Maintenance checklist (when an adapter breaks)

1. Run `python main.py --platform <name> --headed -v` and watch the browser.
2. If location setting fails: update the selector fallback lists in
   `set_location()` (UI churn).
3. If location works but no data: the internal endpoint moved — check
   devtools' Network tab and update `API_URL_PATTERNS`.
4. If data arrives but rows are empty/wrong: payload reshaped — update the
   `parse_*` function and its test fixture.
