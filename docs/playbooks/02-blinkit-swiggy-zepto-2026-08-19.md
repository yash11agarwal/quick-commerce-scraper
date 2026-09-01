# Q-commerce scraper playbook

Target: pincode-scoped product, price and inventory extraction from Indian quick-commerce platforms.

Reference probe used throughout: pincode **700048** (Patipukur / Kolkata Station Rd, Dakshindari, South Dumdum, West Bengal), search term **Mango**.

## 0. Provenance

| Section | Verified | Notes |
|---|---|---|
| Blinkit | 18 August 2026 | Live session, Redux extraction confirmed |
| Swiggy Instamart | 18 August 2026 | Live session, SSR route and search POST both fired |
| Zepto | 19 August 2026 | Live session, search API response captured raw |

This file was rebuilt on 19 August in a fresh sandbox. The Blinkit and Swiggy sections carry forward the confirmed findings from the 18 August session. The original file also held a reference Python implementation which is not reproduced here.

## 1. Platform comparison

| | Blinkit | Swiggy Instamart | Zepto |
|---|---|---|---|
| Host | blinkit.com | swiggy.com/instamart | zepto.com (zeptonow.com now redirects) |
| Framework | Redux SPA | SSR with inline state | Next.js App Router (RSC) |
| Where data lives | `window.__reduxStore__` | `window.___INITIAL_STATE___` in server HTML | XHR response from search API |
| Zero-JS route exists | No | Yes, `globalSearch=true` | Partially, SSR grid but default store only |
| Location cookies readable by page JS | Yes | **No, HttpOnly** | **Yes, all of them** |
| Map confirm step | No | Yes | Sometimes |
| Store id at 700048 | merchant_id `30872` | storeId `1388313` | storeId `42dd88f4-dc27-496e-80f6-5df99c7f8ea4` (KOL-Belgachia) |
| ETA at 700048 | 20 minutes | 30 minutes | 15 minutes |
| Reliable stock field | `inventory` | per-item stock fields in state | `availableQuantity` **from the API only** |
| Stock field that lies | `is_sold_out` (always false) | none observed | `availableQuantity` and `outOfStock` **in the DOM** |
| Prices unit | rupee string with symbol | units plus nanos | **paise integer** |
| Search ranking | lexical | lexical | semantic, heavy drift |

## 2. Blinkit

### 2.1 Location flow

1. Load `blinkit.com`.
2. Click the delivery location selector (`Select Location`).
3. Type the pincode into the `search delivery location` input.
4. Wait for autocomplete, then select by substring match, not equality.
5. Page reloads with location applied. Header shows `Delivery in 20 minutes` plus the full address.
6. Click the header search input (placeholder `Search for atta dal and more`), type the query, press Enter.
7. Scroll to load additional cards, results render progressively.

There is no map confirmation step on Blinkit. Clicking the suggestion applies the location directly.

### 2.2 Where the data lives

Read the Redux store, not the DOM.

```
window.__reduxStore__.getState().ui.search.searchProductBffData.snippets[]
```

The DOM gives name, pack, price, MRP and a rendered "Out of Stock" label. The store gives all of that plus exact integer inventory and every ID needed.

Slices at `getState()`: `data`, `ui`, `modal`, `screen`, `browser`, `api`, `analytics`. Under `data`: `ua`, `featureFlags`, `deviceId`, `chainId`, `auth`, `user`, `location`, `merchant`, `categories`, `recipes`, `mainConfig`, `cart`, `addresses`, `addressesV2`, `search`, `seo`, `persistentKeys`, `secondaryData`, `analyticsAttributes`, `eta`, `print`, `assistReducer`, `ambulanceMetrics`.

Each snippet has four top-level keys: `data`, `tracking`, `widget_type`, `layout_config`. Widget types seen: `product_card_snippet_type_2`, `grid_container_vr`, `image_text_vr_type_header`. Filter on presence of `data.product_id` to isolate real products.

### 2.3 Product fields under `snippet.data`

| Field | Path | Notes |
|---|---|---|
| name | `data.name.text` | |
| display_name | `data.display_name.text` | |
| brand_name | `data.brand_name` | |
| variant | `data.variant.text` | pack size |
| normal_price | `data.normal_price.text` | selling price, rupee symbol prefixed |
| mrp | `data.mrp.text` | null when no MRP shown |
| offer_tag | `data.offer_tag` | |
| inventory | `data.inventory` | integer, the important one |
| is_sold_out | `data.is_sold_out` | unreliable, see 2.4 |
| product_state | `data.product_state` | `available` or `out_of_stock` |
| product_id | `data.product_id` | **string** |
| merchant_id | `data.merchant_id` | store key |
| merchant_type | `data.merchant_type` | |
| group_id | `data.group_id` | **integer**, groups pack variants |
| eta_tag / eta_identifier | `data.eta_tag`, `data.eta_identifier` | |
| max_count | `data.stepper_data_v2.max_count` | mirrors inventory exactly, derived |

### 2.4 Blinkit gotchas

- Do not key stock logic off `is_sold_out`. It was `false` on every row including the zero-inventory one. Use `inventory == 0` or `product_state == "out_of_stock"`.
- `stepper_data_v2.max_count` mirrors `inventory` exactly. No need to store both.
- `product_id` is a string, `group_id` is an integer. Short IDs like `298` and `18612` are corrupted by loose typing in Excel or SQLite. Keep the column typed as text.
- `merchant_id` was 30872 on 19 of 20 rows, with 35940 on a separate merchant node for e-cards and vouchers. Filter on merchant_id for dark-store SKUs only.
- Inventory is point-in-time and drifts within minutes. Stamp every row with a capture timestamp.
- Rows sitting at inventory of 1 flip to out of stock on a single order. Account for this in availability rates.
- Presence of pricing is not a proxy for availability. Zero-inventory rows still carry full price and MRP.
- The Redux store beats network interception here, because the search response is served before any injected patch can attach.

## 3. Swiggy Instamart

### 3.1 The fast path, no browser needed per query

`GET https://www.swiggy.com/instamart/search?query=mango&globalSearch=true`

Returns HTTP 200 with full server-rendered search results in `window.___INITIAL_STATE___`, embedded in an inline script. No JS execution, no clicks, no XHR capture. Verified live: 39 products, 18 cards, pagination cursors, one request.

The `globalSearch=true` flag is required. Without it the same route returns a skeleton with `searchPLV2.data` null.

Prices arrive as structured units plus nanos on this path, which removes the concatenated price string parsing problem entirely.

### 3.2 API surface

`POST /api/instamart/search/v2?offset=0&storeId=1388313&ageConsent=false`

Verified live 18 August from a located session: HTTP 200, `statusCode: 0`, genuine Kolkata assortment (Langra Mango Malda, Mango Chaunsa), `nextOffset: "1"` for pagination. No headers beyond content-type, just the session cookies.

A cookieless GET to `/api/instamart/search` returns **403 Forbidden**.

### 3.3 Cookies are HttpOnly

`document.cookie` in a located session shows only seven JS-visible cookies: `_gcl_au`, `_ga` plus three `_ga_*` containers, `_fbp`, and `aws-waf-token` (374 chars). No location cookie, no `deviceId`, `tid` or `sid`, yet the same session returns Kolkata data and passes API auth.

Consequence: **persist the jar at browser level.** Playwright's `context.cookies()` and `context.storage_state(path=...)` include HttpOnly cookies. Replaying them from `requests` works, because HttpOnly restricts page scripts, not HTTP clients. Do not attempt to hand-build a jar from `document.cookie`.

### 3.4 The wrong-city guard

Two-part assertion on every response:

1. `locationSource == "swgyUL"`. The SEO default is `"seo"`.
2. The echoed address contains the target pincode.

storeId `1392421` is the **Bengaluru** SEO default, not Kolkata. It appears in cookieless server HTML alongside the SEO fallback location.

### 3.5 UI flow quirks (only needed for the one-time bootstrap)

- Suggestion text is duplicated inside each node, for example `700048, Kolkata Station Road700048, Kolkata Station Road`. Match on substring, never equality.
- Clicking a suggestion does not apply the location. A map screen with a **Confirm Location** button follows and must be clicked. Skipping it leaves the location unset and the app silently falls back to a default store.
- The header search is a `button`, not an input, and its label cycles placeholder terms. Clicking it opens a separate screen containing the real searchbox with placeholder `Search for groceries and more`.
- Card DOM puts name and price in separate sibling elements. Pairing must be positional, and the price string concatenates without separators, for example `1 ltr45% OFF 109200`.
- After confirming, the header renders the full address string. Assert on it before trusting downstream data.

### 3.6 Parsing `___INITIAL_STATE___`

Extract by brace-balancing with string and escape awareness, walking character by character tracking `in_str` and `esc`. Regex fails because the value contains nested braces and escaped characters.

## 4. Zepto

### 4.1 Hosts

- `zeptonow.com` now 301s to `www.zepto.com`.
- API host is `bff-gateway.zepto.com`, not `api.zeptonow.com`.
- Images still served from `cdn.zeptonow.com`.

### 4.2 Location flow and cookies

Unlike Swiggy, **every cookie that matters is readable and settable from page JS.** Set these directly and the whole map flow can be skipped after the first bootstrap:

| Cookie | Content |
|---|---|
| `latitude`, `longitude` | plain decimals |
| `user_position` | URL-encoded JSON `{latitude, longitude}` |
| `serviceability` | URL-encoded JSON carrying `primaryStore.storeId`, `etaInMinutes`, `storeDetailedInfo.{city,name}` |
| `aws-waf-token` | WAF challenge token, required on every request |
| `session_id`, `device_id`, `session_count` | session identity |
| `marketplace` | `SUPER_SAVER`, decides which price field the card shows |
| `csrfSecret`, `XSRF-TOKEN` | CSRF pair |

700048 resolves to:

```
storeId   42dd88f4-dc27-496e-80f6-5df99c7f8ea4
name      KOL-Belgachia
city      Kolkata
coords    22.6015112, 88.4003915
eta       15 minutes
```

localStorage carries zustand-persisted slices: `user-position` (with `placeId`, `name`, `formattedAddress`, `shortAddress`), `cart`, `header-store`, `userRecentSearches`, `locationPermission`.

### 4.3 API surface

| Endpoint | Method | Purpose |
|---|---|---|
| `/user-search-service/api/v3/search` | POST | search results |
| `/user-search-service/api/v3/search/filters` | POST | facet rail |
| `/api/v1/maps/place/autocomplete/?place_name=700048` | GET | pincode to Google place id |
| `/api/v1/maps/place/details/?place_id=<id>` | GET | place id to coords |
| `/api/v1/user/customer/address/location?latitude=&longitude=` | GET | serviceability, mints the store |
| `/lms/api/v2/get_page?latitude=&longitude=&page_type=HOME&version=v2` | GET | home layout |

Search request body:

```json
{"query":"Mango","pageNumber":0,"mode":"TYPED","intentId":"<uuid>","userSessionId":"<uuid>"}
```

`mode` is `TYPED` or `AUTOSUGGEST`. **No storeId in the body.** Store binding is entirely cookie driven, which is why the cookie guard matters so much here.

Response envelope keys: `layout`, `footer`, `header`, `selectedTabId`, `pageProductCount`, `totalProductCount`, `currentPage`, `hasReachedEnd`, `userSessionId`, `filters`, `meta`, `experiments`, `pageType`, `pageMeta`, `nextPageParams`, `filterMode`.

Pagination: `totalProductCount: 220` for "mango", `pageProductCount: 29`, `nextPageParams: {filterMode: 1, pageNumber: 1}`.

### 4.4 THE CRITICAL FINDING: the DOM lies about stock

Zepto runs an experiment `always_in_stock` with `variantName: test, enable: true`. It rewrites client state after hydration.

Measured on the same page load, 38 cards:

| Source | availableQuantity | outOfStock |
|---|---|---|
| React props on the card | `10` on all 38 rows, zero variance | `false` on all 38 |
| Raw XHR response body | spread of 0, 1, 2, 3, 4, 6, 7, 9, 12, 25 | `true` on 9 rows |

Out-of-stock items render a completely normal card with a live ADD button and no Sold Out badge.

**This inverts the Blinkit rule.** On Blinkit you read the hydrated Redux store because the DOM label is fine but `is_sold_out` is not. On Zepto the hydrated state is the poisoned source. You must capture the `POST /v3/search` response body before React touches it, either by hooking `XMLHttpRequest.prototype.send` and reading `responseText` on load, or by replaying the POST directly with the cookie jar.

### 4.5 Product schema

Top-level object per result, roughly 70 keys. The ones that matter:

| Field | Notes |
|---|---|
| `id` / `objectId` | store-scoped product id |
| `storeId` | assert this matches your target store |
| `productVariant.id` | the `pvid` in the URL, catalogue-level variant |
| `product.id` | catalogue product id |
| `product.name`, `product.brand`, `product.brandId` | |
| `productVariant.formattedPacksize` | display pack, e.g. `1 pc (120 ml)` |
| `productVariant.packsize`, `unitOfMeasure`, `weightInGms` | structured pack |
| `sellingPrice`, `discountedSellingPrice`, `mrp` | **paise** |
| `superSaverSellingPrice`, `zeptoPassPrice` | alternate price tiers |
| `discountPercent`, `discountAmount` | |
| `availableQuantity` | the operative stock figure, API only |
| `outOfStock` | API only |
| `allocatedQuantity`, `stockoutThresholdQuantity` | both zero on all sampled rows |
| `productVariant.maxAllowedQuantity` | per-order cap |
| `productVariant.quantity` | second quantity field, see below |
| `productType` | `SELLABLESKU` |
| `meta.is_fly_wheel_ad`, `meta.tagsV2` | ad and badge signals |
| `primaryCategoryName`, `primaryCategoryId` | |
| `ratingSummary.averageRating`, `totalRatings` | |

### 4.6 Zepto gotchas

- **Prices are in paise.** `mrp: 20000` is ₹200. Divide by 100 or ship a 100x error.
- Two quantity fields. `availableQuantity` is the sellable count and is what you want. `productVariant.quantity` usually matches but sometimes runs higher: 25 vs 31, 6 vs 22, 12 vs 14. Three of four divergences clamp exactly to `maxAllowedQuantity`, one does not (3 vs 6 with a cap of 25). Do not build logic on the relationship, just take `availableQuantity`.
- Out-of-stock items are demoted into a contiguous band rather than hidden. Across two pulls the same nine SKUs occupied ranks 12 to 20, permuted within the band. A clean block of consecutive rows in the low teens is a stockout signal before it is a relevance signal.
- Sponsored cards duplicate organic ones with an identical `pvid`. Dedupe on pvid, and use `meta.is_fly_wheel_ad` plus the `P3 - Ad.png` tag to separate ad slots from organic rank.
- `pvid` is not a stable cross-store join key for fresh produce. Mango Raw carried a different pvid in the Kolkata store than in the default store.
- Ranking is semantic (`SEMANTIC_EXPERIMENT_V4`, `vector_recall_v8`, `semantic_variant_3`). Only 4 of the top 20 for "mango" were mangoes. Unrelated items (Epigamia Lychee Yogurt, Mother Dairy Mishti Doi) ranked inside the top 20. Do not assume query term presence in results.
- Rank order is not stable between sessions. Two pulls minutes apart returned the same set with the out-of-stock band shuffled internally.
- No `__NEXT_DATA__`. App Router streams into `self.__next_f.push()` chunks. Parse those from the raw HTML, not from `window`, because the array is drained after hydration (`self.__next_f.length` reads 0 post-hydration).
- Other live experiments seen: `guest_cart`, `new_login_flow`, `PVID_SEARCH_CATALOGUE_EXPERIMENT`, `OR2_MIGRATION_EXPERIMENT`.

### 4.7 DOM anchors that held up

```
a[data-testid="product-card"]                 href = /pn/<slug>/pvid/<uuid>
  div[data-is-out-of-stock]                   present but masked by always_in_stock
  [data-slot-id="EdlpPrice"]                  selling price then MRP as sibling spans
  [data-slot-id="ProductName"]
  [data-slot-id="PackSize"]
  [data-slot-id="RatingInformation"]
```

Class names are build-hashed (`cslgId`, `cQAjo6`, `B4vNQ`) and useless.

### 4.8 The silent wrong-store failure

A plain scrape of `/search?query=mango` with no session returns HTTP 200 with a fully server-rendered product grid and no error of any kind, bound to storeId `b4dc8d65-ed2e-4142-81b6-373982b13500`, the Bengaluru default. Pre-location `get_page` calls fire with coords `12.96902 / 77.75395`.

This is harder to catch than the Swiggy equivalent because product names are all English, so nothing looks wrong. Assert on `serviceability.primaryStore.storeId` or the header address text before trusting any row.

## 5. Cross-platform failure modes

| Failure | Platform | Signature | Guard |
|---|---|---|---|
| Cookieless page serves the default store | Swiggy | `locationSource: seo`, Bengaluru address, storeId 1392421 | Enforce locationSource plus pincode assertion |
| Cookieless page serves the default store | Zepto | storeId `b4dc8d65-...`, coords 12.96902 / 77.75395, all-English names | Assert `serviceability.primaryStore.storeId` |
| Location cookies invisible to page JS | Swiggy | `document.cookie` shows only analytics plus WAF token | Persist whole jar at browser level |
| Stock field is a constant lie | Blinkit | `is_sold_out` false on zero-inventory rows | Key off `inventory` or `product_state` |
| Stock field is a constant lie | Zepto | `availableQuantity` = 10 on every card in the DOM | Read the API response body, never the DOM |
| Suggestion click does not apply location | Swiggy | No reload, map screen appears | Click Confirm Location, then assert on header |
| Duplicated suggestion text | Swiggy | `700048, Kolkata Station Road` twice in one node | Substring match, never equality |
| Price string concatenated | Swiggy DOM | `1 ltr45% OFF 109200` | Use the SSR path where prices are units plus nanos |
| Price off by 100x | Zepto | `mrp: 20000` for a ₹200 item | Divide by 100 |
| ID type corruption | Blinkit | `product_id` 298 becomes integer | Type the column as text |
| WAF challenge | Swiggy, Zepto | HTTP 202, `challenge-container` div | Stealth proxy tier, or re-bootstrap the browser |

## 6. Firecrawl operating notes

- `firecrawl_interact` session acquisition is **unreliable and unrelated to the target site**. Roughly 9 failures against 3 successes across the 18 August session, with the identical request succeeding on retry. On 19 August one failure cleared immediately on retry. Wrap in retry with exponential backoff. A failed session is not evidence of blocking.
- `scrapeId` resumes an existing session. This works well and is much cheaper than re-bootstrapping the location flow. Both Zepto passes on 19 August resumed successfully.
- `firecrawl_interact_stop` releases the session. The 19 August runs billed 51 and 30 credits for 437s and 257s respectively.
- `scrapeOptions` must be passed as a real JSON object. Python-style single-quoted dict literals fail schema validation with "expected object, received string".
- `firecrawl_scrape` in this build has **no `actions` array**, so it cannot set a pincode. Interactive sessions are the only route for the location flow.
- `firecrawl_scrape` with `formats: ['rawHtml']`, `proxy: 'stealth'`, `maxAge: 0` passes the AWS WAF clean. The `basic` tier hits the challenge (HTTP 202, `challenge-container`).
- Unpacking a scrape result: take the tool result as a list, index 0, parse the `text` field as JSON, then read `rawHtml` from the inner object.
- Large tool results spill to `/mnt/user-data/tool_results/*.json` rather than entering context. Unpack them with the same envelope pattern: list, index 0, `text` field parsed as JSON, then `stdout` / `output` / `result`.
- The agent prompt inside `firecrawl_interact` handles multi-step JS reliably if each block is labelled and the instruction says to continue past errors. Serializing React fiber props directly throws on circular structure, so reach for the specific subtree (`fiber.memoizedProps.children[0].props.productInformation`) rather than stringifying the fiber.

## 7. Open items

1. Zepto: confirm whether `availableQuantity` is store stock net of allocations or is capped upstream, given the one divergence from `maxAllowedQuantity` that the cap theory does not explain.
2. Zepto: exercise `POST /v3/search` from a plain HTTP client with a replayed cookie jar, as was done for Swiggy, to prove the browser can be dropped after bootstrap.
3. Zepto: walk pagination via `nextPageParams` to confirm the 220-result total and check whether the out-of-stock band recurs on later pages.
4. All three: run the same pincode across platforms at the same timestamp for a like-for-like assortment and price comparison.
5. Blinkit: sample `merchant`, `location`, `eta` and `addressesV2` store slices for dark-store address detail, which was queued but never captured.
