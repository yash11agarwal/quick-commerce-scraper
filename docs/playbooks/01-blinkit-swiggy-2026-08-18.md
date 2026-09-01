# Q-Commerce Scraper Playbook: Blinkit and Swiggy Instamart

Captured 18 August 2026. Benchmark case: pincode **700048** (Patipukur / Kolkata Station Rd, South Dumdum, West Bengal), search term **"Mango"**.

Findings marked CONFIRMED were observed directly in live sessions or fetched from Swiggy's shipped bundles. Everything is now CONFIRMED, including the 700048 store id and a live-verified search POST. One nuance: Swiggy's location and session cookies are HttpOnly, so capture them at jar level, see 4.12.

---

## 1. Purpose

Replicate, without a Firecrawl dependency, the extraction approach that worked against Blinkit and Swiggy Instamart. This document covers the interaction flow per platform, where the usable data actually lives, and the failure modes that produce silently wrong data.

---

## 2. What Firecrawl does under the hood

Worth understanding because the approach is directly reproducible with Playwright.

| Element | Observed | Local equivalent |
|---|---|---|
| Browser | Hosted Chromium exposed at `wss://browser.firecrawl.dev/cdp/<id>?token=<t>` | `chromium.launch()` or `connect_over_cdp()` |
| Live view | `/screencast/<id>` endpoint | `Page.startScreencast`, or just headed mode |
| Action targeting | Playwright ARIA snapshot with `[ref=eNN]` annotations, model picks a ref | `get_by_role`, `get_by_placeholder`, `get_by_text` |
| Loop | snapshot, choose ref, act, re-snapshot | Explicit locator calls |
| Egress | `location: {country: "IN"}` plus `proxy: auto` | India-resident proxy or India-hosted VM |
| Session | Bounded subprocess, `exitCode` / `killed` / `stderr` returned | Your own process management |

Sample of the snapshot format the model acts on:

```
- textbox "search delivery location" [ref=e115]: 700048
- generic "PatipukurKolkata, West Bengal 700048, India" [ref=e94] clickable [cursor:pointer, onclick]
- button "ADD" [ref=e36]
```

No CSS selectors appear anywhere in the trace. That is why it survives both sites' hashed class names. Reproduce this by using accessibility and text locators, not class selectors.

**Reliability note.** Session creation failed 9 times across this working session and succeeded 3 times, with no change to the request between failures and successes. Treat browser acquisition as unreliable. Wrap it in retry with exponential backoff. A failure to acquire a session is not evidence that the target site is blocking you.

---

## 3. Blinkit playbook

### 3.1 Flow (CONFIRMED)

1. Load `https://blinkit.com`. Dismiss any app-download banner.
2. Click the delivery location selector in the header. Label is "Select Location" when unset, or "Delivery in X minutes" plus the address when set.
3. Type the pincode into the input with placeholder `search delivery location`.
4. Wait for autocomplete. Select deterministically. See 3.3.
5. Page reloads with location applied. Header shows `Delivery in 20 minutes` plus the full address.
6. Click the header search input (placeholder `Search for atta dal and more`), type the query, press Enter.
7. Scroll to load additional cards. Results render progressively.

### 3.2 Where the data lives (CONFIRMED)

**Read the Redux store, not the DOM.**

```
window.__reduxStore__.getState().ui.search.searchProductBffData.snippets[]
```

The DOM gives name, pack, price, MRP and a rendered "Out of Stock" label. The Redux store gives all of that plus exact integer inventory and every ID you need.

Store slices available at `getState()`:

- Top level: `data`, `ui`, `modal`, `screen`, `browser`, `api`, `analytics`
- Under `data`: `ua`, `featureFlags`, `deviceId`, `chainId`, `auth`, `user`, `location`, `merchant`, `categories`, `recipes`, `mainConfig`, `cart`, `addresses`, `addressesV2`, `search`, `seo`, `persistentKeys`, `secondaryData`, `analyticsAttributes`, `eta`, `print`, `assistReducer`, `ambulanceMetrics`

Each snippet has four top-level keys: `data`, `tracking`, `widget_type`, `layout_config`. Widget types seen: `product_card_snippet_type_2`, `grid_container_vr`, `image_text_vr_type_header`. Filter on presence of `data.product_id` to isolate real products.

Product fields under `snippet.data`:

| Field | Path | Notes |
|---|---|---|
| name | `data.name.text` | |
| display_name | `data.display_name.text` | |
| brand_name | `data.brand_name` | |
| variant | `data.variant.text` | pack size |
| normal_price | `data.normal_price.text` | selling price, prefixed with the rupee symbol |
| mrp | `data.mrp.text` | null when no MRP is shown |
| offer_tag | `data.offer_tag` | |
| offer | `data.offer` | null on all sampled rows |
| inventory | `data.inventory` | integer, the important one |
| is_sold_out | `data.is_sold_out` | unreliable, see 3.4 |
| product_state | `data.product_state` | `available` or `out_of_stock` |
| product_id | `data.product_id` | string |
| merchant_id | `data.merchant_id` | store key |
| merchant_type | `data.merchant_type` | value not yet sampled |
| group_id | `data.group_id` | integer, groups pack variants |
| eta_tag | `data.eta_tag` | |
| eta_identifier | `data.eta_identifier` | |
| rating | `data.rating` | |
| media_container | `data.media_container` | |
| image | `data.image` | |
| product_badges | `data.product_badges` | |
| overlay_badges | `data.overlay_badges` | |
| max_count | `data.stepper_data_v2.max_count` | mirrors inventory exactly, derived |
| cart_item | `data.atc_action.add_to_cart.cart_item` | holds product_name, display_name |
| meta | `data.meta` | merchant_id, product_id |
| click_action | `data.click_action` | |
| cta | `data.cta` | |
| ui_config | `data.ui_config` | |

Tracking fields under `snippet.tracking`: `widget_meta`, `impression_map`, `click_map`, `entry_source_map`, `common_attributes`, `interactions_map`.

### 3.3 Suggestion selection

700048 returned four suggestions, including **Purani Basti, Patehra, Maihar, Madhya Pradesh**. Selecting the first result would have silently scraped the wrong state.

Match on pincode plus expected state or district. Hard fail if nothing matches. Never fall through to index 0.

### 3.4 Gotchas

- **`is_sold_out` is false on every row**, including the zero-inventory out-of-stock item. Key availability off `inventory == 0` or `product_state == "out_of_stock"` only. This field will quietly corrupt an availability panel.
- `max_count` mirrors `inventory` exactly. Do not store both.
- `product_id` is a **string**, `group_id` is an **integer**. Short IDs like `298` and `18612` were observed. Loose typing in SQLite or Excel will mangle these. Type the column as text.
- Price and MRP remain populated at zero inventory. Presence of a price is not a proxy for availability.
- `merchant_id` was 30872 for 19 of 20 rows and 35940 for an e-card SKU. Filter on merchant_id if you only want dark-store products.
- Store status changed between two pulls minutes apart, from "Currently unavailable" to a 20 minute ETA. Timestamp every row.
- Search relevance bleeds. A non-mango SKU (a sattu product) appeared inside the top 20. Budget for a keyword filter.

### 3.5 Do not monkey-patch fetch

Injecting a `window.fetch` and `XMLHttpRequest` patch from page script captured **zero** responses. The search response is served and consumed before an injected patch can attach.

Playwright's `page.on("response")` does not have this problem, because it hooks at the CDP network layer below page JavaScript. Ordering is irrelevant. Use the native hook.

---

## 4. Swiggy Instamart playbook

### 4.1 Flow (CONFIRMED)

1. Load `https://www.swiggy.com/instamart`. A modal appears reading "Share location to find the closest Instamart store". Products are unreachable until it clears.
2. Click "Search for an area or address". This reveals a textbox with placeholder `Search for area, street name…`.
3. Type the pincode. Suggestions render.
4. Click the matching suggestion. **This does not apply the location.**
5. A map screen appears with a **"Confirm Location" button**. Click it. Blinkit has no equivalent step. Skipping it leaves the location unset and the app falls back to a default store.
6. Header now reads `30 Mins Delivery to 700048, Kolkata Station Rd, Nehru Colony, Dakshindari, South Dumdum, West Bengal 700048, India`. Assert on this before trusting anything downstream.
7. **Search is two-step.** The header search is a `button`, not an input. Its label cycles placeholder terms ("Perfumes", "Laddoo", "Paneer"). Clicking it opens a separate search screen containing the real `searchbox` with placeholder `Search for groceries and more`.
8. Type the query, press Enter. Results render with filter chips (Type, Brand, Sort By) plus query-specific chips.

### 4.2 Suggestion text is duplicated

Suggestions render with their text repeated inside the node:

```
700048, Kolkata Station Road700048, Kolkata Station Road
700048, Purani Basti, Patehra700048, Purani Basti, Patehra
```

Equality matching on suggestion text will fail. Use substring matching. The same Madhya Pradesh decoy appears here as on Blinkit.

### 4.3 Card DOM structure (CONFIRMED)

Name and price live in **separate sibling elements**, not one card node.

- Name element: product name, descriptor, plus any `Sold Out` or `Switch` badge
- Price element: pack size, discount badge, selling price and MRP **concatenated without separators**

Example price element text: `1 ltr45% OFF 109200` meaning 1 litre, 45 percent off, ₹109 against ₹200 MRP.

Pairing must be positional. Price parsing must be pattern based, not delimiter based. Suggested regex approach: extract the leading pack token, then an optional `(\d+)% OFF`, then the trailing numeric run split into selling price and MRP using the discount percentage as the disambiguator.

### 4.4 Selectors are hostile

Class names are build-hashed: `_2CkU8`, `_1SF1k`, `_23IHS`, `sc-gEvEer eTWEyV _38Ot-`. They change without notice. Use the accessibility tree or text anchors.

### 4.5 The silent failure mode, and it is the important one

`https://www.swiggy.com/instamart/search?custom_back=true&query=mango` loads and returns a **complete product grid with HTTP 200 and no error** even with no location set.

Tested without location cookies, it returned a **Bengaluru** catalogue: Kannada product names (Mavina Hannu, Kittale Hannu, Baalehannu, Karbuja) and a 4 minute ETA. Nothing in the response signals that the location is wrong.

**Mitigation is mandatory.** Assert the header address string matches the expected pincode before persisting any row. Without this guard, a scraper hitting the search URL directly will silently accumulate wrong-city data indefinitely.

Response metadata also carried `robots: noindex, nofollow`, and the tail of the render included an "Oops, something's not right" block, so treat partial renders as expected and validate row counts.

### 4.6 Stock granularity (CONFIRMED at state level)

Swiggy exposes **no integer inventory anywhere**, not in the DOM and not in the state. The state fields are:

- `inventory: {inStock: boolean, lowStockText: string}`. Availability is a boolean plus an optional low-stock label.
- `cartAllowedQuantity: {allowedQuantity, quantityLimitBreachedMessage}`. When the message is stock-phrased ("That's all we have in stock at the moment!") the allowedQuantity is effectively remaining stock. When it is cap-phrased ("Only 6 unit(s) of this item can be added per order.") it is a per-order limit. The nearest proxy to depth of stock, usable only with the message check.
- `slotInfo.isAvail` for slot-level availability.

If your panel depends on true depth of stock, Blinkit is the only one of the two platforms that supports it.

### 4.7 Where the JSON lives (CONFIRMED)

`window.___INITIAL_STATE___`, assigned in an **inline script tag inside the server HTML itself**. It is parseable from a plain HTTP GET with no JavaScript execution. Confirmed in two browser captures and one stealth-proxy plain fetch on 18 August 2026.

Top-level slices: `userLocation`, `misc`, `user`, `instamart`, `appHeaders`, `appLocals`, `homeV2`, `storeDetailsV2`, `footerInfo`, `categoryV2`, `productV2`, `searchPLV2`, `campaignMxnV2`, `categoryListingV2`, `collectionListingV2`, `campaignListingV2`, `recipeDirectoryV2`, `recipeDetailV2`, `reorderV2`.

The route decides what is populated server-side:

| Route | homeV2 | storeDetailsV2 | searchPLV2 |
|---|---|---|---|
| `/instamart` | full home feed, 20 products | populated | empty |
| `/instamart/search?query=X` | null | null | data null, skeleton only |
| `/instamart/search?query=X&globalSearch=true` | null | populated | **full search results, server rendered** |

Parsing: the assignment is `window.___INITIAL_STATE___ = {...}` followed by more script. Extract by brace balancing with string awareness, not regex:

```python
import json

def parse_initial_state(html: str) -> dict:
    i = html.find('{', html.find('=', html.find('window.___INITIAL_STATE___')))
    depth, in_str, esc = 0, False, False
    for j in range(i, len(html)):
        ch = html[j]
        if esc: esc = False; continue
        if ch == '\\': esc = True; continue
        if ch == '"': in_str = not in_str; continue
        if in_str: continue
        if ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return json.loads(html[i:j + 1])
    raise ValueError('unbalanced state JSON')
```

### 4.8 The zero-JS search fetch (CONFIRMED)

```
GET https://www.swiggy.com/instamart/search?query=<term>&globalSearch=true
```

One plain GET returns the full search results as structured JSON inside the inline state. No clicks, no XHR capture, no fiber walking. Verified 18 August: 39 products for "mango", 18 cards, HTTP 200.

Why it works, from the shipped `instamart-search-4` bundle: the SSR handler prefetches search results only when the URL query is non-empty **and** `globalSearch === "true"`. Without that flag the search route ships a skeleton.

What lands in `searchPLV2.data`:

- `cards[]` with `@type` values including `GridWidget` (product grids), `OOSItemCollectionCard` (out-of-stock items grouped separately), `InlineViewFilterSortWidget` (filter config)
- `pageOffset.nextOffset` and `searchResultsOffset`, the pagination cursors for the API in 4.10
- `requestId`, `statusCode`, `statusMessage`

**Location caveat, and the correction.** Without location cookies the SSR serves the SEO default store: `userLocation` = Doddakannelli, Bengaluru (12.909483, 77.697731), `locationSource: "seo"`, and `storeDetailsV2.storeId = "1392421"`. That id is therefore the **Bengaluru default store**. An earlier working note attributed 1392421 to the Kolkata store; that was wrong, the value appeared in the cookieless server HTML. The 700048 store id is still to be read from a located session (4.12).

Guard for every persisted row: `userLocation.locationSource` equals `"swgyUL"`, the value observed for a properly located session, where the cookieless default is `"seo"`, **and** `userLocation.address` contains the expected pincode or city. This is the state-level version of the header assertion in 4.5.

### 4.9 Product schema (CONFIRMED, from state)

Product level: `productId`, `parentProductId`, `displayName`, `brand`, `inStock`, `isAvail`, `showQuantity`, `badges`, `analytics`, `adTrackingContext`, `imageBackground`, `listingVariantInfo`, `source`, `variations[]`.

Variation level (38 keys): `skuId`, `spinId`, `displayName`, `brandName`, `quantityDescription`, `secondaryQuantityDescription`, `price`, `inventory`, `cartAllowedQuantity`, `slotInfo`, `podId`, `category`, `subCategoryType`, `superCategory`, `dimensions`, `weightInGrams`, `volumetricWeight`, `rating`, `medias`, `imageIds`, `offerCallouts`, `offerPanels`, `couponLessOffers`, `sla`, `vegClassifier`, `attributeTags`, `aiAttributeTags`, `variationTags`, `listingVariant`, `loudCallout`, `shortDescription`, `stealDealInfo`, `superSaver`, `prebookInfo`, `emiInfo`, `externalPharmacyItem`, `rxRequired`, `isWishlisted`.

Price block:

```
price: {
  mrp:           {currencyCode, units, nanos}
  offerPrice:    {currencyCode, units, nanos}
  discountValue: {currencyCode, units, nanos}
  unitLevelPrice
  offerApplied:  {listingDescription, superOffer, movThreshold, offerHighlights[]}
  maxSaverPrice, flashSalePriceDetails, salePrice
}
```

Prices arrive as units plus nanos, so no string parsing of "1 ltr45% OFF 109200" is needed on this path. `podId` on every variation equals `storeDetailsV2.storeId`, which is how you read the serving store from any item.

### 4.10 API endpoints (paths verbatim from the shipped search bundle)

- `POST /api/instamart/search/v2`. Body: `{facets: [], sortAttribute: "", query, search_results_offset, page_type, is_pre_search_tag}`. Query-string extras observed in the request builder: `offset`, `storeId`, `ageConsent`, optional `layoutId`, `brand`, `voiceSearchTrackingId`. Success is `statusCode: 0`. Paginate by feeding `pageOffset.nextOffset` into `offset` and `searchResultsOffset` into `search_results_offset`.
- `GET /api/instamart/search/mart/v2`. Pre-search feed. Params: store id params plus `isCartPresent`.
- `GET /api/instamart/search/suggest-items/v2`. Autosuggest. Params: `query`, `trackingId`, store id params.
- `GET /api/instamart/complimentary-item/v2/{itemId}`. Complementary items widget.

**Verified live, 18 August.** From a located 700048 browser context, the search POST returned HTTP 200 with `statusCode: 0`, the Kolkata assortment (Langra Mango Malda, Mango Chaunsa), and `nextOffset: "1"`. `storeId` as a query-string parameter worked on the first attempt, with no headers beyond content-type and the session cookies.

A cookieless GET to `/api/instamart/search` returned **403 Forbidden**. The API requires the session cookie set: `deviceId`, `tid` (a JWT with roughly one hour expiry), `sid`, plus the WAF token. Harvest them once in a browser bootstrap, then the API is plain HTTP.

### 4.11 AWS WAF (CONFIRMED)

- Challenge page markers: HTTP 202, `<div id="challenge-container">`, per-host `challenge.js` from `awswaf.com`. The page auto-solves in a real browser and reloads.
- Observed on the first browser load of `/instamart` and on a basic-tier proxy fetch. A stealth-tier fetch of the same URL passed clean with HTTP 200.
- Local handling: run the bootstrap in a persistent browser context so the `aws-waf-token` cookie survives, detect the challenge by marker string or 202, re-solve instead of classifying it as a scrape failure, and use residential or stealth egress for cookieless fetches.

### 4.12 The last two unknowns (CLOSED, 18 August)

Closed in a single located session: 700048 set through the UI, then from the page context a fetch of the globalSearch HTML and the search POST, both riding the session's own cookies.

**Store id for 700048 (Dakshindari, Kolkata Station Rd): `1388313`.** Read from `storeDetailsV2.storeId` in the SSR HTML served to the located session, alongside `userLocation` lat 22.5938146, lng 88.3942369 and `locationSource: "swgyUL"`. The located `userLocation` object also carries `addressId`, `annotation` and `name` fields absent from the SEO default.

**Cookie names: the ones that matter are HttpOnly.** `document.cookie` in the located session shows only seven JS-visible cookies: `_gcl_au`, `_ga` plus three `_ga_*` containers, `_fbp`, and `aws-waf-token` (374 chars, JS-visible). No location or session cookie is visible, yet the same session's SSR fetch returned the Kolkata store and the API POST succeeded. Conclusion: location persistence and the session identifiers (`deviceId`, `tid`, `sid`) ride in HttpOnly cookies.

Consequences for the build:

- Persist the jar at browser level. Playwright's `context.cookies()` and `context.storage_state(path=...)` both include HttpOnly cookies, so the section 6 bootstrap already captures everything. Replaying the jar in `requests` works fine, the HttpOnly flag only restricts page JavaScript, not HTTP clients.
- Exact cookie names are optional knowledge. If a minimal hand-built jar is ever wanted, read the names once via CDP `Network.getAllCookies` or the DevTools Application tab. The `locationSource` value `swgyUL` indicates a userLocation-style cookie.
- The end-to-end chain is proven: UI bootstrap once, the jar carries location plus session plus WAF token, then plain HTTP GETs (globalSearch SSR) and POSTs (search v2) return the correct store with no browser.

---

## 5. Platform comparison

| Aspect | Blinkit | Swiggy Instamart |
|---|---|---|
| Location applied by | Suggestion click | Suggestion click plus map confirm |
| Search entry | Direct input in header | Button opens separate search screen |
| Stock granularity | Integer inventory count | Boolean inStock plus lowStockText |
| Data source | `window.__reduxStore__` | Inline `___INITIAL_STATE___` plus search API |
| Product IDs | product_id, merchant_id, group_id | productId, skuId, spinId, podId |
| Card DOM | Single node per card | Name and price in separate siblings |
| Price format | Discrete fields | Concatenated string |
| Suggestion text | Clean | Duplicated inside node |
| Store id at 700048 | merchant_id 30872 | storeId 1388313 |
| ETA at 700048 | 20 minutes | 30 minutes |
| Direct search URL without location | Not tested | Returns wrong city, HTTP 200, no error |
| Pincode decoy present | Yes, Madhya Pradesh | Yes, Madhya Pradesh |

---

## 6. Reference implementation

Python and Playwright. Accessibility-based locators deliberately. Selectors marked with a comment need verification against the live DOM.

```python
import json, pathlib, datetime
from playwright.sync_api import sync_playwright

CACHE = pathlib.Path("cookies")
CACHE.mkdir(exist_ok=True)

BLINKIT_EXTRACT = """() => {
  const s = window.__reduxStore__.getState();
  const snaps = (s.ui?.search?.searchProductBffData?.snippets) || [];
  return {
    store: {
      merchant: s.data.merchant,
      location: s.data.location,
      eta: s.data.eta,
      chain_id: s.data.chainId,
    },
    products: snaps.filter(x => x.data && x.data.product_id).map(x => ({
      name:          x.data.name?.text,
      pack:          x.data.variant?.text,
      price:         x.data.normal_price?.text,
      mrp:           x.data.mrp?.text ?? null,
      inventory:     x.data.inventory,
      product_state: x.data.product_state,
      is_sold_out:   x.data.is_sold_out,
      product_id:    String(x.data.product_id),
      merchant_id:   String(x.data.merchant_id),
      merchant_type: x.data.merchant_type,
      group_id:      x.data.group_id,
      brand:         x.data.brand_name,
    }))
  };
}"""


def bootstrap_blinkit(ctx, pincode, expect):
    page = ctx.new_page()
    page.goto("https://blinkit.com", wait_until="domcontentloaded")
    page.get_by_text("Select Location").first.click()          # verify
    page.get_by_placeholder("search delivery location").fill(pincode)
    page.wait_for_timeout(1500)
    opts = page.locator(f"text={pincode}")
    for i in range(opts.count()):
        if expect.lower() in opts.nth(i).inner_text().lower():
            opts.nth(i).click()
            break
    else:
        raise RuntimeError(f"no suggestion matched {pincode} / {expect}")
    page.wait_for_load_state("networkidle")
    (CACHE / f"blinkit_{pincode}.json").write_text(json.dumps(ctx.cookies()))
    page.close()


def scrape_blinkit(pincode, term, expect, proxy=None):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, proxy=proxy)
        ctx = b.new_context(locale="en-IN")
        jar = CACHE / f"blinkit_{pincode}.json"
        if jar.exists():
            ctx.add_cookies(json.loads(jar.read_text()))
        else:
            bootstrap_blinkit(ctx, pincode, expect)

        raw = []

        def on_response(r):
            ct = r.headers.get("content-type") or ""
            if "blinkit.com/v" in r.url and "json" in ct:
                try:
                    raw.append({"url": r.url, "body": r.json()})
                except Exception:
                    pass

        page = ctx.new_page()
        page.on("response", on_response)
        page.goto(f"https://blinkit.com/s/?q={term}", wait_until="networkidle")
        for _ in range(4):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(800)

        out = page.evaluate(BLINKIT_EXTRACT)
        out["captured_endpoints"] = [x["url"] for x in raw]
        out["captured_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        out["pincode"] = pincode
        b.close()
        return out


def bootstrap_swiggy(ctx, pincode, expect):
    page = ctx.new_page()
    page.goto("https://www.swiggy.com/instamart", wait_until="domcontentloaded")
    page.get_by_text("Search for an area or address").click()          # verify
    page.get_by_placeholder("Search for area, street name").fill(pincode)
    page.wait_for_timeout(1500)

    # suggestion text is duplicated inside the node, so match on substring
    opts = page.locator(f"text={pincode}")
    for i in range(opts.count()):
        if expect.lower() in opts.nth(i).inner_text().lower():
            opts.nth(i).click()
            break
    else:
        raise RuntimeError(f"no suggestion matched {pincode} / {expect}")

    # the step Blinkit does not have
    page.get_by_role("button", name="Confirm Location").click()
    page.wait_for_load_state("networkidle")

    # MANDATORY guard: without this the app silently serves a default store
    header = page.get_by_role("button", name__contains="Delivery to").inner_text()  # verify
    if pincode not in header:
        raise RuntimeError(f"location did not apply, header reads: {header}")

    (CACHE / f"swiggy_{pincode}.json").write_text(json.dumps(ctx.cookies()))
    page.close()
```

Swiggy extraction, stateless path. Uses the 4.8 fetch and the 4.7 parser. `requests` works once the cookie jar carries the WAF token and session cookies from a bootstrap; before that, run the same fetch through the Playwright context.

```python
import requests

CHALLENGE_MARKER = 'challenge-container'

def fetch_swiggy_search_state(query, cookies, expect_pincode):
    r = requests.get(
        "https://www.swiggy.com/instamart/search",
        params={"query": query, "globalSearch": "true"},
        cookies=cookies,
        headers={"User-Agent": UA, "Accept-Language": "en-IN"},
        timeout=30,
    )
    if r.status_code == 202 or CHALLENGE_MARKER in r.text:
        raise WafChallenge("re-bootstrap the session in a browser")
    st = parse_initial_state(r.text)

    loc = st.get("userLocation") or {}
    if loc.get("locationSource") == "seo" or expect_pincode not in (loc.get("address") or ""):
        raise WrongStore(f"state served for: {loc.get('address')}")

    data = (st.get("searchPLV2") or {}).get("data") or {}
    products = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("displayName") and isinstance(o.get("variations"), list):
                products.append(o)
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)

    walk(data)
    return {
        "store_id": (st.get("storeDetailsV2") or {}).get("storeId"),
        "next_offset": (data.get("pageOffset") or {}).get("nextOffset"),
        "search_results_offset": data.get("searchResultsOffset"),
        "products": [{
            "name": p["displayName"],
            "brand": p.get("brand"),
            "product_id": p.get("productId"),
            "in_stock_product": p.get("inStock"),
            "variants": [{
                "sku_id": v.get("skuId"),
                "pack": v.get("quantityDescription"),
                "price": (v.get("price", {}).get("offerPrice") or {}).get("units"),
                "mrp": (v.get("price", {}).get("mrp") or {}).get("units"),
                "in_stock": (v.get("inventory") or {}).get("inStock"),
                "low_stock_text": (v.get("inventory") or {}).get("lowStockText"),
                "allowed_qty": (v.get("cartAllowedQuantity") or {}).get("allowedQuantity"),
                "pod_id": v.get("podId"),
            } for v in p["variations"]],
        } for p in products],
    }
```

Pagination past the first page: switch to `POST /api/instamart/search/v2` (4.10) with the two offset cursors from this response, same cookie jar.

---

## 7. Data captured, 700048, "Mango", 18 August 2026

### 7.1 Blinkit

| # | Product | Pack | Price | MRP | Inventory | State | Product ID | Merchant ID | Group ID |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Mercely's Merii Mango Paradise Ice Cream Can | 200 ml | 179 | 200 | 3 | available | 800171 | 30872 | 3126814 |
| 2 | Chaunsa Mango (Aam) | 400 g | 145 | 175 | 0 | out_of_stock | 368933 | 30872 | 1920716 |
| 3 | Mango Instant Voucher | 1 unit | 495 | 500 | 26 | available | 780291 | 35940 | 2841015 |
| 4 | Let's Try 0% Mixed Sattu | 450 g | 71 | 110 | 2 | available | 560845 | 30872 | 1054536 |
| 5 | Maaza Mango Drink 600 ml | 600 ml | 34 | 35 | 9 | available | 298 | 30872 | 1951318 |
| 6 | Maaza Mango Drink | 1.75 ltr | 79 | 99 | 14 | available | 114208 | 30872 | 1952060 |
| 7 | Frooti Mango Drink | 600 ml | 34 | 35 | 5 | available | 18612 | 30872 | 1951324 |
| 8 | Real Fruit Power Alphonso Nectar Mango Drink | 1 ltr | 102 | 200 | 15 | available | 391685 | 30872 | 1952161 |
| 9 | Frooti Refreshing Mango Drink | 10 x 150 ml | 95 | 100 | 5 | available | 724695 | 30872 | 2943001 |
| 10 | Frooti Mango Drink - 2 Ltr | 2 ltr | 88 | 110 | 11 | available | 625554 | 30872 | 1953878 |
| 11 | Organically Grown Raw Mango | 200 g | 51 | 58 | 1 | available | 711501 | 30872 | 2634620 |
| 12 | Paper Boat Aamras Mango Drink | 215 ml | 40 | | 8 | available | 5440 | 30872 | 2460458 |
| 13 | Paper Boat Swing Slurpy Mango Drink | 600 ml | 40 | | 3 | available | 480576 | 30872 | 2965105 |
| 14 | Raw Pressery Aam Panna | 750 ml | 62 | 92 | 7 | available | 639229 | 30872 | 3165896 |
| 15 | Raw Pressery Alphonso Mango Drink | 1 ltr | 218 | 234 | 2 | available | 490559 | 30872 | 1951322 |
| 16 | Jade Forest Mango Lush Iced Tea | 300 ml | 60 | 65 | 1 | available | 504011 | 30872 | 2950025 |
| 17 | Paper Boat Aamras / Mango Drink | 600 ml | 69 | 70 | 1 | available | 747661 | 30872 | 2692255 |
| 18 | Paper Boat Nata De Coco Mango Fruit Drink | 250 ml | 40 | | 5 | available | 674581 | 30872 | 2460440 |
| 19 | Pran Frooto Mango Drink | 1 ltr | 68 | | 1 | available | 383355 | 30872 | 1951632 |
| 20 | Raw Pressery Alphonso Mango Drink | 6 x 200 ml | 266 | 336 | 1 | available | 497156 | 30872 | 1951352 |

### 7.2 Swiggy Instamart

| # | Product | Pack | Price | MRP | Discount | Status |
|---|---|---|---|---|---|---|
| 1 | Langra Mango (Malda) (Aam) | 1 kg | 299 | 374 | 20% | Sold Out |
| 2 | Mango Chaunsa (Aam) | 2 Pieces | 155 | 194 | 20% | Sold Out |
| 3 | Muskmelon (Kharbuja) | 1 Piece | 90 | 113 | 20% | |
| 4 | Amul Gold Mango Duetz Ice Cream Stick | 60 ml | 20 | | | |
| 5 | Frooti Tetra Pack | 150 ml | 10 | | | |
| 6 | Go Zero Mango Duet Guilt Free Ice Cream Stick | 60 ml | 69 | 95 | 27% | |
| 7 | NOICE Fresh Mango Juice (With Coco Jelly) | 150 ml | 73 | 129 | 43% | |
| 8 | Epigamia Greek Yogurt - Mango | 85 g | 60 | | | |
| 9 | Real Fruit Juice, Alphonso Mango | 1 ltr | 109 | 200 | 45% | |
| 10 | Pluckk Cold Pressed Extracted 100% Mango Juice | 250 ml | 94 | 99 | 5% | |
| 11 | Hocco Aamchi Mango Ice Cream | 120 ml | 200 | | | |
| 12 | NOICE Mango Kombucha | 200 ml | 84 | 158 | 46% | |
| 13 | Get-A-Way Mango High Protein Ice Cream Cup | 100 ml | 78 | 120 | 35% | |
| 14 | Epigamia Fruit Yogurt - Mango | 75 g | 35 | | | |
| 15 | Pluckk Cold Pressed Extracted Aam Panna Juice | 250 ml | 94 | 99 | 5% | |
| 16 | Maaza - Real Mango Fruit Drink | 1.2 ltr | 75 | | | |
| 17 | Elephant Apple (Seb) | 1 Piece | 35 | 44 | 20% | |
| 18 | Kwality Wall's Alphonso Mango Ice Cream Tub | 700 ml | 160 | | | |
| 19 | Frooti Mango Drink Bottle | 600 ml | 33 | 35 | 5% | |
| 20 | Swissyum Swiss Roll Mango | 150 g | 71 | 75 | 5% | |

### 7.3 Cross-platform overlaps in the top 20

| SKU | Blinkit | Swiggy | Delta |
|---|---|---|---|
| Frooti Mango Drink 600 ml | 34 | 33 | Swiggy cheaper by 1 |
| Real Fruit Alphonso Mango 1 ltr | 102 | 109 | Blinkit cheaper by 7 |

Both platforms show fresh mango sold out, consistent with the season ending.

---

## 8. Consolidated failure modes

| Failure | Platform | Symptom | Mitigation |
|---|---|---|---|
| Wrong city served silently | Swiggy | HTTP 200, full grid, wrong catalogue | Assert header address contains the pincode before persisting |
| Wrong state selected from suggestions | Both | Plausible but wrong data | Match pincode plus state, hard fail on no match |
| Location never applied | Swiggy | Falls back to default store | Click Confirm Location on the map screen |
| `is_sold_out` always false | Blinkit | Out-of-stock rows read as in stock | Use inventory or product_state only |
| Injected fetch patch captures nothing | Blinkit | Empty capture array | Use Playwright `page.on("response")` |
| Short product IDs mangled | Blinkit | 298 becomes numeric | Type the column as text |
| Non-matching SKUs in results | Both | Sattu, muskmelon, elephant apple in mango results | Keyword filter on product name |
| Name-only matching drops valid rows | Swiggy | "Frooti Tetra Pack" has no "Mango" in the name | Supplement with brand and category matching |
| Inventory drifts within minutes | Both | Counts change between runs | Timestamp every row, treat as point-in-time |
| Browser session fails to acquire | Infrastructure | Repeated failures then success, unchanged request | Retry with backoff, do not infer blocking |
| WAF challenge page served | Swiggy | HTTP 202, challenge-container div, no product HTML | Solve once in a real browser, persist the token cookie, detect the marker and re-solve |
| Cheap proxy tier challenged | Swiggy | Challenge on basic egress, clean pass on stealth | Use residential or stealth egress, or a solved token cookie |
| Cookieless API call rejected | Swiggy | 403 Forbidden on /api/instamart paths | Bootstrap session cookies in a browser, then call the API |
| Session token expiry | Swiggy | tid JWT expires about one hour after issue | Refresh the jar when API auth starts failing |
| Cookieless page states the default store | Swiggy | locationSource is seo, Bengaluru address, storeId 1392421 | Enforce the locationSource and pincode guard before persisting |
| Location cookies invisible to page JS | Swiggy | document.cookie shows only analytics cookies and the WAF token | Persist the whole jar at browser level, HttpOnly included |

---

## 9. Next actions

1. Wire the stateless Swiggy path into the scraper: bootstrap once per pincode, persist the full jar with `storage_state`, GET the globalSearch URL, parse the inline state, enforce the locationSource and pincode guard.
2. Use `POST /api/instamart/search/v2` with the same jar for pagination past the first page, verified live with statusCode 0 on 18 August.
3. Optional: name the HttpOnly location cookie via CDP `Network.getAllCookies` if a minimal hand-built jar is ever wanted; jar-level persistence makes this unnecessary.
4. Sample `merchant_type` values on Blinkit, still unobserved.
5. Open the Blinkit `data.merchant`, `data.location`, `data.eta` and `data.addressesV2` slices. These are the only plausible home for dark store metadata and were never dumped. Current expectation, unverified, is that Blinkit exposes merchant_id and ETA but not a dark store postal address.
6. Map merchant_id on Blinkit and storeId on Swiggy across a grid of pincodes to derive dark store catchment boundaries, achievable without any street address.
