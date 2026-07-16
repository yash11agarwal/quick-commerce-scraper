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

## Current status (last live test pass)

This project reverse-engineers six actively-hostile-to-bots websites, so
"works" is a moving target. Here's the honest state as of the most recent
end-to-end test run against the real sites from a residential IP:

| Platform | Status | Notes |
|---|---|---|
| Blinkit | ✅ Working | Location + search both succeed; location step is a bit slow (~30s). |
| BigBasket Now | ✅ Working | Same as above; location step took ~100s in testing. |
| Swiggy Instamart | ❌ Blocked | Site returned what looked like a bot-detection/"request blocked" page before our selectors even got a chance. Likely needs a residential proxy, not a selector fix — see `swiggy_instamart.py`'s docstring. |
| Zepto | ❌ Broken | Location succeeds, but the search API endpoint we listen for never fires — it's likely moved. See `zepto.py`'s docstring; `API_URL_PATTERNS` has been broadened to capture diagnostic evidence on the next failed run. |
| Amazon Now | ❌ Broken | Was silently landing on the *regular* Amazon marketplace instead of the 15-minute storefront — this is now a loud, caught failure instead of silently-wrong data (see `amazon_now.py`). The real "Now" entry point still needs re-discovering live. |
| Flipkart Minutes | ❌ Broken | Was reporting "location set" successfully while the pincode was never actually accepted, so every search silently came back with 0 products. Now verified and will fail loudly instead. |

**Also found:** keyword search can grab the wrong SKU/pack-size for a query
(whatever the parser sees as the first/best match) — see "Exact-SKU
tracking" below for the fix.

Since these sites actively block non-residential traffic, whoever maintains
this next likely can't reproduce a live failure from wherever they're
working. That's what the debug-capture feature (below) is for.

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
main.py                      # CLI: one sweep = platforms × pincodes × queries (keyword search)
track_products.py            # CLI: one sweep over targets.xlsx (exact product URLs)
dashboard.py                 # local web dashboard (stdlib HTTP server + JSON API)
config.yaml                  # pincodes, queries, platforms, rate limits, retry
targets.xlsx                 # exact-SKU watchlist: Pincodes + Products sheets
qc_scraper/
  schema.py                  # ProductRecord + StockGranularity (normalized output)
  config.py                  # YAML → typed config
  storage.py                 # SQLite append-only time series
  targets.py                 # targets.xlsx loader + per-platform URL → product_id
  utils/
    rate_limiter.py          # per-domain min-delay + jitter (shared per run)
    retry.py                 # exponential backoff (2s/4s/8s…)
    user_agents.py           # UA rotation (one per browser session)
  scrapers/
    base.py                  # BaseScraper ABC: Playwright lifecycle +
                             #   network-response interception + goto/retry +
                             #   dump_debug() failure-diagnostics capture
    parsing.py               # defensive recursive JSON hunting, label parsing
    blinkit.py … flipkart_minutes.py   # one adapter per platform
web/index.html                # dashboard frontend (self-contained, no build step)
scripts/build_targets_template.py   # regenerates targets.xlsx if deleted
tests/                       # parser fixtures (canaries) + storage/targets roundtrip
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

**Windows prerequisite:** Playwright's dependencies (`greenlet`) are
compiled C extensions and need the **Microsoft Visual C++ Redistributable**
to load. If `python main.py` fails with `ImportError: DLL load failed while
importing _greenlet`, install it from
https://aka.ms/vs/17/release/vc_redist.x64.exe, restart, and try again —
this isn't a bug in the code, most fresh Windows installs simply don't have
it yet.

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

### Debugging a broken platform

Every `set_location()` failure, `search_product()` failure, and every
search that comes back with **zero results** (which is often a silent
failure rather than a raised error — the page just didn't do what the code
assumed) automatically saves a screenshot + the raw JSON the page loaded
into `./debug/<platform>/`:

```
debug/
  flipkart_minutes/
    location_110001_FAILED_20260715-013045.png
    location_110001_FAILED_20260715-013045.json
    location_110001_FAILED_20260715-013045.url.txt
```

This exists because these sites block non-residential IPs, so whoever is
fixing an adapter often can't reproduce the failure live themselves — the
screenshot shows exactly what the browser saw, and the `.json` file shows
every matching API response captured up to that point. Hand these three
files over instead of re-describing what happened in words.

### Exact-SKU tracking (targets.xlsx)

Keyword search (`config.yaml`'s `queries`) asks each platform's own search
ranking to pick a result — which can silently grab the wrong pack size or
a different brand than you meant. For a fixed watchlist of specific
products, **`targets.xlsx`** lets you paste the exact product page URL
instead, so there's no guessing:

```bash
python track_products.py --headed -v
```

Open `targets.xlsx` (an Instructions tab explains the process): list your
pincodes on the **Pincodes** tab, and on the **Products** tab, for each
product/platform combination, paste the URL of that product's own page
(open the product on the real site, copy the address bar) plus a `label`
you choose — use the *same* label for the same product across different
platforms so the dashboard groups and compares them together, exactly like
it does for keyword-search queries. Results append to the same
`data/observations.db`, so `python dashboard.py` shows both keyword-search
and exact-SKU rows.

If `targets.xlsx` ever gets deleted, regenerate it with
`python scripts/build_targets_template.py`.

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

## Bonus tool: LinkedIn job hunter & tracker (`jobs.py`)

This repo also ships a second, independent tool that reuses the same
patterns for job hunting — with **Excel as the whole interface**
(like `targets.xlsx`, but for both input and output): it pulls public
LinkedIn postings for your saved searches, scores them against your
keywords, and tracks each application through a pipeline
(`new → interested → applied → interviewing → offer/rejected`).

```bash
python jobs.py init      # creates job_tracker.xlsx — configure searches there
python jobs.py hunt      # pulls & ranks postings into the Jobs sheet
# ...work the pipeline in Excel (Status dropdown / Add Note column)...
python jobs.py sync      # absorbs your Excel edits, refreshes the sheets
```

Full docs: [docs/job_hunter.md](docs/job_hunter.md).

## Maintenance checklist (when an adapter breaks)

1. Run `python main.py --platform <name> --headed -v` and watch the browser.
2. If location setting fails: update the selector fallback lists in
   `set_location()` (UI churn).
3. If location works but no data: the internal endpoint moved — check
   devtools' Network tab and update `API_URL_PATTERNS`.
4. If data arrives but rows are empty/wrong: payload reshaped — update the
   `parse_*` function and its test fixture.
