# V2 architecture

Status: Phase 0 design. No code exists yet. Written 1 September 2026 against the three
playbooks in `docs/playbooks/`, the requirements in `docs/REQUIREMENTS.md` and the standing
rules in `CLAUDE.md`. Nothing described here has been run. Anything marked OPEN is a gap the
playbooks do not close; every OPEN item is collected in `docs/OPEN_QUESTIONS.md`.

This document is written for the owner of the project, who is not a programmer. Where a
choice was mine, the reasons are spelled out and the choice is listed again in section 20 so
it can be overturned in one place.

---

## 1. Why a V2 rather than a patched V1

V1 is the `qc_scraper/` package and `main.py` on the main branch. Its own README reports four
of its six platforms broken. The problems are not selector-level, they are structural, and
each one violates a hard rule in `CLAUDE.md`:

| V1 behaviour | Rule it breaks | What V2 does instead |
|---|---|---|
| Marks a pincode "set" when the location modal closes. Never reads the location back. | Rule 4: verify the pincode is in effect | Reads the location back from the site, asserts it, writes `effective_pincode` per row |
| Returns `[]` for "no matches", "blocked" and "parser broke" alike, and logs the same warning for all three | Rule 2: never collapse failure states | Every job ends with an `ErrorCode`; empty is `NO_RESULTS` only with positive evidence |
| Keeps no raw payload. Debug dumps are best-effort, capped at 20 responses, and only on failure | Rule 3: persist raw before parsing | Every response that feeds a row is stored verbatim, compressed, before the parser runs |
| Prices are `float` | Data conventions | Integer paise internally, `Decimal` at the Excel boundary |
| Hunts recursively for any dict with a plausible key name (`iter_dicts_with_keys`) | "When a platform changes": no tolerant parsing | Parsers read exact documented paths; a missing path is `SCHEMA_DRIFT`, loud |
| Listens on `api.zeptonow.com`, which no longer serves search (playbook: `bff-gateway.zepto.com`) | Playbook beats prior knowledge | Endpoints come from the specs, which come from the playbooks |
| Two platforms (Amazon Now, Flipkart Minutes) with no playbook | Requirements section 0 | Dropped. Four platforms, four playbooks |

Nothing from V1 is imported into V2. Three V1 lessons are worth keeping and are recorded in the
relevant platform specs: never use "text contains the typed pincode" as a suggestion selector
(it matches the input box you just typed into), never fall through to a generic
`input[type=text]` (BigBasket's product search bar accepts a pincode without complaint), and
a suggestion click that appears to succeed must be followed by a verification.

What happens to the V1 files on this branch is a question for you (`OPEN_QUESTIONS.md`, A6).

---

## 2. What happens on a run, in plain language

1. You run `python -m qcom run --input input.xlsx --out output/`.
2. The workbook is opened and fully validated before any browser starts. Wrong headers,
   blank required cells, malformed pincodes, duplicate rows: the run stops with a message that
   names the sheet, the cell and the problem. Nothing is coerced.
3. A run id is minted and a run record is written to SQLite together with the complete job
   list: one job per active product x active pincode x selected platform. Nothing has been
   fetched yet, but the plan is on disk. That is what makes resume possible.
4. One worker starts per selected platform. Each worker owns its own browser. Workers share
   nothing except the database and the per-host throttle, so a Swiggy failure cannot touch
   Blinkit.
5. A worker takes its jobs grouped by pincode. For each pincode it sets the location once,
   reads the location back from the site, and asserts it matches. If it does not match after
   the policy's retries, every job for that pincode on that platform fails with
   `LOCATION_NOT_SET`. Nothing is ever searched under an unverified location.
6. For each search term at that pincode, the worker runs the platform's primary strategy from
   its spec, writes every response verbatim (compressed) into SQLite, and only then parses.
   Parsing is a pure function over those stored bytes and can be re-run offline.
7. Every outcome is classified with an `ErrorCode`. Retryable codes are retried per the policy
   table. `BLOCKED` stops that platform for the rest of the run. Five consecutive failures
   (configurable) trip that platform's circuit breaker.
8. When every worker has finished, the output workbook is generated from the database (the
   workbook is a projection of the database, never the other way round), a summary is printed
   with the failure counts at the top, and the process exits non-zero if the failure rate
   crossed the threshold or any platform was blocked.
9. `python -m qcom resume --run-id <id>` reloads the job list, skips finished jobs, runs the
   rest, and regenerates the workbook. No row is written twice.

---

## 3. Module layout

```
qcom/                          the package; `python -m qcom` runs cli/main.py
  __main__.py
  cli/
    main.py                    typer app: run, resume, smoke, health. Argument parsing and exit codes only.
  core/
    models.py                  pydantic models: Job, ProductListing, RawCapture, EffectiveLocation, HealthReport
    errors.py                  ErrorCode enum and the exception classes that carry it
    config.py                  config.yaml plus .env loading, validated with pydantic; config hash
    storage.py                 SQLite: schema, raw payload write/read, job state, listings
    runner.py                  the run loop: job planning, per-platform workers, resume
    retry.py                   policy table to tenacity retry objects; circuit breaker
    browser.py                 Playwright lifecycle: launch, device profile, proxy, session jar persistence
    throttle.py                per-host rate limiter with jitter, thread-safe
    normalise.py               pack size parsing, unit normalisation, price_per_unit, discount_pct
    matching.py                match_score
    quality.py                 data quality counters and run-over-run price comparison
    summary.py                 run summary, exit status
    clock.py                   IST and UTC timestamps
    logging.py                 structlog JSON configuration
  io/
    excel_in.py                input workbook reader and validator
    excel_out.py               output workbook writer
  platforms/
    base.py                    the PlatformAdapter protocol; helpers that know no platform
    registry.py                platform name to adapter class
    blinkit/                   adapter.py, parser.py
    swiggy_instamart/          adapter.py, parser.py, initial_state.py (brace-balanced extractor)
    zepto/                     adapter.py, parser.py
    bigbasket/                 adapter.py, parser.py
    fake/                      fixture-backed adapter for Phase 1 and the end-to-end tests
tests/
  contract/                    the shared adapter contract suite, parametrised over every adapter
  fixtures/<platform>/         trimmed real payloads: normal, empty, out_of_stock, missing_mrp, corrupted
  core/  io/  platforms/       unit tests per layer
docs/
  ARCHITECTURE.md              this file
  REQUIREMENTS.md              the kickoff prompt, verbatim
  OPEN_QUESTIONS.md            everything the playbooks or requirements leave open
  platform-specs/              one checkable spec per platform
  playbooks/                   the source playbooks, verbatim
config.yaml                    versioned, no secrets
.env.example                   documents every secret key with a dummy value
```

Import rules, enforced by a test that reads the import statements of every module:

- `platforms/*` may import from `core.models`, `core.errors`, `core.normalise` and nothing
  else in `core`, and never from `io` or `cli`.
- `io/*` may import from `core.models` and `core.errors` only.
- `cli/*` imports from `core` only.
- No module outside `platforms/` contains the name of a platform. The one place platform names
  appear downstream is `platforms/registry.py`, which `core.runner` uses to look adapters up.

A note on the directory name `io`: the Python standard library also has a module called `io`.
There is no clash because ours is `qcom.io`, a sub-package, and `python -m qcom` never puts
the `qcom/` directory itself on the import path. The name follows `CLAUDE.md`.

---

## 4. The adapter contract

Exactly the five functions in `CLAUDE.md`. The run loop calls nothing else.

```python
class PlatformAdapter(Protocol):
    name: str                      # "blinkit", "swiggy_instamart", "zepto", "bigbasket"
    version: str                   # adapter version, written to run_meta
    hosts: tuple[str, ...]         # hosts the throttle keys on
    probe: Probe                   # known-good query for health: pincode, term, expected city

    def set_location(self, page: Page, pincode: str, expect: LocationExpectation) -> EffectiveLocation: ...
    def search(self, page: Page, term: str, max_results: int) -> list[RawCapture]: ...
    def parse(self, raw: RawCapture) -> list[ProductListing]: ...
    def classify_failure(self, exc_or_response: Exception | ResponseLike) -> ErrorCode: ...
    def health_check(self, page: Page) -> HealthReport: ...
```

What each one is for, in plain terms:

- `set_location` drives the site's own location picker, then **reads the location back** from
  wherever the platform exposes it (header text, a cookie, a state slice, per-row store ids)
  and returns what the site actually has in effect. If it cannot prove the pincode is in
  effect it raises `LocationNotSetError`. It never returns a guess. `LocationExpectation`
  carries the expected city and state so the adapter can pick the right autocomplete
  suggestion; every playbook found a Madhya Pradesh decoy for 700048, so index 0 is never
  trusted.
- `search` performs the platform's primary strategy and returns the raw bytes it captured,
  each tagged with the strategy that produced it. It does not parse. It may return more than
  one capture (the search response, plus location evidence, plus supplementary responses).
- `parse` turns one stored capture into listings. It is a pure function: no network, no
  browser, no clock, no config. It reads the exact paths in the spec. A missing path raises
  `SchemaDriftError` naming the path; an unparseable value yields `None` plus an anomaly.
- `classify_failure` maps an exception or a response to an `ErrorCode`, using the platform's
  known signatures (HTTP 202 with `challenge-container` is `BLOCKED` on Swiggy and Zepto, a
  403 on `/api/instamart` is `BLOCKED`, and so on).
- `health_check` runs the probe (700048, "Mango") end to end and asserts that every path the
  spec's drift watchlist names still exists, returning a report rather than listings.

The `page` argument is a Playwright `Page` that `core.browser` created inside a context it
owns. Adapters use it directly. They never launch browsers, never read config, never write to
the database, and never touch Excel.

Adding a platform means: write the adapter, capture fixtures, pass the contract suite. If that
needs a change in `core/`, the abstraction is wrong, and the change is a conversation, not a
special case.

---

## 5. Data model

All models are pydantic. All money is integer paise. All timestamps are timezone-aware.

### 5.1 ErrorCode

The enum from the requirements, verbatim, plus the one skip code the requirements name:

```
NETWORK_TIMEOUT   RATE_LIMITED   BLOCKED   PROXY_ERROR   LOCATION_NOT_SET
NO_RESULTS        SCHEMA_DRIFT   PARSE_ERROR   UNKNOWN   SKIPPED_PLATFORM_BLOCKED
```

`NO_RESULTS` is a finding, not a failure, but it is a code because it must be reported
distinctly. Job status is a separate enum: `PENDING`, `IN_PROGRESS`, `OK`, `NO_RESULTS`,
`FAILED`, `SKIPPED`. A job is `OK` when at least one listing was written, `NO_RESULTS` when
the platform positively returned a well-formed empty result, `FAILED` when it ended with any
other code after retries, `SKIPPED` when the platform was stopped before the job ran.

### 5.2 Job

One unit of work: `job_id`, `run_id`, `platform`, `requested_pincode`, `city` (pass-through),
`search_term`, `input_row_id`, `brand`, `pack_size`, `category` (the last three are match
scoring inputs and pass-throughs), `max_results`. `input_row_id` is the Excel row number in the
`products` sheet, header row being 1, so the first product is row 2.

### 5.3 EffectiveLocation

What `set_location` proved: `platform`, `requested_pincode`, `effective_pincode`, `store_id`,
`eta_minutes`, `address_text`, `evidence` (a dict of which readback sources were consulted and
what each said), `verified_at_utc`. `effective_pincode` is the pincode string the site
reported, or `None` when the platform exposes no pincode in any readback. A `None` here is a
`LOCATION_NOT_SET` failure unless the spec for that platform documents an accepted substitute,
and no spec currently does (see the Zepto question in `OPEN_QUESTIONS.md`).

### 5.4 RawCapture

One stored response: `capture_id`, `run_id`, `job_id`, `attempt_no`, `seq`, `platform`,
`strategy`, `source`, `method`, `url`, `http_status`, `content_type`, `body` (bytes),
`sha256`, `size_bytes`, `captured_at_utc`, `request` (method, url, query, POST body, and the
request header names; never cookie values or tokens).

`source` says what kind of thing the bytes are, because the four platforms hand over their
data in four different ways:

| source | meaning | used by |
|---|---|---|
| `network_response` | a response body captured with `page.on("response")`, which hooks below page JavaScript and is not affected by hydration | Zepto primary, BigBasket primary, Blinkit supplementary |
| `page_state` | a JSON serialisation of a JavaScript object read from the page, taken with `page.evaluate` | Blinkit primary (the Redux slice) |
| `ssr_document` | a full HTML document fetched with the context's cookie jar | Swiggy primary |
| `api_replay` | a response to a request V2 made itself with the context's cookie jar | Swiggy pagination, BigBasket pagination, Zepto secondary |

`raw_payload_ref` in the output workbook is the `capture_id`. Every listing row carries the id
of the capture it was parsed from.

### 5.5 ProductListing

One row of the `results` sheet before the run-level columns are added. Money in paise.

```
platform, effective_pincode, result_rank, platform_product_id (str), product_name, brand,
pack_size (as shown), unit_normalised (str | None), mrp_paise (int | None),
selling_price_paise (int | None), discount_pct (Decimal | None), price_per_unit_paise (int | None),
currency ("INR"), in_stock (bool | None), stock_qty (int | None), eta_minutes (int | None),
store_or_seller_id (str | None), category_path (str | None), product_url (str | None),
image_url (str | None), match_score (Decimal | None), capture_id, strategy
```

Availability is deliberately two fields, because the platforms disagree about what they will
tell you (the table is from the BigBasket playbook, section 1):

| | Blinkit | Swiggy Instamart | Zepto | BigBasket |
|---|---|---|---|---|
| Integer stock exposed | Yes | No | Yes | No |
| Authoritative field | `inventory` (int) | `variations[].inventory.inStock` (bool) | `availableQuantity` (int, raw XHR only) | `availability.avail_status` (`"001"`/`"000"`) |
| Field that lies | `is_sold_out` | none observed | `availableQuantity` and `outOfStock` in the DOM | `inv_info.skus[].qty` |
| `in_stock` in V2 | `inventory > 0` | `inStock` | `availableQuantity > 0` | `avail_status == "001"` |
| `stock_qty` in V2 | `inventory` | `None` | `availableQuantity` | `None` |

`stock_qty` is `None` for Swiggy and BigBasket on every row, always. It is never zero-filled and
never inferred from a low-stock string.

### 5.6 HealthReport

`platform`, `adapter_version`, `ok`, `strategy`, `checks` (list of name, ok, detail),
`location` (the `EffectiveLocation` the probe produced), `capture_ids`, `checked_at_utc`.

### 5.7 Money: why integer paise

Two acceptable options were `Decimal` and integer paise. V2 uses integer paise inside `core/`
and `platforms/`, converting to `Decimal` rupees only in `io/excel_out.py`.

Reasons: an integer cannot accumulate rounding error, cannot be accidentally divided into a
float, and is what Zepto already sends. Blinkit and BigBasket send rupee strings, which parse
exactly with `Decimal` and then multiply by 100 exactly. Swiggy sends `units` plus `nanos`,
which is `units * 100 + nanos // 10_000_000` exactly. A SQLite `INTEGER` column stores it
without loss. The Excel writer produces `Decimal(paise) / 100` and writes it as a number with a
two-decimal format; the round-trip test asserts `Decimal(str(cell_value)) == original`.

A price that arrives with a fractional paisa (a nanos value not divisible by 10,000,000) is
recorded as a data quality anomaly and the paise value is `None`, not rounded.

---

## 6. Storage schema (SQLite)

One database file per installation (`storage.path`, default `data/qcom.sqlite`), WAL mode, one
connection per worker thread. All writes for one job (its listings, its raw captures, its
status change) happen in one transaction, so a crash can never leave half a job's rows behind.

```sql
schema_version (version INTEGER)

runs (
  run_id TEXT PRIMARY KEY,
  started_at_utc TEXT, started_at_ist TEXT, ended_at_utc TEXT, ended_at_ist TEXT,
  code_version TEXT, git_sha TEXT, config_hash TEXT, config_json TEXT,
  input_path TEXT, input_sha256 TEXT, run_label TEXT,
  proxy_label TEXT,                 -- provider label only, never credentials
  adapter_versions_json TEXT,
  status TEXT,                      -- IN_PROGRESS | COMPLETED | COMPLETED_WITH_FAILURES | ABORTED
  exit_code INTEGER,
  summary_json TEXT
)

jobs (
  job_id TEXT PRIMARY KEY,          -- <run_id>:<platform>:<pincode>:<input_row_id>
  run_id TEXT NOT NULL REFERENCES runs,
  platform TEXT, requested_pincode TEXT, city TEXT, search_term TEXT, input_row_id INTEGER,
  brand TEXT, pack_size TEXT, category TEXT, max_results INTEGER,
  status TEXT NOT NULL,             -- PENDING | IN_PROGRESS | OK | NO_RESULTS | FAILED | SKIPPED
  attempts INTEGER NOT NULL DEFAULT 0,
  final_code TEXT, final_reason TEXT,
  strategy TEXT,                    -- which strategy produced the rows
  effective_pincode TEXT, store_id TEXT, eta_minutes INTEGER, location_evidence_json TEXT,
  first_started_utc TEXT, last_finished_utc TEXT, duration_ms INTEGER,
  artifact_path TEXT,               -- screenshot or html saved on failure
  results_returned INTEGER,
  UNIQUE (run_id, platform, requested_pincode, input_row_id)
)

attempts (
  attempt_id INTEGER PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs, attempt_no INTEGER,
  started_utc TEXT, finished_utc TEXT,
  outcome TEXT, error_code TEXT, error_message TEXT, traceback TEXT, artifact_path TEXT
)

raw_payloads (
  capture_id TEXT PRIMARY KEY,      -- <run_id>:<seq>
  run_id TEXT NOT NULL, job_id TEXT, attempt_no INTEGER, seq INTEGER,
  platform TEXT, strategy TEXT, source TEXT,
  method TEXT, url TEXT, http_status INTEGER, content_type TEXT,
  request_json TEXT,                -- query, body, header names; no cookies, no tokens
  sha256 TEXT, size_bytes INTEGER,
  body_zlib BLOB NOT NULL,
  captured_at_utc TEXT
)

listings (
  listing_id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs, capture_id TEXT NOT NULL REFERENCES raw_payloads,
  captured_at_utc TEXT, captured_at_ist TEXT,
  platform TEXT, requested_pincode TEXT, effective_pincode TEXT, city TEXT, search_term TEXT, input_row_id INTEGER,
  result_rank INTEGER, platform_product_id TEXT, product_name TEXT, brand TEXT, pack_size TEXT,
  unit_normalised TEXT, mrp_paise INTEGER, selling_price_paise INTEGER, discount_pct TEXT,
  price_per_unit_paise INTEGER, currency TEXT, in_stock INTEGER, stock_qty INTEGER,
  eta_minutes INTEGER, store_or_seller_id TEXT, category_path TEXT, product_url TEXT, image_url TEXT,
  match_score TEXT, strategy TEXT
)

dq_events (                         -- data quality anomalies, one row each
  id INTEGER PRIMARY KEY, run_id TEXT, job_id TEXT, listing_id INTEGER,
  kind TEXT,                        -- pack_size_unparsed | mrp_below_selling | price_moved_gt_40pct | ...
  detail TEXT
)

platform_state (
  run_id TEXT, platform TEXT, status TEXT,   -- ACTIVE | STOPPED_BLOCKED | STOPPED_CIRCUIT | STOPPED_RATE_LIMIT
  reason TEXT, consecutive_failures INTEGER, stopped_at_utc TEXT,
  PRIMARY KEY (run_id, platform)
)
```

`discount_pct` and `match_score` are stored as text decimals to avoid SQLite's float column.
Pincodes are `TEXT` everywhere. `platform_product_id` is `TEXT` everywhere, because Blinkit
ids like `298` will otherwise be turned into numbers somewhere down the line.

Resume reads `jobs` where `status IN ('PENDING', 'IN_PROGRESS')`. An `IN_PROGRESS` job is one
the previous process died inside; its listings (if any were written outside a transaction,
which the design prevents) are deleted and it is re-run with its attempt count preserved.

Run-over-run price comparison reads the previous run's `listings` for the same
`(platform, requested_pincode, platform_product_id)`.

Screenshots and HTML snapshots taken on failure go to
`runs/<run_id>/artifacts/<job_id>_attempt<N>.png|.html` and the path is stored on the attempt.
Session jars go to `sessions/<platform>_<pincode>.json`. Both directories are gitignored; the
session files contain tokens and are treated as secrets.

---

## 7. Retry design

The policy table from the requirements, unchanged, and how it is applied:

| code | retry | policy | applied to |
|---|---|---|---|
| `NETWORK_TIMEOUT` | yes | 3 attempts, exponential backoff with jitter (2s, 4s, 8s base, plus 0 to 1s) | the job |
| `RATE_LIMITED` | yes | backoff from 60s, max 2 attempts, then the platform pauses for the run | the job, then the platform |
| `BLOCKED` | no | stop the platform for this run; remaining jobs become `SKIPPED_PLATFORM_BLOCKED` | the platform |
| `PROXY_ERROR` | yes | 2 attempts, then rotate proxy if configured, else abort the run | the job, then the run |
| `LOCATION_NOT_SET` | yes | 2 attempts, each with a fresh browser context, then fail every job in that pincode group | the pincode group |
| `NO_RESULTS` | no | not an error; recorded as a finding | |
| `SCHEMA_DRIFT` | no | fail loudly, raw saved, missing path named in the reason | |
| `PARSE_ERROR` | no | fail, raw saved, traceback saved | |
| `UNKNOWN` | yes | 1 attempt, then fail; any `UNKNOWN` in a run is listed as a bug in the summary | the job |

Mechanics:

- `core.retry` builds one `tenacity.Retrying` object per code from this table. There is no
  generic "retry on any exception". An exception that does not carry an `ErrorCode` is
  classified by the adapter's `classify_failure`; if that also returns nothing, it is
  `UNKNOWN`.
- One attempt of a job is: search under the already-verified location, store raw, parse. The
  location step is retried separately at the pincode-group level, because re-running the
  location flow once per job would multiply the cost by the number of search terms.
- The circuit breaker counts consecutive `FAILED` outcomes per platform within the run
  (`NO_RESULTS` resets it, as does `OK`). At `circuit_breaker.consecutive_failures` (default 5)
  the platform stops and its remaining jobs become `SKIPPED` with reason `circuit_open`.
- `BLOCKED` is recognised by signatures in each spec (challenge page, "Request Blocked"
  interstitial, sustained 403). There is no attempt to solve a challenge, rotate identity, or
  otherwise get past a wall. The platform stops, the run reports it, and the exit status is
  non-zero.

Structure versus value, because this decides which code a bad payload gets: a **missing
structural path** the spec says is always present (for example no `tabs[0].product_info` on
BigBasket) is `SCHEMA_DRIFT` and fails the job. A **present but unparseable value** in a
single row (a price string that is not a number) leaves that field `None`, records a
`dq_events` row, and keeps the listing. The second rule stops one odd row from discarding
nineteen good ones; the first rule stops a reshaped payload from silently producing empty
columns.

---

## 8. Concurrency plan

**Sync Playwright, one thread per platform.** This is the choice the requirements asked me to
justify.

- Each selected platform gets one worker thread. Each thread creates its own `sync_playwright()`
  instance, its own browser, and its own database connection. Threads share the per-host
  throttle (behind a lock) and nothing else.
- Inside a platform, jobs run sequentially by default. `concurrency.<platform>` in
  `config.yaml` (default 1 everywhere) sets how many browser contexts that platform may run.
  With a value above 1, pincode groups are handed out across that many contexts; a pincode
  group never splits, because the location is a property of the context.
- The default therefore runs at most four browsers at once, one per platform. If the machine
  cannot hold four Chromium instances, `concurrency.max_platforms_in_parallel` caps it.

Why sync rather than async: the playbooks show that three of the four platforms hand over
their data as plain request and response after a one-time browser bootstrap, and the fourth
(Blinkit) is a scripted click-and-read. None of that benefits from an event loop. Sync code
reads top to bottom, which matters for a project whose owner will debug it from logs six
months from now, and tests are plain functions. Isolation between platforms comes from
threads, which is coarse but exactly the granularity the circuit breaker and `BLOCKED` rule
need. V1 chose async and gained nothing from it.

Why not more than one context per platform by default: every extra context is another location
bootstrap on the same site from the same egress, which is the pattern that draws bot walls.
Volume is an open question (A3); the default stays low until the answer says otherwise.

---

## 9. Location verification, per platform

Rule 4 in one table. Full detail is in each spec.

| platform | how the pincode is applied | readback used for `effective_pincode` | wrong-store signature | store id recorded |
|---|---|---|---|---|
| Blinkit | header location picker, autocomplete, no map step | header address text after reload contains the pincode; Redux `data.location` slice OPEN | none known; the Madhya Pradesh suggestion decoy is the risk | `merchant_id` per row (30872 at 700048) |
| Swiggy Instamart | area search, suggestion, **Confirm Location on the map** | `userLocation.address` in `___INITIAL_STATE___` contains the pincode AND `locationSource == "swgyUL"`; header text as a second witness | `locationSource == "seo"`, storeId `1392421`, Bengaluru address | `storeDetailsV2.storeId` (1388313 at 700048), equals `podId` on every variation |
| Zepto | address search, suggestion, sometimes a map confirm | OPEN: no documented readback carries the pincode. `serviceability` cookie gives store id and city; `user-position` localStorage `formattedAddress` may carry the pincode (unverified) | storeId `b4dc8d65-ed2e-4142-81b6-373982b13500`, coords 12.96902 / 77.75395 | `serviceability.primaryStore.storeId`, asserted equal to `storeId` on every row |
| BigBasket | header delivery selector, autocomplete, no map step seen | cookie `_bb_pin_code` equals the pincode AND header text contains it AND every row's `visibility.sa_id` is one value | `__NEXT_DATA__` holding homepage rows; `_bb_locSrc` and `_bb_addressinfo` area are NOT guards | `visibility.sa_id` (28232 at 700048), `fc_id` recorded alongside |

Every worker asserts before the first search and writes the readback into the job. Each
platform's known 700048 values are used by `health`, not by `run`, because for any other
pincode the expected store id is unknown and must not be guessed.

---

## 10. Strategy ladder, per platform

The requirements ask for a ladder: primary documented endpoint, then a secondary the playbook
names, then DOM only if the playbook describes it. Every capture, job and listing records the
strategy that produced it. There is no silent downgrade; a fallback is a logged event.

| platform | primary | secondary | DOM |
|---|---|---|---|
| Blinkit | `redux_store`: read `getState().ui.search.searchProductBffData.snippets` after the header search | none named by the playbook; the network capture of the search XHR is stored as evidence but its URL is OPEN | not in the ladder; the DOM lacks inventory |
| Swiggy Instamart | `ssr_global_search`: GET `/instamart/search?query=<term>&globalSearch=true` with the located context's cookies, parse `___INITIAL_STATE___` | `search_v2_api`: POST `/api/instamart/search/v2` for pages after the first | not in the ladder; prices concatenate in the DOM |
| Zepto | `xhr_capture`: navigate `/search?query=<term>` and capture the POST `/user-search-service/api/v3/search` body below page JS | `api_replay`: POST the same endpoint from the context; cookie and header requirements OPEN | **forbidden**: the DOM is rewritten by the `always_in_stock` experiment |
| BigBasket | `listing_svc_capture`: navigate `/ps/?q=<term>` and capture GET `/listing-svc/v2/products?type=ps&slug=<term>&page=1&bucket_id=52` | `listing_svc_replay`: GET the same endpoint from the context for pages 2 and up | not in the ladder; `__NEXT_DATA__` is a trap |

Replays are made with Playwright's `APIRequestContext` (`page.context.request`), which shares
the browser context's cookie jar including HttpOnly cookies. This is why no HTTP client
library is needed beyond the approved dependency list, and why Swiggy's HttpOnly session
cookies are not a problem.

---

## 11. Sessions, device profile and politeness

- After `set_location` verifies, the context's `storage_state()` (cookies including HttpOnly,
  plus localStorage) is saved to `sessions/<platform>_<pincode>.json`. On the next run the
  jar is loaded first and the readback assertion runs again. If it fails, the jar is deleted
  and the UI flow runs. Rule 4 holds either way; the jar only saves time. Swiggy's `tid`
  expires in roughly an hour and WAF tokens have their own lifetime, so jar reuse across runs
  is expected to fail sometimes and that is handled, not fought.
- Browser contexts use Playwright's built-in `Desktop Chrome` device profile, locale `en-IN`,
  timezone `Asia/Kolkata`. No user-agent rotation, no fingerprint tricks.
- `throttle`: per host, minimum gap 8s plus 0 to 4s jitter (V1's defaults, kept). Navigation
  and replays both go through it. An HTTP 429 or a platform throttle signal is `RATE_LIMITED`.
- Proxy: `server`, `username`, `password` from `.env` (`QCOM_PROXY_SERVER`,
  `QCOM_PROXY_USERNAME`, `QCOM_PROXY_PASSWORD`), applied at browser launch. `config.yaml`
  carries only a `proxy.label` for the summary. Rotation depends on the answer to A1.
- Captchas and challenge pages are never solved by V2. A real browser passes the AWS WAF
  challenge on its own (playbook 1, section 4.11); if it does not, that is `BLOCKED`.

---

## 12. Conventions

| thing | rule |
|---|---|
| money | integer paise in `core/` and `platforms/`; `Decimal` rupees only in `io/excel_out.py` |
| timestamps | `captured_at_utc` and `captured_at_ist`, both ISO 8601 with offset, from `zoneinfo` |
| pincodes | `str` always; validated as exactly six digits; written to Excel as text |
| unknown | `None`; never `0`, `""`, `"N/A"` |
| ids | `str`; `rating_info.sku_id` on BigBasket is a different entity and is not an id column |
| `discount_pct` | `(mrp - sp) / mrp * 100` as `Decimal` with two places, only when both present and `mrp >= sp`; else `None` plus anomaly `mrp_below_selling` when `mrp < sp` |
| `unit_normalised`, `price_per_unit` | only when the pack size parses unambiguously (section 14); else `None` plus anomaly `pack_size_unparsed` |
| `currency` | `"INR"` on every row |
| `result_rank` | 1-based position in the platform's own order after de-duplication, before anything else |

---

## 13. Excel contract

### Input (`input.xlsx`)

Validated in full, before any browser, by `io/excel_in.py`. The validator produces a list of
problems, each with sheet, cell and message, and the run aborts with exit code 2 if the list
is non-empty. Checks:

- sheets `products`, `pincodes`, `settings` exist (case-sensitive)
- `products` header row is exactly `product_name, brand, pack_size, category, active` in
  that order; `pincodes` is `pincode, city, active`; `settings` is `key, value`
- `product_name` non-blank on every active row; `pincode` non-blank, exactly six digits after
  reading as text (a numeric cell 700048 is accepted and converted; `700048.0`, `70004`,
  `7000481`, `70004A` are rejected, never coerced)
- `active` is blank, TRUE or FALSE (case-insensitive text or boolean cell); blank means TRUE
- no duplicate active `product_name` (case-insensitive, whitespace-trimmed); no duplicate
  active `pincode`
- `settings.platforms` blank or a comma-separated subset of the registry;
  `max_results_per_query` blank or a positive integer (default 20); `run_label` free text
- at least one active product, one active pincode, one selected platform

### Output (`output/<run_id>_results.xlsx`)

Four sheets, exactly as the requirements list them. `results` has the 29 columns of
requirements section 3 in that order, plus `strategy` as a 30th column (requirements section 6
demands it and section 3 omits it; see `OPEN_QUESTIONS.md` B1). `run_summary` has one row per
job. `failures` has one row per job that produced no listings, including `NO_RESULTS` jobs
marked as such. `run_meta` is key/value.

Formatting: pincode columns as text (`@` number format), price columns as numbers with
`0.00`, `captured_at_ist` as a real datetime cell, `captured_at_utc` as ISO text, header row
frozen, autofilter on every sheet.

The workbook is written by reading the database, never from in-memory state, so `resume`
produces exactly the workbook `run` would have.

---

## 14. Matching and normalisation

### match_score

A number from 0 to 1 written on every row, never used to filter. Inputs are the `products`
row (`product_name`, optional `brand`, optional `pack_size`) and the listing.

```
tokens(x)      = lower-cased, punctuation stripped, whitespace-split words
name_overlap   = |tokens(input product_name) ∩ tokens(listing name + listing brand)| / |tokens(input product_name)|
brand_match    = 1 if input brand given and equals listing brand (case-insensitive, trimmed) else 0
pack_match     = 1 if input pack_size given, both parse (section below), and quantities are equal in the same base unit, else 0

score = 0.6 * name_overlap + 0.2 * brand_match + 0.2 * pack_match
```

When the input has no `brand`, its 0.2 goes to `name_overlap`; likewise for `pack_size`. So a
name-only input scores purely on token overlap. The formula is deliberately simple and
documented so you can reason about a score in the sheet.

### Pack size parsing

Grammar accepted: `<number> <unit>` and `<count> x <number> <unit>`, with units
`g gm gms kg`, `ml l ltr litre`, `pc pcs piece pieces unit units`. Output base units are `g`,
`ml`, `pcs`; `kg` and `l` are multiplied by 1000; multipacks are multiplied out. Everything
else is unparsed: `1 pc (120 ml)`, `2 Pieces` on variable-weight produce, ranges, `combo`.
Unparsed means `unit_normalised = None`, `price_per_unit = None`, one `pack_size_unparsed`
anomaly. There is no second-guessing.

`price_per_unit` is the selling price per **1 kg, 1 L or 1 piece** in paise (converted to
rupees in Excel), chosen so the number is human-sized. This basis is a choice (A13).

---

## 15. Data quality checks

Every check writes a `dq_events` row and increments a counter in `run_summary`:

| kind | when |
|---|---|
| `pack_size_unparsed` | section 14 |
| `mrp_below_selling` | `mrp < selling_price` |
| `missing_mrp`, `missing_selling_price` | required-looking price absent on a row |
| `price_moved_gt_40pct` | selling price differs by more than 40% from the previous run for the same platform, pincode, product id |
| `state_inventory_disagree` (Blinkit) | `product_state` and `inventory` disagree |
| `store_id_mixed` | more than one store id among a job's rows where the spec says there must be one |
| `fractional_paisa` | a price that is not a whole paisa |
| `llm_flow` (BigBasket) | `is_llm_flow` or `is_llm_timeout` true on the pull |

None of these change a value. They flag.

---

## 16. Logging

`structlog`, JSON lines to `runs/<run_id>/run.jsonl` and human-readable to the console. Every
event carries `run_id`, and where applicable `job_id`, `platform`, `pincode`, `term`,
`attempt`, `strategy`, `error_code`. Fallbacks, retries, circuit breaker trips and platform
stops are events, not just log lines, so they can be counted in the summary.

---

## 17. The `health` command

`python -m qcom health [--platform p]` runs, for each platform, the probe from the playbooks
(700048, "Mango", expected city Kolkata):

1. set location and verify (the readback assertions in section 9)
2. run the primary strategy, store the capture
3. assert every path in the spec's drift watchlist exists on the envelope and on at least one
   row, and that each has the documented type
4. compare the known 700048 store id with the readback, as a warning not a failure (stores
   can legitimately change)
5. print a report per platform and exit non-zero if any check failed

It touches the network and is run by hand or on a schedule before `run`. It is the early
warning; it does not fix anything.

---

## 18. Testing plan

Written as the code is written, not after.

- **Parser tests, offline.** `tests/fixtures/<platform>/` holds trimmed real payloads: normal,
  empty, out of stock, missing MRP, corrupted. Fixtures are captured in Phases 2 and 3 from
  the live site and trimmed to what the tests need. Until a platform's fixtures exist, that
  platform's parser does not exist either.
- **Adapter contract suite.** One parametrised suite in `tests/contract/` run against every
  registered adapter with fixture-backed pages. It checks the five functions' signatures,
  that `parse` is pure, that a corrupted payload yields `PARSE_ERROR` and a reshaped one
  yields `SCHEMA_DRIFT`, that no listing has `stock_qty` set on a boolean platform, that ids
  and pincodes are strings, that money is integer, and that `set_location` refuses to return
  without a readback.
- **Excel round trip.** Write, read, assert pincode text and price precision survive.
- **End to end with the fake adapter.** Run loop, retries, resume after a simulated crash,
  circuit breaker, `BLOCKED` stopping a platform, workbook output. No network.
- **Layering test.** Reads imports, enforces section 3.
- **Live smoke**, by hand: `python -m qcom smoke --platform blinkit --pincode 700048 --term "mango"`
  prints the readback, the strategy, the row count and the first rows.

`pytest` green with the network disabled is the bar before any phase is called done.

---

## 19. Configuration

`config.yaml` (versioned, hashed into `run_meta`):

```yaml
version: 2
run:
  max_failure_rate: 0.2            # above this the run exits non-zero
  output_dir: output
browser:
  headless: true
  device: Desktop Chrome
  navigation_timeout_s: 45
proxy:
  label: null                      # provider label for the summary; credentials live in .env
throttle:
  min_gap_s: 8
  jitter_s: 4
retry:                             # the policy table; edit here, not in code
  network_timeout: {attempts: 3, backoff_base_s: 2}
  rate_limited: {attempts: 2, backoff_base_s: 60}
  proxy_error: {attempts: 2}
  location_not_set: {attempts: 2}
  unknown: {attempts: 1}
circuit_breaker:
  consecutive_failures: 5
concurrency:
  max_platforms_in_parallel: 4
  blinkit: 1
  swiggy_instamart: 1
  zepto: 1
  bigbasket: 1
storage:
  path: data/qcom.sqlite
  sessions_dir: sessions
  artifacts_dir: runs
platforms:
  blinkit: {enabled: true}
  swiggy_instamart: {enabled: true}
  zepto: {enabled: true}
  bigbasket: {enabled: true}
```

`.env.example`:

```
QCOM_PROXY_SERVER=http://proxy.example:8000
QCOM_PROXY_USERNAME=dummy
QCOM_PROXY_PASSWORD=dummy
```

Dependencies: `playwright`, `pydantic`, `typer`, `tenacity`, `structlog`, `openpyxl`, `pytest`,
plus `pyyaml` for `config.yaml` (V1 already uses it; it is the only way to read YAML without
writing a parser, and it is not on the approved list, so it is question A14). No HTTP client
library: replays use Playwright's own request context.

---

## 20. Decisions I made that you can overturn

Each of these is a judgement call. The default is stated; changing any of them in Phase 0 is
cheap.

1. Sync Playwright with one thread per platform (section 8).
2. Integer paise internally (section 5.7).
3. A pincode group is the unit of location retry, not the job (section 7).
4. Structure versus value: missing path fails the job, unparseable value nulls the field (section 7).
5. `strategy` added as the 30th `results` column (section 13, question B1).
6. `price_per_unit` per kg, per L, per piece (section 14, question A13).
7. Session jars persisted on disk and reused after re-verification (section 11, question A16).
8. Default concurrency: all platforms in parallel, one context each (section 8, question A4).
9. Exit non-zero above a 20% job failure rate or on any `BLOCKED` (section 19, question A17).
10. Swiggy: one listing row per variation, not per product (spec, question A10).
11. Zepto: `discountedSellingPrice` as `selling_price` (spec, question A12).
12. BigBasket: `desc` and `brand.name` concatenated as `product_name` is **not** done; `product_name` is `desc` and `brand` is separate (spec).
13. Blinkit `store_or_seller_id` is the row's own `merchant_id`, so voucher rows show 35940 rather than being filtered out (spec).
