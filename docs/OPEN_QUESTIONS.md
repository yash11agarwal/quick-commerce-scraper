# Open questions

Everything the requirements, the standing rules or the playbooks leave open, in one place.
Section A was answered on 1 September 2026; the answers and where each landed are in
`docs/ARCHITECTURE.md` section 21, and the recommendations below are kept for the record.
Section B lists inconsistencies found in the requirements and how each was resolved. Section C
is the list of gaps that can only be closed by looking at the live site; Phases 2 and 3 close
them platform by platform.

Still genuinely open after the answers: A7 (final platform set, "decide later"), A18 (Zepto has
no confirmed readback carrying the pincode; rule 4 stays strict until Phase 3 looks), and
whether a proxy will exist at all (A1), which decides whether Swiggy is reachable (B9).

---

## A. Decisions (answered 1 September 2026; see ARCHITECTURE.md section 21)

### A1. Proxy
Which residential proxy provider, is rotation per request or per session, and how are the
credentials supplied? The design reads `QCOM_PROXY_SERVER`, `QCOM_PROXY_USERNAME`,
`QCOM_PROXY_PASSWORD` from `.env` and applies them at browser launch. Per-session rotation
(one egress per browser context) fits the location-bootstrap model; per-request rotation
would break the session jars. This also decides whether Swiggy is reachable at all: V1 saw an
explicit "Request Blocked" wall from a residential IP, and the playbooks needed a stealth
proxy tier for cookieless fetches.

### A2. Logged-in sessions
Does any platform need a logged-in account? The playbooks did everything as a guest. If the
answer is no, V2 has no credential handling for platforms at all, which is simpler and safer.

### A3. Volume and frequency
Roughly how many products x pincodes x platforms per run, and how often? At the V1 throttle
(8 to 12 seconds between requests per host) a job costs about 15 to 40 seconds after location
bootstrap, so 50 products x 5 pincodes x 4 platforms is roughly 4 to 10 hours with one
context per platform. The answer decides the concurrency defaults and whether the throttle is
too conservative.

### A4. Headless or headed, and the machine
Recommendation: headless by default, `--headed` flag for debugging. What machine runs this
(OS, RAM, CPU)? The default runs four Chromium instances at once; on a machine with less than
8 GB RAM I would cap `max_platforms_in_parallel` at 2.

### A5. History retention
Recommendation: keep every run in SQLite indefinitely, because the run-over-run price check
(requirements section 5) needs the previous run, and disk is cheap at this volume (a few MB
per run including compressed raw payloads). Alternative: a `prune --older-than` command.
Do you want history kept, and is a prune command needed?

### A6. What happens to V1 on this branch
Recommendation: delete `qc_scraper/`, `main.py`, `track_products.py`, `export_excel.py`,
`dashboard.py`, `web/`, `scripts/`, `targets.xlsx`, `inputs.xlsx`, `tests/` and
`docs/flowchart.md` in Phase 1, so the branch holds one scraper and `python -m qcom` is the
only entry point (as `CLAUDE.md` describes). The main branch keeps V1 untouched. Alternative:
keep V1 alongside until V2 reaches parity on Blinkit and BigBasket, at the cost of two test
suites and two configs in one tree.

### A7. Platform set
Confirm: Blinkit, Swiggy Instamart, Zepto, BigBasket. Amazon Now and Flipkart Minutes have no
playbook and are dropped, per requirements section 0. Also confirm the platform key for
BigBasket is `bigbasket` (V1 used `bigbasket_now`; the playbook covers the `bbnow` context,
and slotted BigBasket is unverified).

### A8. The `city` column
Requirements section 3 makes `city` optional and pass-through. Every playbook says the
autocomplete for 700048 offers a Madhya Pradesh decoy and that the correct suggestion must be
picked by pincode plus expected city or state, never index 0. So the adapter needs an expected
city (or state) per pincode. Recommendation: make `city` required in the `pincodes` sheet and
add an optional `state` column; the matcher accepts a suggestion containing the pincode and
either the city or the state. Alternative: keep `city` optional and fail any pincode without
one with a validation error that says why.

### A9. `strategy` column
See B1. Recommendation: add it as the 30th column of `results`.

### A10. Swiggy: one row per variation
A Swiggy product carries several variations (pack sizes), each with its own SKU id, price and
stock flag. Recommendation: one `results` row per variation, sharing the product's
`result_rank`, so pack-size matching works and nothing is hidden. Alternative: one row per
product using the first variation, which is what V1 did and what loses the other pack sizes.

### A11. Swiggy: `stock_qty` from the cart limit message
Swiggy exposes no stock count, but `cartAllowedQuantity.allowedQuantity` is effectively the
remaining stock when `quantityLimitBreachedMessage` reads `That's all we have in stock at the
moment!`, and a per-order cap when it reads `Only N unit(s) of this item can be added per
order.` Recommendation: leave `stock_qty = None` for Swiggy always, because hard rule 1 says
never infer, and the string could change wording silently. Alternative: fill `stock_qty` only
on an exact match of the stock-phrased string, and record which string was matched.

### A12. Zepto: which price is `selling_price`
Zepto sends `sellingPrice`, `discountedSellingPrice`, `superSaverSellingPrice` and
`zeptoPassPrice`. The `marketplace=SUPER_SAVER` cookie decides which one the card shows.
Recommendation: `discountedSellingPrice` as `selling_price`, `mrp` as `mrp`, the others kept
in the raw payload only. Alternative: `sellingPrice`. Say which price you compare against on
the other platforms and I will match it.

### A13. `price_per_unit` basis
Recommendation: rupees per 1 kg, per 1 L, or per 1 piece, whichever base unit the pack
parses to. BigBasket's own `base_price` is per 100 g, which is the other common choice.

### A14. YAML dependency
Requirements section 4 names `config.yaml`, but `pyyaml` is not on the approved dependency
list. Options: add `pyyaml` (V1 already uses it), or use `config.toml` read by Python 3.11's
built-in `tomllib` with no dependency at all. Recommendation: `pyyaml`, because the
requirement says YAML and V1 users already know the file.

### A15. Results beyond the first page
`max_results_per_query` defaults to 20. First pages hold 20 rows (BigBasket), 29 (Zepto), 39
products (Swiggy), and Blinkit loads more on scroll. Pagination past page 1 is unverified on
BigBasket and Zepto, and the Swiggy POST body has two unrecorded values. Recommendation:
Phases 2 and 3 deliver first-page results and cap `max_results` at the first page; pagination
is a Phase 4 item once each mechanism is captured live. Do you need more than the first page
in the first working version?

### A16. Session jars on disk
The design saves each verified browser session (cookies including HttpOnly, localStorage) to
`sessions/<platform>_<pincode>.json` so later runs skip the location UI. These files contain
session tokens and are gitignored. Is that acceptable on the machine in A4, or should every
run bootstrap from nothing?

### A17. Exit status threshold
Recommendation: exit non-zero when more than 20% of jobs failed, or when any platform was
`BLOCKED`. Configurable in `config.yaml`.

### A18. Zepto readback without a pincode
No confirmed Zepto readback carries the pincode. What is confirmed: the store id and city in
the `serviceability` cookie, the store id on every row, and the coordinates. `formattedAddress`
in localStorage may carry the pincode; unverified. Hard rule 4 says a job without a verified
pincode is a failure. Recommendation: in Phase 3, look for the pincode in `formattedAddress`
and the header first. If neither carries it, I come back to you with the evidence rather than
weakening the rule. Alternative you could pre-approve: accept city plus non-default store id
plus non-default coordinates as Zepto's verification, with `effective_pincode` written as
`None` and the `results` row marked accordingly.

### A19. A `reparse` command
Requirements section 2 says parsing must be re-runnable over stored raw payloads, but the
command list in `CLAUDE.md` has no command for it. Recommendation: add
`python -m qcom reparse --run-id <id>` in Phase 4, which re-parses every stored capture of a
run with the current parsers and regenerates the workbook, so a parser fix can be applied to
old data without re-scraping.

### A20. Health probe
`health` uses the playbooks' probe (700048, "Mango") on all four platforms so its output can be
compared to the playbook data tables. Fine, or would you rather it used the first active
pincode and product from `input.xlsx`?

---

## B. Inconsistencies found, with proposed resolutions

### B1. `strategy` column
Requirements section 3 lists the `results` columns "exactly" and has no strategy column;
section 6 says "the output row must record which strategy produced it". Proposed: section 6
wins, `strategy` becomes column 30.

### B2. `city` optional versus needed
Section 3 versus every playbook's suggestion-matching rule. See A8.

### B3. No HTTP client in the approved dependencies
The playbooks' fast paths (Swiggy SSR, BigBasket and Zepto replays) are plain HTTP requests
with the browser's cookie jar. `requests` and `httpx` are not on the approved list. Resolved
without a new dependency: Playwright's `APIRequestContext` (`page.context.request`) makes
requests with the context's jar, HttpOnly cookies included. No question, just confirming you
are happy with that.

### B4. `io/` directory name
`CLAUDE.md` names the Excel layer `io/`, which is also a standard library module name. As the
sub-package `qcom.io` there is no clash. Kept as named.

### B5. "Do not filter" versus the playbooks' keyword filter
Requirements section 5 says return the top N by platform rank and do not filter; the
playbooks say results need a keyword filter (Zepto's semantic drift, BigBasket's `aam`
expansion). Resolved: no filtering, `match_score` on every row so you can filter in Excel.
Noting that a BigBasket filter would need the vernacular synonym set (`aam`), which
`match_score` does not know; a mango row named "Aam Panna" will score low on the token
"mango". That is honest, but worth knowing.

### B6. Blinkit inventory: capped or exact
V1's README says the integer is backend-capped and an estimate; the playbook calls it exact
and observed values 0, 1, 2, 3, 5, 7, 8, 9, 11, 14, 15, 26. Playbook wins: `stock_qty` is the
integer as served, no "estimate" label.

### B7. `NO_RESULTS` as both an error code and a finding
The policy table lists it as a code and says it is not an error. Resolved: it stays in the
`ErrorCode` enum (so it is reported distinctly) and the job status is a separate enum where
`NO_RESULTS` is a terminal success state with zero rows.

### B8. Entry points
`CLAUDE.md` describes `python -m qcom ...`; V1 uses `main.py` and friends. V2 is the `qcom`
package. What happens to the V1 entry points is A6.

### B9. Definition of done may be unreachable without A1
"`health` passes for every platform" requires Swiggy to answer from wherever this runs. V1
was walled from a residential IP; the playbook's clean passes used a stealth proxy tier for
cookieless fetches. If A1's answer is "no proxy", Swiggy may not be reachable and the
definition of done needs a carve-out. Flagging now rather than in Phase 3.

### B10. `stock_qty` column versus platforms with no count
Requirements section 3 has a `stock_qty` column; the BigBasket playbook warns against a
schema with a mandatory numeric quantity. Resolved: the column exists and is `None` on every
Swiggy and BigBasket row, never 0 or 1.

---

## C. Playbook gaps to close live (no answer needed now)

These are the "capture it and look" items. Each platform spec lists them in full; this is
the short form so you can see the size of the job. The rule for each: the parser does not
exist until the fixture does, and an unconfirmed empty-result signature is a loud
`SCHEMA_DRIFT`, not a quiet `NO_RESULTS`.

### Blinkit (Phase 2)
Search XHR URL; contents of the `data.location`, `data.merchant`, `data.eta` slices; exact
header text; behaviour while the store reads "Currently unavailable"; shapes of `image`,
`click_action`, `rating`, `eta_tag`; any category field; empty and blocked signatures;
whether `/s/?q=` produces the same slice; `merchant_type` values.

Status 1 September 2026: the adapter exists and stores exactly the evidence that answers the
first two (every JSON response during a search, and the four `data.*` slices), but none of
these has been looked at because the build environment could not reach the site. The
platform spec, section 15, lists the one live run that closes them.

### Swiggy Instamart (Phase 3)
`page_type` and `is_pre_search_tag` for pagination; nesting from `cards[]` to products;
price keys on a no-discount row; empty signature; non-zero `statusCode` meaning; category
value types; `sla`, `medias`, `imageIds` shapes; product URL; whether the current egress is
walled.

### Zepto (Phase 3)
The UI click path for the first bootstrap; a readback carrying the pincode (A18); the path
from `layout` to products; replay headers; the displayed price field (A12); empty signature;
pagination; URL slug and image path; whether the API chain can replace the UI bootstrap.

### BigBasket (Phase 3)
Suggestion texts; map confirm on other paths; cookie-only replay; pagination; `bucket_id`;
slotted versus bbnow; `inv_info` on a combo row; jar TTL; header endpoint and `images[]`
layout; empty and blocked signatures; other `avail_status` values.
