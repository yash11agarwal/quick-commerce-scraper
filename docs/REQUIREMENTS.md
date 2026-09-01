# Kickoff prompt: quick-commerce price and availability scraper

Paste everything below the line into Claude Code in an empty repo, with the playbook
files attached in the same message. Answer its Phase 0 questions before letting it write code.

---

## 0. Role and objective

You are building a production-grade, greenfield scraper. Not a script, not a notebook, not a
demo. It runs unattended, on a schedule, and I have to be able to trust its output without
manually spot-checking every row.

**Objective:** given an Excel workbook of product names and pincodes, fetch live listing data
from Indian quick-commerce platforms and write a clean Excel workbook of results, with every
raw response persisted so any number in the output can be traced back to the bytes it came from.

**Platforms:** exactly those covered in the attached playbooks (Blinkit, Swiggy Instamart,
Zepto, BigBasket). Do not add a platform that has no playbook. Do not skip one that does.

I do not have a computer science background. Explain your architectural choices in plain
language when you present them, and do not leave anything important implicit.

## 1. How to use the attached playbooks

The playbooks are the source of truth for platform mechanics: endpoints, request and response
shapes, how location or pincode is applied, headers, auth or token behaviour, pagination,
and known quirks.

Before writing any scraper code:

1. Read every playbook end to end.
2. For each platform, write `docs/platform-specs/<platform>.md` distilling the playbook into a
   checkable spec: the exact request(s) to make, the exact JSON path to every field I need,
   how the pincode is set and how you verify it took effect, what a blocked response looks like,
   what an empty result looks like, and what is known to change frequently.
3. In the same file, add an `## Open questions` section listing anything the playbook does not
   answer. Bring those to me in Phase 0.

Rules of precedence:

- Playbook beats your prior knowledge. If the playbook contradicts what you think you know
  about a platform, follow the playbook and note the discrepancy.
- If the playbook is silent on something material, **ask me**. Do not invent an endpoint,
  guess a field name, or fall back to DOM scraping without telling me.
- If a playbook step turns out to be stale when you test it live, stop, tell me exactly which
  step failed and what you observed, and propose a fix. Do not quietly route around it.

## 2. Non-negotiable reliability contract

These are the reasons this project exists. A build that violates any of these is not done,
regardless of how much code exists.

1. **Repeatable, not one-off.** The same command, same inputs, same day, must produce
   materially the same output. A run that works once and breaks tomorrow is a failure.
2. **No fabricated data, ever.** If extraction fails, the row is recorded as a failure with a
   reason code. Never emit a plausible-looking price, never carry forward a stale value into a
   new run as if it were fresh, never let a parser return an empty list that silently reads as
   "product not available".
3. **Distinguish the three empties.** "Platform returned zero matches", "we were blocked",
   and "our parser broke" must be three different, separately reported states. Collapsing them
   is the single most dangerous bug in this project.
4. **Raw before parsed.** Every network response used to produce a row is persisted verbatim
   (compressed) to local storage before parsing, keyed to the run. Parsing is a pure function
   over stored raw payloads, so I can re-parse historical data without re-scraping.
5. **Pincode verified, not assumed.** Setting a location can silently fail and the site will
   happily serve results for a default location. After setting a pincode you must read back the
   location the site actually has in effect and assert it matches. If it does not, that
   platform-pincode combination is a failure, not a result. Every output row carries the
   pincode actually in effect, read back from the platform, not the one I requested.
6. **Every failure is typed and retried according to policy** (Section 6). No bare
   `except: pass`. No infinite retries. No retry on an error class where retrying cannot help.
7. **Resumable.** If a run dies at 60 percent, re-running it must skip completed work and
   finish the rest, without duplicating rows.
8. **Fails loud, not silent.** A run that completes with a high failure rate must exit non-zero
   and say so at the top of the summary, not bury it.

## 3. Input and output contract

This is the interface I actually use. Get it exactly right; everything else is negotiable.

### Input workbook (`input.xlsx`)

Sheet `products`
| column | required | notes |
|---|---|---|
| product_name | yes | search term as I would type it |
| brand | no | used for match scoring, not for search |
| pack_size | no | e.g. "500 g", used for match scoring |
| category | no | free text, passed through to output |
| active | no | TRUE/FALSE, default TRUE, lets me park rows without deleting them |

Sheet `pincodes`
| column | required | notes |
|---|---|---|
| pincode | yes | 6 digit, read as text, leading zeros preserved |
| city | no | passed through to output |
| active | no | TRUE/FALSE, default TRUE |

Sheet `settings` (key/value)
- `platforms`: comma separated subset, blank means all configured platforms
- `max_results_per_query`: integer, default 20
- `run_label`: free text tag written into output

Behaviour: the job list is the cross product of active products, active pincodes, and selected
platforms. Validate the workbook **before** launching a browser: wrong headers, blank required
cells, malformed pincodes, and duplicate rows all abort with a clear message naming the sheet,
cell and problem. Never silently coerce a bad pincode.

### Output workbook (`output/<run_id>_results.xlsx`)

Sheet `results`, one row per product listing returned:

`run_id`, `captured_at_ist`, `captured_at_utc`, `platform`, `requested_pincode`,
`effective_pincode`, `city`, `search_term`, `input_row_id`, `result_rank`, `platform_product_id`,
`product_name`, `brand`, `pack_size`, `unit_normalised`, `mrp`, `selling_price`, `discount_pct`,
`price_per_unit`, `currency`, `in_stock`, `stock_qty`, `eta_minutes`, `store_or_seller_id`,
`category_path`, `product_url`, `image_url`, `match_score`, `raw_payload_ref`

Sheet `run_summary`, one row per platform x pincode x search term: status, results returned,
attempts, duration, final error code, error message.

Sheet `failures`, every job that did not produce results: full job identity, error code, human
readable reason, attempt count, timestamp of last attempt, path to the saved raw payload or
screenshot for debugging.

Sheet `run_meta`: run_id, start and end time, code version or git SHA, config hash, input file
hash, platform adapter versions, proxy in use (provider label, never credentials), counts by
status, overall exit status.

Formatting rules: pincodes as text, prices as numbers not strings, `captured_at_ist` as a real
datetime, freeze the header row, autofilter on. Currency values must round-trip exactly.

## 4. Architecture requirements

- Python 3.11+, Playwright (sync or async, your call, justify it), SQLite for raw payloads and
  run state, openpyxl or pandas for Excel IO.
- Preferred libraries: `pydantic` for schema validation, `typer` for the CLI, `tenacity` for
  retry policy, `structlog` or stdlib logging with JSON output, `pytest` for tests.
  **Ask before adding any dependency outside this list.**
- Layering, strictly enforced: `platforms/` adapters know about one platform each and nothing
  about Excel or the database; `core/` owns the job model, the run loop, retry policy and
  storage; `io/` owns Excel reading and writing; `cli/` is a thin shell over `core/`.
  An adapter must never import from `io/` or `cli/`.
- Money: use `Decimal` or integer paise internally. Never `float` for a price. State which you
  chose and why.
- All timestamps recorded in both IST and UTC, ISO 8601, timezone aware.
- Config in a versioned `config.yaml`; secrets only in `.env`, never committed, `.env.example`
  checked in. Proxy credentials and any tokens are secrets.
- Concurrency: bounded and configurable, per platform, defaulting low. Add jitter between
  requests. Rate limit per platform per host.
- Respect the platform: throttle conservatively, back off hard on 429, use Playwright's
  standard device profiles rather than anything exotic, and do not attempt to defeat captchas.
  If a platform blocks us, record `BLOCKED`, stop that platform for the run, and report it.
  Reliability comes from being polite and resumable, not from fighting the site.

### Adapter contract

Every platform adapter implements the same interface, and the run loop knows nothing else
about it:

- `set_location(page, pincode) -> EffectiveLocation` (must verify and return what the site
  actually has in effect, or raise `LocationNotSetError`)
- `search(page, term, max_results) -> list[RawCapture]` (returns captured raw payloads)
- `parse(raw: RawCapture) -> list[ProductListing]` (pure, no network, no browser)
- `classify_failure(exc_or_response) -> ErrorCode`
- `health_check() -> HealthReport` (see Section 7)

`ProductListing` is one pydantic model shared by all platforms. Per-platform field quirks are
normalised inside the adapter, not downstream. If a platform genuinely cannot supply a field,
it is `None`, never `0`, never `""`, never a guess.

## 5. Matching, normalisation and data quality

- Do not assume result 1 is the product I asked for. Compute a `match_score` (brand, pack size,
  token overlap) and write it out. Return the top N by platform rank, do not filter for me,
  but give me the score so I can filter.
- Normalise pack size into `unit_normalised` (grams, ml, pieces) and derive `price_per_unit`,
  but only when the pack size parses unambiguously. When it does not, leave both `None` and
  count it in a data quality tally in `run_summary`. Never guess a conversion.
- `discount_pct` is derived from mrp and selling_price only when both are present and
  mrp >= selling_price. Otherwise `None`, and flag mrp < selling_price as a data quality
  anomaly rather than emitting a negative discount.
- Flag, do not fix: if a price moves more than 40 percent from the previous run for the same
  platform, pincode and platform_product_id, write the row and raise a data quality warning in
  the summary.

## 6. Failure taxonomy and retry policy

Define this as an enum in code and use it everywhere, including the Excel output.

| code | meaning | retry | policy |
|---|---|---|---|
| `NETWORK_TIMEOUT` | request or navigation timed out | yes | 3 attempts, exponential backoff with jitter |
| `RATE_LIMITED` | 429 or platform throttle signal | yes | backoff starting at 60s, max 2 attempts, then pause the platform |
| `BLOCKED` | bot wall, captcha, sustained 403 | no | stop the platform for this run, mark remaining jobs `SKIPPED_PLATFORM_BLOCKED` |
| `PROXY_ERROR` | proxy refused, auth failed, tunnel died | yes | 2 attempts, then rotate proxy if configured, else abort run |
| `LOCATION_NOT_SET` | pincode not applied or read-back mismatch | yes | 2 attempts with a fresh browser context, then fail the job |
| `NO_RESULTS` | platform returned a valid empty result | no | not an error, record it as a real finding |
| `SCHEMA_DRIFT` | response parsed but expected fields missing | no | fail loudly, save the raw payload, name the missing JSON path |
| `PARSE_ERROR` | unexpected exception during parse | no | fail, save raw payload and traceback |
| `UNKNOWN` | anything unclassified | yes | 1 attempt, then fail; treat any `UNKNOWN` in a run as a bug to fix |

Fallback ladder per platform, in order, each step logged so I can see which one produced the
data: primary documented endpoint from the playbook, then secondary endpoint or intercepted
network capture if the playbook names one, then rendered DOM extraction as a last resort and
only if the playbook describes it. Never silently downgrade: the output row must record which
strategy produced it.

Circuit breaker: if a platform fails N consecutive jobs (configurable, default 5), stop that
platform for the run and continue with the others. One broken platform must never take the run
down.

## 7. Testing and verification

Non-negotiable, and written as you go, not bolted on at the end.

- **Offline parser tests.** Save real captured payloads as fixtures under `tests/fixtures/<platform>/`.
  Parser tests run with zero network and must cover: a normal result, an empty result, an
  out-of-stock item, a missing-mrp item, and a deliberately corrupted payload.
- **Adapter contract suite.** One parametrised test suite every adapter must pass, so adding a
  platform means making the same tests green.
- **Excel round trip test.** Write a workbook, read it back, assert types survive, especially
  pincode-as-text and price precision.
- **End-to-end test with a fake adapter.** Proves the run loop, retries, resume and Excel
  output work without touching the internet.
- **Live smoke test**, run manually: `python -m qcom smoke --platform blinkit --pincode 700048
  --term "amul butter"` prints what it got and which strategy produced it.
- **`health` command.** For each platform, run one known-good query and report whether the
  documented endpoint still responds with the documented shape. This is my early warning that a
  platform changed and the scraper is about to start lying to me. It must be runnable
  standalone and exit non-zero on drift.

Definition of "verified" for any phase: you ran it, against the real platform, and pasted the
actual output. Not "this should work". If you cannot get a live run to work, say so plainly
and stop.

## 8. Build order, with mandatory stop points

Do not run ahead. Stop at each checkpoint and wait for me.

**Phase 0 (no code).** Read the playbooks. Produce `docs/ARCHITECTURE.md` covering module
layout, data model, storage schema, retry design, and concurrency plan, plus the per-platform
specs from Section 1 and a consolidated list of open questions for me. **Stop.**

**Phase 1.** Scaffolding: repo structure, config, SQLite schema, pydantic models, Excel reader
and writer, CLI, run loop, retry engine, resume logic, logging. Wire it end to end with a fake
adapter that returns fixture data. Full test suite green. **Stop and show me a real output
workbook produced from the fake adapter.**

**Phase 2.** Blinkit adapter only, live. Location set and verified, search, parse, normalise.
Fixtures captured, contract tests green, live smoke test output pasted. **Stop.**

**Phase 3.** Remaining platforms one at a time, each one green on the contract suite before you
start the next. **Stop after each.**

**Phase 4.** Hardening: proxy support and rotation, circuit breaker, `health` command, data
quality checks, run-over-run price comparison, structured logs.

**Phase 5.** Documentation: `README.md` with setup, the exact commands, how to add a platform,
how to debug a failed run, how to interpret every error code. Then a full clean run over a
small real input workbook, with the summary pasted.

## 9. Before you start, ask me about

Do not assume any of these. Ask in Phase 0, in one batch:

1. Residential proxy provider, whether rotation is per-request or per-session, and how
   credentials are supplied.
2. Whether any platform needs a logged-in session, and if so how credentials or a saved session
   are supplied.
3. Expected volume: roughly how many products x pincodes x platforms per run, and how often.
4. Headless or headed by default, and what machine this runs on.
5. Whether run-over-run price history matters enough to keep every run in SQLite indefinitely.
6. Anything the playbooks left ambiguous.

## 10. Working rules while you build

- Small, working commits with clear messages. Never commit a broken tree.
- Run the tests before you tell me something is done. Paste the result.
- Do not write TODO stubs and mark a phase complete. An unimplemented path raises
  `NotImplementedError`, it does not return an empty list.
- No `# type: ignore`, no bare excepts, no swallowing exceptions to make a run "pass".
- If you find yourself about to guess, stop and ask instead. A question costs me two minutes.
  A wrong guess buried in a parser costs me a month of bad data before I notice.
- Flag inconsistencies you spot in my requirements rather than picking one silently.

## 11. Definition of done

- These commands exist and work:
  `python -m qcom run --input input.xlsx --out output/`,
  `python -m qcom resume --run-id <id>`,
  `python -m qcom smoke --platform <p> --pincode <p> --term "<t>"`,
  `python -m qcom health`.
- `run` produces the output workbook described in Section 3.
- A fresh clone plus the documented setup steps reproduces a working run on a clean machine.
- All tests pass offline, with no network.
- `health` passes for every platform.
- Killing a run midway and re-running it completes the remaining work without duplicates.
- Every number in the output workbook is traceable to a stored raw payload.
- `README.md` is good enough that I can debug a failed run six months from now without you.
