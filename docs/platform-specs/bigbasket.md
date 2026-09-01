# BigBasket

Source: `docs/playbooks/03-bigbasket-2026-08-19.md`, all sections. CONFIRMED means observed
live on 19 August 2026 (one session, 20 rows sampled). OPEN means the playbook does not say.

Adapter name: `bigbasket`. Reference probe: 700048, "Mango", expected city Kolkata. Everything
here was observed under `xentrycontext=bbnow` (the quick-delivery context); slotted BigBasket
is unverified.

## 1. Profile

| | |
|---|---|
| Host | `https://www.bigbasket.com` |
| Framework | Next.js pages router; `__NEXT_DATA__` present but a trap (section 10) |
| Where the data lives | XHR from `/listing-svc/v2/products` |
| Zero-JS route | **No.** Search results are never server-rendered |
| Location cookies readable by page JS | Yes, all of them |
| Map confirm step | Not required in the session; whether other entry paths have one is OPEN |
| Service area at 700048 | `sa_id` 28232 (secondary 30927, 28213 seen in analytics) |
| Fulfilment centre at 700048 | `fc_id` 2443; `path_id` 20890; `_bb_cid` 14 (Kolkata) |
| ETA at 700048 | 6 minutes |
| Stock granularity | Two-state enum `availability.avail_status`. **No integer anywhere in the payload** (confirmed by inspecting the full flattened key set) |
| Price unit | Rupee strings without a symbol, `"74.25"` |
| Search ranking | Synonym-expanded lexical: `mango` expands to `aam`, pulling in Aam Doi, Aam Kasundi, Aam Panna. Only 2 of the top 20 were mangoes. Also an LLM-mediated path with a non-determinism flag (section 6) |

## 2. Location flow (CONFIRMED except step 3)

1. `goto https://www.bigbasket.com`. Dismiss the app banner and any `Got it` tooltip.
2. Click the header delivery selector. Before a location it reads a prompt; after, it reads
   `Delivery in 6 mins  700048, Kolkata`.
3. Type the pincode into the location input. Suggestions render. **The suggestion texts and
   shape were never captured** (the agent's output did not reach stdout). Phase 3 logs the raw
   suggestion strings before any matcher is written. Given the Madhya Pradesh decoy on Blinkit
   and Swiggy, match on pincode plus expected city or state, never index 0.
4. Select the Kolkata / West Bengal suggestion. The location applied without a map confirm.
5. Assert the header before anything else.

V1 lessons kept: the click on the delivery selector must be confirmed to have opened the
location input before typing, because a generic `input[type=text]` fallback typed the pincode
into the product search bar, which accepted it and searched for "700048" as a product with no
error. No generic input fallback exists in V2; if the location input is not found, that is
`LocationNotSetError`.

## 3. Readback and verification (rule 4)

Three witnesses, all required:

| witness | status | what is asserted |
|---|---|---|
| Cookie `_bb_pin_code` | CONFIRMED `"700048"` | equals the requested pincode; this is `effective_pincode` |
| Header text | CONFIRMED `Delivery in 6 mins  700048, Kolkata` | contains the pincode; `eta_minutes` is the leading integer |
| `visibility.sa_id` on every product row | CONFIRMED 28232 on all 20 rows | all rows share one value and that value appears in the `_bb_sa_ids` cookie (`28232,30927` at 700048). Per-row, travels with the data; the strongest witness |

Two things that look like guards and are not:

- `_bb_locSrc` stayed `default` after the pincode was applied. It is not a location-source
  flag. Never gated on.
- `_bb_addressinfo` decodes (base64) to
  `22.6015112|88.4003915|Kasba|700048|Kolkata|1|false|true|true|Bigbasketeer`. The area
  `Kasba` is south Kolkata; the coordinates are the Dum Dum side. The area label is a stale
  default. Never asserted on, never stored as the resolved area.

Evidence captures at location time (`api_replay`): `GET /ui-svc/v2/header/?send_door_info=true&send_address_set_by_user=true`
(the playbook names it "the header assertion source"; its field layout is OPEN) and the
decoded `_bb_addressinfo`, `_bb_lat_long`, `_bb_sa_ids`, `_bb_cid`, `xentrycontext` cookies.

`store_or_seller_id` on each row is `visibility.sa_id` as a string; `fc_id` and `path_id`
stay in raw.

## 4. Strategy ladder

| step | strategy id | what | status |
|---|---|---|---|
| primary | `listing_svc_capture` | navigate `https://www.bigbasket.com/ps/?q=<term>` in the located page; capture `GET https://www.bigbasket.com/listing-svc/v2/products?type=ps&slug=<term>&page=1&bucket_id=52` with `page.on("response")`, stored as `network_response` | CONFIRMED: HTTP 200, ~324 KB, 20 products per page |
| secondary | `listing_svc_replay` | `page.context.request.get` of the **captured page-1 URL with only `page` changed**, for pages 2 to `number_of_pages`, stored as `api_replay` | the endpoint works from inside the located page with the cookie jar; a pure cookie replay with no browser is OPEN (playbook open item) |
| DOM | none | | not in the ladder |

`bucket_id=52` was on the live call and stable across two queries in the session. Its meaning
is OPEN. V2 never constructs the listing URL from scratch; the replay copies the captured
query string and increments `page`, so an unknown parameter is carried through, never
invented.

## 5. Envelope

```
root
├── tabs[]
│   └── [0]
│       ├── product_info
│       │   ├── products[]          <- the rows
│       │   ├── page
│       │   ├── number_of_pages     <- paginate on this
│       │   ├── total_count
│       │   ├── ps_or_search
│       │   ├── show_deal_type
│       │   └── is_tobacco
│       ├── tab_info, search_info
│       ├── injection_filters, injection_filters_1..4
│       ├── switch_filters, quick_filters, navigation_filters, power_filters
│       ├── filter_opts, sort_opts
│       └── bread_crumbs
├── screen_info, base_img_url, analytics_attrs, session_data, extended_results_dest
├── widget_details, consolidated_variants_info, variant_product_info
├── is_llm_timeout                  <- logged on every pull
└── is_llm_flow                     <- logged on every pull
```

## 6. Non-deterministic ranking

`is_llm_flow` and `is_llm_timeout` are stored on the job and emitted as the `llm_flow`
data quality event when either is true. If the LLM path engages, rank order is not guaranteed
reproducible between two pulls of the same query, and a timeout may silently degrade to a
different ranker. This is the only platform of the four with an observable ranking switch.

## 7. Field map

| output column | path on the product row | type as served | rule |
|---|---|---|---|
| `platform_product_id` | `id` | **string** | as text. `requested_sku_id`, `ean_code` (equal to `id`, not a real EAN), `parent_info.{parent_id, child_id}` (ints) and `inv_info.skus[].id` (int) stay in raw. `rating_info.sku_id` is the **parent**, a different entity; never used as an id |
| `product_name` | `desc` | string, without brand | required. Not concatenated with brand (decision 12 in ARCHITECTURE) |
| `brand` | `brand.name` | string, sometimes with trailing spaces (`"Epigamia "`) | strip whitespace. `brand.slug`, `brand.url` raw |
| `pack_size` | `w` | string, `"500 g"`, `"1.75 L"`, `"8 pcs"` | required. `magnitude` + `unit` (`"500"` + `"g"`) raw; `pack_desc` (`"Cup"`, `"Jar"`) raw |
| `selling_price` | `pricing.discount.prim_price.sp` | string, `"74.25"` | `Decimal`; x100 to paise |
| `mrp` | `pricing.discount.mrp` | string | same |
| `in_stock` | `availability.avail_status` | `"001"` or `"000"` | `"001"` is True, `"000"` is False, any other value is `None` plus anomaly `unknown_avail_status` with the value |
| `stock_qty` | none | | `None` always |
| `eta_minutes` | header at location time | | |
| `store_or_seller_id` | `visibility.sa_id` | int | as string |
| `category_path` | `category.tlc_name`, `mlc_name`, `llc_name` | strings | joined with ` > `. Slugs and ids raw. `llc_slug` is a query fragment (`type=pc&slug=mangoes`), not a path |
| `product_url` | `absolute_url` | `/pd/<id>/<slug>/` | prefixed with `https://www.bigbasket.com` |
| `image_url` | `images[]`, five sizes `s/m/l/xl/xxl` on host `bbassets.com` | key layout OPEN | `None` until the fixture shows the layout; intended: the `l` size of `images[0]` |
| `currency` | | | `"INR"` |
| raw only | `pricing.discount.prim_price.{rsp, base_price, base_unit}` (unit-normalised price, e.g. `12.8` per `100g`, worth cross-checking our own normalisation against), `pricing.discount.{d_text, d_avail, deal_score, subscription_price, sec_price}`, `offer_available`, `number_of_skus_sold` (`"32K+ sold (1 mo)"`, bucketed), `rating_info.{avg_rating, rating_count, review_count}`, `visibility.{path_id, fc_id, supply_chain_ecs}`, `availability.{button, label, not_for_sale, show_express}`, `sku_max_quantity`, `children[]`, `variant_info[]`, `combo`, `bxgy`, `variable_weight`, `video_links`, `gif_links`, `prescription_only`, `is_tobacco`, `is_free_reward`, `quality_report` | | |

## 8. Fields that look like inventory and are not

| field | value observed | what it actually is |
|---|---|---|
| `inv_info.skus[].qty` | `1` on all 20 rows, including both out-of-stock rows | not stock. Probably a bill-of-materials multiplier; unconfirmed. A stock field cannot read 1 on an out-of-stock row |
| `sku_max_quantity` | `0` on all 20 | no per-order cap, not zero stock |
| `pricing.discount.camp_detail.{on_inv_v_f, off_inv_v_f, on_inv_c_f, ...}` | | `inv` is **invoice**, discount funding splits |
| `availability.button` (`"Add"` / `"Notify Me"`), `availability.label` (`"Coming back soon"`) | | display strings only |
| `pricing.discount.d_avail`, `offer_available` | string `"true"` | strings, not booleans; truthiness passes for `"false"` too. Never read as booleans |

## 9. Structural requirements (missing means `SCHEMA_DRIFT`)

- `tabs` is a non-empty list; `tabs[0].product_info` is an object with `products` (list),
  `page`, `number_of_pages`, `total_count`
- `is_llm_flow`, `is_llm_timeout` present at root
- on every product: `id` (string), `desc`, `brand.name`, `w`,
  `pricing.discount.prim_price.sp`, `pricing.discount.mrp`, `availability.avail_status`,
  `visibility.sa_id`, `category.{tlc_name, mlc_name, llc_name}`, `absolute_url`
- `avail_status` is a string

## 10. The `__NEXT_DATA__` trap

`__NEXT_DATA__` on `/ps/?q=<query>` holds the **homepage** payload (`custPageData`, My Smart
Basket, Banana Robusta), not the search. A parser that reaches for it gets plausible product
rows from the correct store that have nothing to do with the query, with no error. V2 never
reads `__NEXT_DATA__`, and the health check asserts that the parsed rows came from a
`listing-svc` capture whose URL contains `slug=<term>`.

## 11. Empty result

OPEN. `total_count == 0` with `products == []` is the obvious candidate and was not observed.
Until captured (Phase 3), an empty `products` list is `SCHEMA_DRIFT` with reason
`empty_signature_unconfirmed`.

## 12. Blocked

OPEN. No wall was observed. Generic classifier: 403 on the document or on `listing-svc` is
`BLOCKED`, 429 is `RATE_LIMITED`, a challenge marker (if BigBasket turns out to share the AWS
WAF vendor) is `BLOCKED`, anything else `UNKNOWN`.

## 13. Drift watchlist (what `health` asserts)

`_bb_pin_code == "700048"`; header contains `700048`; a `listing-svc/v2/products` response
captured with `slug=Mango` in its URL; envelope keys of section 9 present; every product key
of section 9 present with the documented type; `avail_status` in `{"001", "000"}` on every
row; a single `visibility.sa_id` across rows, present in `_bb_sa_ids` (warn if not 28232);
`inv_info.skus[].qty == 1` on every row (if this ever varies, the BOM reading gets its test
case); `is_llm_flow` logged.

## 14. Known to change

`is_llm_flow` engages unpredictably. Brand names carry trailing whitespace inconsistently.
`d_text` mixes `"21% OFF"` and `"₹12 OFF"` formats (raw only, never parsed). Cookie jar TTL is
unmeasured. `bucket_id` semantics unknown.

## 15. Replay cookie jar (for reference; V2 uses `storage_state`, not a hand-built jar)

Rebinding a fresh session to 700048 needs `_bb_pin_code`, `_bb_addressinfo`, `_bb_lat_long`,
`_bb_cid`, `_bb_sa_ids`, `_bb_cda_sa_info`, `_bb_nhid`/`_bb_dsid`/`_bb_dsevid`,
`xentrycontext`, `xentrycontextid`/`jentrycontextid`, `_bb_bb2.0`/`bb2_enabled`,
`is_integrated_sa`/`isintegratedsa`, `is_global`, plus the session-minted `csrftoken`,
`csurftoken`, `_bb_vid`, which cannot be faked. Bootstrapping once in a browser and persisting
the jar covers all of these.

## 16. Fixtures to capture in Phase 3

`normal` (page 1 for 700048 Mango, trimmed to five rows including one `"000"` row and one
`combo`-bearing row if any exists), `empty`, `out_of_stock` (fresho! Chausa Mango), a
`missing_mrp` row if one exists (otherwise a documented synthetic removal), `corrupted`, one
page-2 replay response, the header endpoint response, and the raw suggestion strings from
step 3.

## Open questions

1. Autocomplete suggestion text and shape (not captured).
2. Whether a map confirm step exists on other entry paths.
3. Cookie-only replay with no browser at all (unverified; replay in the session was `fetch`
   from inside the located page).
4. Pagination past page 1: `number_of_pages` is present, mechanism untested.
5. `bucket_id` semantics.
6. Slotted (non-bbnow) BigBasket: different assortment or availability shape?
7. `inv_info` bill-of-materials reading: needs a row with non-null `combo`.
8. Cookie jar TTL.
9. Field layout of the header endpoint response and of `images[]`.
10. Empty-result and blocked signatures; whether `avail_status` has values other than
    `"001"` and `"000"`.
