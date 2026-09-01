# Zepto

Source: `docs/playbooks/02-blinkit-swiggy-zepto-2026-08-19.md` section 4 and the
cross-platform sections 5 and 7. This is the only playbook that covers Zepto. CONFIRMED means
observed live on 19 August 2026. OPEN means the playbook does not say.

Adapter name: `zepto`. Reference probe: 700048, "Mango", expected city Kolkata, expected
store name `KOL-Belgachia`.

## 1. Profile

| | |
|---|---|
| Host | `https://www.zepto.com` (`zeptonow.com` now 301s here) |
| API host | `bff-gateway.zepto.com` (not `api.zeptonow.com`, which V1 listened on) |
| Image host | `cdn.zeptonow.com` |
| Framework | Next.js App Router (React Server Components). No `__NEXT_DATA__`; streamed `self.__next_f.push()` chunks that are drained after hydration |
| Where the data lives | The XHR response body of the search POST, **read before React hydrates** |
| Zero-JS route | Partially: an SSR grid exists, but bound to the default store only |
| Location cookies readable by page JS | Yes, all of them, and settable |
| Map confirm step | Sometimes |
| Store id at 700048 | `42dd88f4-dc27-496e-80f6-5df99c7f8ea4`, name `KOL-Belgachia`, city Kolkata, coords 22.6015112 / 88.4003915 |
| ETA at 700048 | 15 minutes |
| Stock granularity | Integer `availableQuantity`, authoritative, **API only** |
| Price unit | **Paise** integers. `mrp: 20000` is ₹200 |
| Search ranking | Semantic (`SEMANTIC_EXPERIMENT_V4`, `vector_recall_v8`), heavy drift, unstable between pulls |

## 2. The finding that shapes everything: the DOM lies about stock

Zepto runs an experiment `always_in_stock` (`variantName: test, enable: true`) that rewrites
client state after hydration. Measured on one page load, 38 cards:

| source | `availableQuantity` | `outOfStock` |
|---|---|---|
| React props on the card | `10` on all 38, zero variance | `false` on all 38 |
| Raw XHR response body | 0, 1, 2, 3, 4, 6, 7, 9, 12, 25 | `true` on 9 rows |

Out-of-stock items render a normal card with a live ADD button and no badge. Therefore:
**V2 never reads Zepto product data from the page.** Not the DOM, not React fibers, not any
`page.evaluate`. Only the network response body, captured with `page.on("response")` (which
hooks below page JavaScript), or a direct replay of the POST.

## 3. Location flow

Cookies (CONFIRMED, all JS-readable and settable):

| cookie | content |
|---|---|
| `latitude`, `longitude` | plain decimals |
| `user_position` | URL-encoded JSON `{latitude, longitude}` |
| `serviceability` | URL-encoded JSON with `primaryStore.storeId`, `etaInMinutes`, `storeDetailedInfo.{city, name}` |
| `aws-waf-token` | WAF challenge token, required on every request |
| `session_id`, `device_id`, `session_count` | session identity |
| `marketplace` | `SUPER_SAVER`; decides which price field the card shows |
| `csrfSecret`, `XSRF-TOKEN` | CSRF pair |

localStorage carries zustand-persisted slices: `user-position` (with `placeId`, `name`,
`formattedAddress`, `shortAddress`), `cart`, `header-store`, `userRecentSearches`,
`locationPermission`.

Location endpoints (CONFIRMED to exist and to be what the app calls):

| endpoint | purpose |
|---|---|
| `GET /api/v1/maps/place/autocomplete/?place_name=700048` | pincode to Google place id |
| `GET /api/v1/maps/place/details/?place_id=<id>` | place id to coordinates |
| `GET /api/v1/user/customer/address/location?latitude=&longitude=` | serviceability; mints the store |
| `GET /lms/api/v2/get_page?latitude=&longitude=&page_type=HOME&version=v2` | home layout |

**The UI click path is OPEN.** The playbook documents the cookies and the endpoints, and says
the map flow can be skipped after the first bootstrap by setting cookies directly, but it does
not enumerate which elements to click and type in for that first bootstrap. V1's selectors
were guesses. Phase 3 starts by walking the picker in a headed browser and recording the
accessible names, exactly as the Blinkit and Swiggy playbooks did.

Bootstrap design, pending that:

1. `goto https://www.zepto.com`. Wait for the WAF auto-solve if the challenge appears.
2. Open the location picker, type the pincode, pick the suggestion that contains the pincode
   and the expected city, confirm on the map if the map appears.
3. Read the `serviceability` cookie. Assert `storeDetailedInfo.city` equals the expected city.
4. Save `context.storage_state()` to `sessions/zepto_<pincode>.json`.

On later runs the jar is loaded, step 3 is repeated, and the UI flow is skipped when it passes.

## 4. Readback and verification (rule 4)

This is the weakest platform for rule 4 and it is flagged as question A18.

| witness | status | what is asserted |
|---|---|---|
| `serviceability.primaryStore.storeId` cookie | CONFIRMED | recorded as the job's store id; the Bengaluru default `b4dc8d65-ed2e-4142-81b6-373982b13500` is `LOCATION_NOT_SET` |
| `serviceability.storeDetailedInfo.city` | CONFIRMED | must equal the expected city (case-insensitive) |
| `storeId` on every product row | CONFIRMED present on every result | must equal the cookie's store id; mismatch is `store_id_mixed` and fails the job |
| Header address text | mentioned as an assertion source, format not recorded | captured as evidence |
| localStorage `user-position.formattedAddress` | CONFIRMED to exist; whether it contains the pincode is **OPEN** | captured as evidence. If Phase 3 shows the pincode in it, it becomes the `effective_pincode` witness |
| Pre-location `get_page` coordinates | CONFIRMED `12.96902 / 77.75395` for the default | coordinates in `latitude`/`longitude` cookies must not equal the Bengaluru default |

**No confirmed witness carries the pincode.** Until Phase 3 finds one, `effective_pincode`
cannot be filled truthfully for Zepto, and per `CLAUDE.md` a job without a verified pincode is
a failure. The decision in A18 is whether city plus store id plus non-default coordinates is
an acceptable readback for Zepto, with `effective_pincode` written from `formattedAddress`
only if it contains the six digits.

`eta_minutes` is `serviceability.etaInMinutes`.

## 5. Strategy ladder

| step | strategy id | what | status |
|---|---|---|---|
| primary | `xhr_capture` | navigate `https://www.zepto.com/search?query=<term>` in the located page; capture every `POST https://bff-gateway.zepto.com/user-search-service/api/v3/search` response body with `page.on("response")`, stored as `network_response` in arrival order | CONFIRMED source of the truthful stock numbers |
| secondary | `api_replay` | `page.context.request.post` to the same endpoint with the body in section 6, using the context's jar; stored as `api_replay` | the endpoint and body are CONFIRMED; the headers needed beyond cookies (the CSRF pair, any `intentId` rules) are OPEN. Replay was never exercised in the playbook (open item 2) |
| DOM | **forbidden** | see section 2 | never |

Search request body (CONFIRMED):

```json
{"query": "Mango", "pageNumber": 0, "mode": "TYPED", "intentId": "<uuid>", "userSessionId": "<uuid>"}
```

`mode` is `TYPED` or `AUTOSUGGEST`. There is no `storeId` in the body: store binding is
entirely cookie-driven, which is why section 4 matters so much.

## 6. Envelope

Response keys (CONFIRMED): `layout`, `footer`, `header`, `selectedTabId`, `pageProductCount`,
`totalProductCount`, `currentPage`, `hasReachedEnd`, `userSessionId`, `filters`, `meta`,
`experiments`, `pageType`, `pageMeta`, `nextPageParams`, `filterMode`.

Pagination (CONFIRMED for "mango"): `totalProductCount: 220`, `pageProductCount: 29`,
`nextPageParams: {filterMode: 1, pageNumber: 1}`. Walking it was never exercised (open item 3).

The path from `layout` to the product objects is OPEN. Each product object has roughly 70
keys; the ones below are confirmed. V1's assumption of a `productResponse` wrapper is not in
the playbook and is not used. The parser reads the exact path once the Phase 3 fixture
shows it.

`experiments` is stored with every capture, and `always_in_stock` is logged on every pull.

## 7. Field map

| output column | path on the product object | type as served | rule |
|---|---|---|---|
| `platform_product_id` | `productVariant.id` (the `pvid`) | uuid string | de-duplication key. `id`/`objectId` (store-scoped) and `product.id` stay in raw |
| `product_name` | `product.name` | string | required |
| `brand` | `product.brand` | string | key required. `product.brandId` raw |
| `pack_size` | `productVariant.formattedPacksize` | string, `"1 pc (120 ml)"` | required. `packsize`, `unitOfMeasure`, `weightInGms` raw |
| `selling_price` | `discountedSellingPrice` | paise int | as is, no conversion. Question A12 covers `sellingPrice` and `superSaverSellingPrice` |
| `mrp` | `mrp` | paise int | as is |
| `in_stock` | `availableQuantity` with `outOfStock` as cross-check | int; bool | `availableQuantity > 0`. Disagreement is anomaly `stock_flags_disagree`; `availableQuantity` wins (it is the authoritative field per the cross-platform table) |
| `stock_qty` | `availableQuantity` | int | as is. Never `productVariant.quantity` (runs higher: 25 vs 31, 6 vs 22) |
| `eta_minutes` | `serviceability.etaInMinutes` at location time | | |
| `store_or_seller_id` | `storeId` | uuid string | asserted equal to the cookie's store id |
| `category_path` | `primaryCategoryName` | string | single level; `primaryCategoryId` raw |
| `product_url` | DOM cards link to `/pn/<slug>/pvid/<uuid>`; the slug's source in the API object is OPEN | | `None` until the slug field is identified. No placeholder slug |
| `image_url` | not documented | | `None`. OPEN |
| `currency` | | | `"INR"` |
| raw only | `discountPercent`, `discountAmount`, `superSaverSellingPrice`, `zeptoPassPrice`, `allocatedQuantity`, `stockoutThresholdQuantity` (both 0 on every row), `productVariant.maxAllowedQuantity`, `productVariant.quantity`, `productType`, `meta.is_fly_wheel_ad`, `meta.tagsV2`, `ratingSummary` | | |

De-duplication: sponsored cards duplicate organic ones with an identical `pvid`. Keep the
first occurrence in arrival order and its rank. `meta.is_fly_wheel_ad` stays in raw.

## 8. Structural requirements (missing means `SCHEMA_DRIFT`)

- the captured response parses as JSON with keys `layout`, `totalProductCount`,
  `pageProductCount`, `nextPageParams`, `experiments`
- product objects are found at the path fixed in Phase 3
- on every product object: `storeId`, `productVariant.id`, `productVariant.formattedPacksize`,
  `product.name`, `product.brand`, `mrp`, `discountedSellingPrice`, `availableQuantity`,
  `outOfStock`, `primaryCategoryName`
- `availableQuantity` is an int, `outOfStock` is a bool, prices are ints

A capture whose product objects all carry `availableQuantity == 10` and `outOfStock == false`
is the DOM-poisoned shape and must not occur on the network path; if it does, `SCHEMA_DRIFT`
with reason `poisoned_uniform_stock`.

## 9. Empty result

OPEN. `totalProductCount: 0` is the obvious candidate and was not observed. Until captured
(Phase 3), zero product objects is `SCHEMA_DRIFT` with reason `empty_signature_unconfirmed`.

## 10. Blocked (CONFIRMED signatures)

| signature | code |
|---|---|
| HTTP 202 with `challenge-container` (AWS WAF, shared with Swiggy) | on navigation: wait for the auto-solve reload once, then `BLOCKED`; on replay: `BLOCKED` |
| HTTP 403 on `bff-gateway.zepto.com` | `BLOCKED` after one re-bootstrap of the jar |
| `storeId == b4dc8d65-...` with a 200 | `LOCATION_NOT_SET`, not blocked |

## 11. Drift watchlist (what `health` asserts)

`serviceability` cookie parses and carries `primaryStore.storeId`, `etaInMinutes`,
`storeDetailedInfo.city == "Kolkata"` (warn if the store id is not `42dd88f4-...`); one
`user-search-service/api/v3/search` response captured; envelope keys in section 8 present;
product objects located; every key in section 8 present with the documented type;
`availableQuantity` values are not uniformly 10; `experiments` present.

## 12. Known to change

Rank order is not stable between pulls minutes apart. The out-of-stock items form a contiguous
band (ranks 12 to 20 across two pulls) rather than being hidden. `pvid` differs between stores
for fresh produce, so it is not a cross-store join key. Live experiments seen: `guest_cart`,
`new_login_flow`, `PVID_SEARCH_CATALOGUE_EXPERIMENT`, `OR2_MIGRATION_EXPERIMENT`. The whole
`experiments` block is logged on every pull.

## 13. Fixtures to capture in Phase 3

`normal` (the raw POST response for 700048 Mango, trimmed to five objects including an
`outOfStock: true` one and a sponsored duplicate), `empty`, `out_of_stock`, `missing_mrp` (if
any row lacks `mrp`; otherwise a synthetic removal, documented as such), `corrupted`, the
`serviceability` cookie value, the `user-position` localStorage value, and one cookieless
default-store response for the wrong-store test.

## Open questions

1. The UI click path for the first bootstrap (elements, placeholders, whether the map appears).
2. A readback that carries the pincode (`formattedAddress`? header text?). Decides A18.
3. Path from `layout` to the product objects.
4. Headers required for a direct POST replay (CSRF pair, `intentId` rules), and whether
   replay works with the jar alone (playbook open item 2).
5. Which of `sellingPrice`, `discountedSellingPrice`, `superSaverSellingPrice` is the
   displayed price under `marketplace=SUPER_SAVER` (A12).
6. Empty-result signature; pagination via `nextPageParams` (playbook open item 3).
7. Source of the URL slug and the image path on the API object.
8. Whether `availableQuantity` is net of allocations (playbook open item 1).
9. Whether the API chain (autocomplete, details, address/location) can replace the UI
   bootstrap, and whether calling it sets `serviceability` server-side.
