# Blinkit

Source: `docs/playbooks/01-blinkit-swiggy-2026-08-18.md` sections 2, 3, 5, 6, 7.1 and
`docs/playbooks/02-blinkit-swiggy-zepto-2026-08-19.md` section 2. The two agree on every point.
CONFIRMED means observed live on 18 August 2026. OPEN means neither playbook says.

Adapter name: `blinkit`. Reference probe: 700048, "Mango", expected city Kolkata, expected
state West Bengal.

## 1. Profile

| | |
|---|---|
| Host | `https://blinkit.com` |
| Framework | Redux single-page app |
| Where the data lives | `window.__reduxStore__.getState()` |
| Zero-JS route | No |
| Location cookies readable by page JS | Yes |
| Map confirm step | No. Clicking the suggestion applies the location and reloads the page |
| Store id at 700048 | `merchant_id` 30872. E-cards and vouchers sit on merchant 35940 |
| ETA at 700048 | 20 minutes (it read "Currently unavailable" minutes earlier in the same session) |
| Stock granularity | Integer `inventory`, authoritative |
| Price unit | Rupee string with the symbol, `"₹34"` |
| Search ranking | Lexical, with bleed (a sattu product appeared in the mango top 20) |

## 2. Location flow (CONFIRMED)

1. `goto https://blinkit.com`, wait for `domcontentloaded`. Dismiss any app-download banner.
2. Click the delivery location selector in the header. Its label is `Select Location` when no
   location is set, or `Delivery in X minutes` plus the address when one is.
3. Fill the input with placeholder `search delivery location` with the pincode. Type it rather
   than set it, so the autocomplete fires.
4. Wait for the suggestion list. For 700048 it held four entries, one of them
   `Purani Basti, Patehra, Maihar, Madhya Pradesh`. The accessible name of the correct one was
   `PatipukurKolkata, West Bengal 700048, India` (no space between the locality and the city;
   substring match, never equality).
5. Select the suggestion whose text contains the pincode **and** the expected city or state
   from the `LocationExpectation`. If nothing matches, raise `LocationNotSetError` with the
   full list of suggestion texts in the reason. Never fall through to index 0.
6. The page reloads with the location applied.

V1 lessons kept: a selector of the form `text=<pincode>` matches the input box you just typed
into and "succeeds" without selecting anything; a keyboard `ArrowDown, Enter` picks index 0,
which is exactly what step 5 forbids. Neither is used.

## 3. Readback and verification (rule 4)

Run before the first search and after every location retry.

| witness | status | what is asserted |
|---|---|---|
| Header text after the reload | CONFIRMED to show `Delivery in 20 minutes` plus the full address | contains the requested six-digit pincode; `effective_pincode` is that pincode as found in the header |
| `getState().data.location`, `data.merchant`, `data.eta`, `data.addressesV2` | OPEN. The slices exist (the slice names are confirmed) but were never dumped | captured as `page_state` evidence on every location set. If Phase 2 shows the pincode or a store id inside, that becomes the primary witness and the header the second |
| `merchant_id` on the returned rows | CONFIRMED 30872 on 19 of 20 rows, 35940 on the voucher row | recorded per row; not a job-level assertion because more than one merchant can legitimately appear |

If the header reads `Currently unavailable` the store may be closed rather than the location
wrong. Whether search still returns rows in that state is OPEN. Until known, that header text
with the pincode present is accepted as a verified location with `eta_minutes = None`, and
the summary counts it under `store_unavailable`.

`eta_minutes` is the integer in `Delivery in N minutes` from the header. `store_or_seller_id`
on each row is that row's `merchant_id` as a string.

## 4. Search (CONFIRMED)

1. Click the header search input, placeholder `Search for atta dal and more`.
2. Type the term, press Enter.
3. Results render progressively. Scroll (the reference implementation used four wheel steps of
   3000 px with 800 ms pauses) until the snippet count stops growing or reaches `max_results`.

The playbook's reference implementation navigates to `https://blinkit.com/s/?q=<term>` instead
of using the header input. That route is in the playbook but not in the confirmed step list;
it is the navigation fallback if the header input is not found, and it is logged as
`navigation=direct_url` when used. It is not a separate data strategy.

## 5. Strategy ladder

| step | strategy id | what | status |
|---|---|---|---|
| primary | `redux_store` | `page.evaluate` of `JSON.stringify(window.__reduxStore__.getState().ui.search.searchProductBffData)` after the scroll settles, stored as `page_state` | CONFIRMED source of truth |
| evidence | `network_capture` | every JSON response from a `blinkit.com` host during step 4, captured with `page.on("response")`, stored as `network_response` | the URL of the search response is OPEN; this capture is how Phase 2 learns it. Not parsed |
| DOM | none | the DOM lacks `inventory` and every id | not in the ladder |

Do not inject a `fetch` or `XMLHttpRequest` patch from page script. It captured zero responses
live, because the search response lands before an injected patch can attach. Playwright's
`page.on("response")` hooks below page JavaScript and is unaffected.

## 6. Envelope

```
searchProductBffData
└── snippets[]
    ├── data              <- product fields, only when data.product_id is present
    ├── tracking          <- widget_meta, impression_map, click_map, entry_source_map, common_attributes, interactions_map
    ├── widget_type       <- product_card_snippet_type_2 | grid_container_vr | image_text_vr_type_header
    └── layout_config
```

Rows are the snippets whose `data.product_id` is present, in array order. De-duplicate on
`data.product_id`, keeping the first occurrence.

## 7. Field map

| output column | path under `snippet.data` | type as served | rule |
|---|---|---|---|
| `platform_product_id` | `product_id` | string (`"298"`, `"18612"` observed) | keep as text, never cast |
| `product_name` | `name.text` | string | required |
| `brand` | `brand_name` | string or null | key required, value may be null |
| `pack_size` | `variant.text` | string, `"600 ml"`, `"10 x 150 ml"` | required |
| `selling_price` | `normal_price.text` | string, `"₹34"` | strip `₹`, commas, whitespace; `Decimal`; x100 to paise |
| `mrp` | `mrp.text` | string or null (null on the Paper Boat rows) | key required; null means `None`, no anomaly, it is a documented case |
| `in_stock` | `inventory` with `product_state` as cross-check | int; `"available"` or `"out_of_stock"` | `inventory > 0`. If `product_state` disagrees, anomaly `state_inventory_disagree`, `inventory` wins |
| `stock_qty` | `inventory` | int, 0 to 26 observed | as is |
| `eta_minutes` | header at location time | | see section 3 |
| `store_or_seller_id` | `merchant_id` | int or string | as string |
| `category_path` | not in the playbook | | `None`. OPEN |
| `product_url` | not in the playbook (`click_action` exists, shape undocumented) | | `None`. OPEN. V1 assumed `/prn/<slug>/prid/<id>`; unverified, not used |
| `image_url` | `image` exists, shape undocumented | | `None`. OPEN |
| `currency` | | | `"INR"` |
| raw only | `display_name.text`, `group_id` (int), `merchant_type`, `offer_tag`, `offer`, `product_badges`, `overlay_badges`, `rating`, `media_container`, `eta_tag`, `eta_identifier`, `stepper_data_v2.max_count`, `atc_action`, `meta`, `cta`, `ui_config` | | not output columns; stay in the raw payload |

Fields that must never be read for a value: `is_sold_out` (false on every row including the
zero-inventory one), `stepper_data_v2.max_count` (mirrors `inventory`, derived).

## 8. Structural requirements (missing means `SCHEMA_DRIFT`)

- `window.__reduxStore__` is defined and `getState()` returns an object with `ui.search`
- `ui.search.searchProductBffData.snippets` is a list
- on every snippet with `data.product_id`: keys `name.text`, `variant.text`,
  `normal_price.text`, `mrp`, `brand_name`, `inventory`, `product_state`, `merchant_id`,
  `group_id`, `product_id`
- `inventory` is an integer, `product_state` is one of the two documented strings

## 9. Empty result

OPEN. No empty search was captured. Until the empty signature is captured (Phase 2, with a
nonsense term such as `xqzvwkq`), a snippet list with no product rows is classified
`SCHEMA_DRIFT` with reason `empty_signature_unconfirmed`, not `NO_RESULTS`. That is loud on
purpose.

## 10. Blocked

OPEN. No bot wall was observed on Blinkit in either session. The generic classifier applies:
HTTP 403 on the document is `BLOCKED`, HTTP 429 is `RATE_LIMITED`, a navigation timeout is
`NETWORK_TIMEOUT`, a page that loads without `window.__reduxStore__` is `UNKNOWN` and is
reported as a bug to classify.

## 11. Drift watchlist (what `health` asserts)

`window.__reduxStore__`; `getState().ui.search.searchProductBffData.snippets` is a list with
at least one snippet carrying `data.product_id`; on that snippet every key in section 8 with
the documented type; `data.inventory` is an int; the header contains `700048`; at least one
row has `merchant_id == 30872` (warning only).

## 12. Known to change

Inventory drifts within minutes; rows at inventory 1 flip on a single order. Store status
flips between "Currently unavailable" and an ETA. Widget types and the number of non-product
snippets vary. Class names are irrelevant because nothing here uses them.

## 13. Fixtures to capture in Phase 2

`normal` (700048 Mango, trimmed to five rows including one at inventory 0 and one with null
MRP; the 18 August table already documents both cases), `empty`, `out_of_stock`
(the Chaunsa Mango row), `missing_mrp` (a Paper Boat row), `corrupted` (truncated JSON), plus
the `data.location`/`data.merchant`/`data.eta` evidence dump.

## Open questions

1. URL of the search XHR (V1 guessed `/v\d+/search` and `/v1/layout/search`; unverified).
2. Contents of `data.location`, `data.merchant`, `data.eta`, `data.addressesV2`: do they carry
   the pincode or a store id usable as a readback?
3. Exact header string format after location set, and behaviour of search while the header
   reads "Currently unavailable".
4. Shapes of `image`, `click_action`, `rating`, `eta_tag`; whether a product URL can be
   built without guessing.
5. Category information: none is listed in the playbook field map.
6. Empty-result signature.
7. Blocked signature.
8. Whether `/s/?q=<term>` navigation produces the same Redux slice as the header search.
9. `merchant_type` values (never sampled).
