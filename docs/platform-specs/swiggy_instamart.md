# Swiggy Instamart

Source: `docs/playbooks/01-blinkit-swiggy-2026-08-18.md` section 4 (the detailed one, including
the shipped-bundle analysis in 4.7 to 4.12) and `docs/playbooks/02-blinkit-swiggy-zepto-2026-08-19.md`
section 3. CONFIRMED means observed live on 18 August 2026. OPEN means neither playbook says.

Adapter name: `swiggy_instamart`. Reference probe: 700048, "Mango", expected city Kolkata.

## 1. Profile

| | |
|---|---|
| Host | `https://www.swiggy.com/instamart` |
| Framework | Server-side rendered with inline state |
| Where the data lives | `window.___INITIAL_STATE___`, assigned in an inline script in the server HTML |
| Zero-JS route | Yes: `/instamart/search?query=<term>&globalSearch=true` |
| Location cookies readable by page JS | **No.** HttpOnly. `document.cookie` shows only analytics cookies and `aws-waf-token` |
| Map confirm step | **Yes.** Clicking a suggestion does not apply the location; `Confirm Location` on the map screen does |
| Store id at 700048 | `storeDetailsV2.storeId` `1388313`. The cookieless default is `1392421`, Bengaluru |
| ETA at 700048 | 30 minutes |
| Stock granularity | Boolean `inventory.inStock` plus an optional `lowStockText` string. No integer anywhere |
| Price unit | `{currencyCode, units, nanos}` objects |
| Search ranking | Lexical |

## 2. Location flow, one-time bootstrap (CONFIRMED)

1. `goto https://www.swiggy.com/instamart`. A modal reads `Share location to find the closest
   Instamart store`. Products are unreachable until it clears.
2. Click `Search for an area or address`. A textbox with placeholder `Search for area, street
   name…` appears.
3. Type the pincode. Suggestions render with their text **duplicated inside each node**:
   `700048, Kolkata Station Road700048, Kolkata Station Road`. Match on substring, never
   equality. The Madhya Pradesh decoy `700048, Purani Basti, Patehra` is present here too.
4. Click the suggestion whose text contains the pincode and the expected city or state.
   Nothing matched: `LocationNotSetError` with the suggestion texts in the reason.
5. A map screen appears with a `Confirm Location` button. Click it. Skipping this leaves the
   location unset and the app silently serves a default store.
6. The header now reads
   `30 Mins Delivery to 700048, Kolkata Station Rd, Nehru Colony, Dakshindari, South Dumdum, West Bengal 700048, India`.
7. Save `context.storage_state()` to `sessions/swiggy_instamart_<pincode>.json`. It includes the
   HttpOnly cookies (`deviceId`, `tid`, `sid` per the bundle analysis, plus the WAF token).
   The HttpOnly flag restricts page scripts, not the HTTP client that replays the jar.

The first load of `/instamart` may serve the AWS WAF challenge (HTTP 202,
`<div id="challenge-container">`, `challenge.js` from `awswaf.com`). It auto-solves in a real
browser and reloads. The adapter waits for the reload; it never attempts to solve it.

## 3. Readback and verification (rule 4)

Two witnesses, both required, checked on every SSR fetch (not only at bootstrap), because the
SSR path is stateless per query and the jar can expire:

| witness | status | what is asserted |
|---|---|---|
| `___INITIAL_STATE___.userLocation.locationSource` | CONFIRMED `"swgyUL"` when located, `"seo"` for the cookieless default | must equal `"swgyUL"` |
| `___INITIAL_STATE___.userLocation.address` | CONFIRMED contains the pincode | must contain the requested pincode; `effective_pincode` is the six-digit string found in it |
| Header text at bootstrap | CONFIRMED format in section 2 step 6 | must contain the pincode; `eta_minutes` is the leading integer (`30`) |
| `storeDetailsV2.storeId` | CONFIRMED `1388313` at 700048 | recorded as the job's store id. `1392421` is the Bengaluru default and is a `LOCATION_NOT_SET` failure regardless of the other witnesses |
| `variations[].podId` | CONFIRMED equal to `storeDetailsV2.storeId` on every variation | every row's `podId` must equal the job's store id; a mismatch is `store_id_mixed` |

The located `userLocation` also carries `addressId`, `annotation`, `name`, `lat` 22.5938146,
`lng` 88.3942369. These are stored as evidence.

## 4. Strategy ladder

| step | strategy id | what | status |
|---|---|---|---|
| primary | `ssr_global_search` | `page.context.request.get("https://www.swiggy.com/instamart/search", params={"query": term, "globalSearch": "true"})`, using the located context's jar; store the full HTML as `ssr_document`; extract `___INITIAL_STATE___` by brace balancing (section 6) | CONFIRMED: 39 products, 18 cards, HTTP 200, one request |
| secondary | `search_v2_api` | `POST /api/instamart/search/v2?offset=<nextOffset>&storeId=<storeId>&ageConsent=false` for pages after the first, stored as `api_replay` | CONFIRMED live: HTTP 200, `statusCode: 0`, Kolkata assortment, `nextOffset: "1"` |
| DOM | none | name and price sit in separate sibling nodes and the price string concatenates (`1 ltr45% OFF 109200`) | not in the ladder |

`globalSearch=true` is required. Without it the same route returns a skeleton with
`searchPLV2.data` null, which must be classified `SCHEMA_DRIFT` (it means the flag was lost),
never `NO_RESULTS`.

The secondary POST body is `{facets: [], sortAttribute: "", query, search_results_offset,
page_type, is_pre_search_tag}`. The values used for `page_type` and `is_pre_search_tag` in the
verified call were not recorded. OPEN. Pagination past the first page is therefore not
implementable until Phase 3 captures one live POST.

## 5. Envelope

Top-level slices of `___INITIAL_STATE___`: `userLocation`, `misc`, `user`, `instamart`,
`appHeaders`, `appLocals`, `homeV2`, `storeDetailsV2`, `footerInfo`, `categoryV2`,
`productV2`, `searchPLV2`, `campaignMxnV2`, `categoryListingV2`, `collectionListingV2`,
`campaignListingV2`, `recipeDirectoryV2`, `recipeDetailV2`, `reorderV2`.

```
searchPLV2.data
├── cards[]                     @type: GridWidget | OOSItemCollectionCard | InlineViewFilterSortWidget
├── pageOffset.nextOffset       pagination cursor -> offset
├── searchResultsOffset         pagination cursor -> search_results_offset
├── requestId
├── statusCode                  0 is success on the API; meaning of other values OPEN
└── statusMessage
```

Product objects live inside `GridWidget` cards (in stock) and `OOSItemCollectionCard` cards
(out of stock, grouped separately). The nesting between a card and its product objects is
OPEN; the reference implementation walked the tree for dicts carrying `displayName` and a
`variations` list. The V2 parser will read the exact path once the Phase 3 fixture shows it.
Row order: card order, then item order within the card, so out-of-stock items rank after
in-stock ones by construction.

## 6. Extracting `___INITIAL_STATE___`

Regex fails because the value holds nested braces and escaped characters. Find
`window.___INITIAL_STATE___`, then the first `{` after the following `=`, then walk
character by character tracking `in_string` and `escape`, counting depth, and stop at depth 0.
The playbook's `parse_initial_state` (file 01, section 4.7) is the reference. It lives in
`platforms/swiggy_instamart/initial_state.py` and has its own unit test with a nested,
escaped fixture.

## 7. Field map

One listing row per **variation**, not per product (question A10). A product with three pack
sizes yields three rows sharing `result_rank`.

| output column | path | type as served | rule |
|---|---|---|---|
| `platform_product_id` | `variations[].skuId` | | as string. `productId`, `parentProductId`, `spinId` stay in raw |
| `product_name` | product `displayName` | string | required |
| `brand` | product `brand` | string | key required |
| `pack_size` | `variations[].quantityDescription` | string, `"1 ltr"`, `"2 Pieces"` | required. `secondaryQuantityDescription` raw only |
| `selling_price` | `variations[].price.offerPrice.{units,nanos}` | `units` int or string, `nanos` int | `units * 100 + nanos // 10_000_000`; `nanos % 10_000_000 != 0` is `fractional_paisa` and `None`. Which of `offerPrice`, `salePrice`, `mrp` is populated on a no-discount row is OPEN |
| `mrp` | `variations[].price.mrp.{units,nanos}` | same | same conversion; value may be absent on no-discount rows (table shows blank MRP for several) |
| `in_stock` | `variations[].inventory.inStock` | bool | as is. Product-level `inStock` is a cross-check; disagreement is anomaly `product_variation_stock_disagree`, variation wins |
| `stock_qty` | none | | `None` always. See question A11 on `cartAllowedQuantity` |
| `eta_minutes` | header at bootstrap | | the leading integer in `30 Mins Delivery to ...`. `variations[].sla` exists, shape OPEN |
| `store_or_seller_id` | `variations[].podId` | | as string; asserted equal to `storeDetailsV2.storeId` |
| `category_path` | `variations[].superCategory`, `category`, `subCategoryType` | keys CONFIRMED, value types OPEN | `None` until the fixture shows the types; intended form `superCategory > category > subCategoryType` |
| `product_url` | not in the playbook | | `None`. OPEN |
| `image_url` | `variations[].imageIds`, `medias` exist; CDN base not documented | | `None`. OPEN |
| `currency` | `price.*.currencyCode` | | `"INR"`; any other value is anomaly `currency_not_inr` |
| raw only | `inventory.lowStockText`, `cartAllowedQuantity.{allowedQuantity, quantityLimitBreachedMessage}`, `slotInfo.isAvail`, `price.discountValue`, `price.offerApplied`, `price.superSaver`, `badges`, `analytics`, `adTrackingContext`, `vegClassifier`, `rating`, `weightInGrams`, `dimensions` | | |

`cartAllowedQuantity.quantityLimitBreachedMessage` has two phrasings: stock-phrased
(`That's all we have in stock at the moment!`), where `allowedQuantity` is effectively
remaining stock, and cap-phrased (`Only 6 unit(s) of this item can be added per order.`),
where it is a per-order limit. V2 leaves `stock_qty = None` in both cases unless question A11
says otherwise.

## 8. Structural requirements (missing means `SCHEMA_DRIFT`)

- the document contains `window.___INITIAL_STATE___` and it extracts to an object
- `userLocation.locationSource` and `userLocation.address` present
- `storeDetailsV2.storeId` present
- `searchPLV2.data` is an object (null means the `globalSearch` flag was lost)
- `searchPLV2.data.cards` is a list; `pageOffset` present
- on every product: `productId`, `displayName`, `brand`, `variations` (list)
- on every variation: `skuId`, `quantityDescription`, `price` (object with `mrp` and
  `offerPrice` keys), `inventory.inStock` (bool), `podId`

## 9. Empty result

OPEN. What `searchPLV2.data` looks like for a term with no matches was not captured. Until it
is (Phase 3, nonsense term), `cards` with no product objects is `SCHEMA_DRIFT` with reason
`empty_signature_unconfirmed`.

Partial renders are expected: the tail of one response carried an "Oops, something's not
right" block. Row count is validated against the number of product objects found, and a
response with `statusCode != 0` on the API path is not treated as a result (its meaning is
OPEN, so it is `UNKNOWN` and reported).

## 10. Blocked (CONFIRMED signatures)

| signature | code | note |
|---|---|---|
| HTTP 202 with `<div id="challenge-container">` and `awswaf.com` challenge script | `BLOCKED` on a replay; on a browser navigation, wait for the auto-solve reload once, then `BLOCKED` if it persists | seen on first browser load and on a basic-tier proxy fetch; a stealth-tier fetch passed |
| HTTP 403 on any `/api/instamart` path | `BLOCKED` if the jar was freshly verified this run, otherwise re-bootstrap once (the `tid` JWT expires in about an hour) and then `BLOCKED` | cookieless GET returned 403 |
| Page text `Request Blocked` with `Your request looks automated and has been blocked` | `BLOCKED` | V1 observed this interstitial from a residential IP; not in the playbooks, kept as a signature |
| `locationSource == "seo"` with a 200 | `LOCATION_NOT_SET`, not blocked | the silent default store |

## 11. Drift watchlist (what `health` asserts)

`___INITIAL_STATE___` extractable; `userLocation.locationSource == "swgyUL"`;
`userLocation.address` contains `700048`; `storeDetailsV2.storeId` present (warn if not
`1388313`); `searchPLV2.data.cards` is a non-empty list; at least one product with a
variation carrying every key in section 8; `price.offerPrice.units` present;
`inventory.inStock` is a bool; `podId == storeDetailsV2.storeId`.

## 12. Known to change

`tid` expires hourly. The WAF token has its own lifetime. The header search button's label
cycles placeholder terms (irrelevant to V2, which never uses the search UI after bootstrap).
Class names are build-hashed and unused. `robots: noindex` on the search route.

## 13. Fixtures to capture in Phase 3

`normal` (the SSR HTML for 700048 Mango, trimmed to the state script with five products
including a Sold Out one and a no-discount one), `empty`, `out_of_stock` (an
`OOSItemCollectionCard` item), `missing_mrp` (a no-discount row), `corrupted`, one
`search_v2_api` POST response for pagination, and one cookieless `seo` document for the
wrong-store test.

## Open questions

1. `page_type` and `is_pre_search_tag` values for the `search/v2` POST body.
2. Exact nesting from `cards[]` to product objects inside `GridWidget` and
   `OOSItemCollectionCard`.
3. Which price keys are populated on a row without a discount; whether `mrp` is absent or
   equal to `offerPrice`.
4. Empty-result signature; meaning of non-zero `statusCode`; `nextOffset` at the last page.
5. Types of `category`, `subCategoryType`, `superCategory`; shape of `sla`, `medias`,
   `imageIds`; CDN base for images; whether a product URL exists in the state.
6. One row per variation (A10) and `stock_qty` from stock-phrased `allowedQuantity` (A11).
7. Exact HttpOnly cookie names (optional; jar-level persistence makes this unnecessary).
8. Whether the residential IP that V1 used is now blocked outright, or whether the playbook's
   stealth-tier finding generalises. This decides whether Swiggy is reachable at all without
   the proxy in question A1.
