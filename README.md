# qcom: quick-commerce price and availability scraper (V2)

Takes an Excel workbook of product names and pincodes, fetches live listings from Blinkit,
Swiggy Instamart, Zepto and BigBasket at each pincode, and writes an Excel workbook of results
with every raw response stored so any number can be traced back to the bytes it came from.

Read `CLAUDE.md` first: it holds the rules this code is built to. `docs/ARCHITECTURE.md`
explains the design in plain language. `docs/platform-specs/` says exactly what each platform
adapter does and what is still unverified.

## Status

| phase | what | state |
|---|---|---|
| 0 | architecture, platform specs, open questions | done |
| 1 | scaffolding, run loop, retry, resume, Excel in and out, fake adapter, tests | done |
| 2 | Blinkit adapter, live | adapter written, offline tests green; **live smoke not yet run** (see below) |
| 3 | Swiggy Instamart, Zepto, BigBasket adapters, live | not started |
| 4 | proxy rotation, health, data quality, reparse | partly: health and data quality exist, wired to the fake |
| 5 | documentation, clean run | not started |

Blinkit is the only real adapter so far. A blank `platforms` setting means Blinkit. The
built-in fake adapter (`--platforms fake`) returns fixture data and never touches the network;
it is what the end-to-end tests use.

### Phase 2 is not done until a live run is pasted

The Blinkit adapter was built and tested in an environment that could not reach blinkit.com
(Cloudflare answered 403 to a non-browser client, and the environment's egress gateway would
not complete Chromium's TLS handshake). Its tests drive a real Chromium against a local
stand-in of the site, and its parser fixtures are synthesised from the playbook's data table
(`tests/fixtures/blinkit/README.md`). What remains is one live run on an ordinary machine:

```bash
python -m qcom smoke --platform blinkit --pincode 700048 --term "Mango" --city Kolkata --save-captures captures/blinkit
python -m qcom health --platform blinkit
```

Paste both outputs back. `captures/blinkit/` then holds the raw bytes needed to replace the
synthesised fixtures with real ones and to answer the open questions in
`docs/platform-specs/blinkit.md`. Add `--headed` to watch it.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .
playwright install chromium
cp .env.example .env                                   # optional; only needed for a proxy
```

Python 3.11 or newer.

## Commands

```bash
python -m qcom template --out input.xlsx               # blank input workbook
python -m qcom run --input input.xlsx --out output/    # full run
python -m qcom resume --run-id <id>                    # finish an interrupted run
python -m qcom smoke --platform blinkit --pincode 700048 --term "Mango" --city Kolkata
python -m qcom smoke --platform blinkit --pincode 700048 --term "Mango" --save-captures captures/blinkit   # also writes every raw body
python -m qcom health --platform blinkit               # drift check; exits non-zero on drift
pytest                                                 # full suite, offline
```

`run` prints a summary with failures at the top and exits 0 on success, 1 when the failure
rate crossed the threshold or a platform was blocked, 2 on an invalid workbook or config, 3
when the run had to abort.

## Input workbook

Sheet 1 (`products`): one search term per row in `product_name`. Optional `brand`,
`pack_size`, `category`, `active`.
Sheet 2 (`pincodes`): one six-digit pincode per row in `pincode`, kept as text. Optional
`city`, `state`, `active`.
Sheet `settings` (optional): `platforms` (comma-separated, blank means all), `max_results_per_query`
(default 20), `run_label`.

The workbook is validated completely before any browser starts. Every problem is reported
with its sheet and cell.

## Output workbook

`output/<run_id>_results.xlsx` with four sheets:

- `results`: one row per listing, 31 columns (`docs/ARCHITECTURE.md` section 13). Prices are
  numbers in rupees, pincodes are text, `captured_at_ist` is a real datetime.
- `run_summary`: one row per platform x pincode x search term with status, attempts, duration,
  strategy and final error code.
- `failures`: every job that produced no rows, with its error code, reason, attempt count and
  a pointer to the stored raw payload or screenshot.
- `run_meta`: run id, times, code version, git SHA, config hash, input hash, adapter versions,
  proxy label, counts by status and code, data quality counters, platform stops.

The workbook is generated from the SQLite database (`data/qcom.sqlite`), never from memory,
so `resume` produces exactly what `run` would have.

## Error codes

| code | meaning | retried |
|---|---|---|
| `NETWORK_TIMEOUT` | request or navigation timed out | 3 attempts |
| `RATE_LIMITED` | 429 or a throttle signal | 2 attempts, then the platform pauses for the run |
| `BLOCKED` | bot wall, captcha, sustained 403 | no; the platform stops, remaining jobs are `SKIPPED_PLATFORM_BLOCKED` |
| `PROXY_ERROR` | proxy refused or died | 2 attempts, then the next fallback proxy, else the run aborts |
| `LOCATION_NOT_SET` | pincode not applied or the readback did not match | 2 attempts with a fresh browser context, then every job at that pincode fails |
| `NO_RESULTS` | the platform positively returned an empty, well-formed result | not an error |
| `SCHEMA_DRIFT` | the response is missing a path the spec says is always there | no; the path is named in the reason |
| `PARSE_ERROR` | the parser crashed | no; raw payload and traceback saved |
| `UNKNOWN` | anything unclassified | 1 attempt; every `UNKNOWN` is a bug to classify |

Five consecutive failed jobs on one platform trip its circuit breaker; the rest are skipped
and the other platforms carry on.

## Debugging a failed run

1. Read the summary printed at the end, or `run_meta` in the workbook. Platform stops and the
   failure rate are at the top.
2. Open the `failures` sheet. Each row has the error code, the reason, and either capture ids
   (`captures=<run_id>:000123`) or a screenshot path under `runs/<run_id>/artifacts/`.
3. The raw payload for a capture id is in the `raw_payloads` table of `data/qcom.sqlite`,
   compressed with zlib and checksummed. `Storage.capture_body(capture_id)` returns the bytes.
4. `runs/<run_id>/run.jsonl` has every event with `job_id`, `attempt`, `code` and `strategy`.
5. `python -m qcom health --platform <p>` tells you whether the platform still looks the way the
   spec says it does.

## Configuration

`config.yaml` holds throttle, retry policy, circuit breaker, concurrency and storage paths, and
is hashed into `run_meta`. `browser.executable_path` points at a specific Chromium when
Playwright's own download is not wanted; the browser-driven tests read `QCOM_CHROMIUM_PATH`
for the same purpose and skip when no Chromium is installed. Secrets (proxy server and credentials) live in `.env`, which is
gitignored; `.env.example` documents the keys. Session jars under `sessions/` contain tokens and
are gitignored too.

## Adding a platform

Write `qcom/platforms/<name>/adapter.py` implementing the five functions in
`qcom/platforms/base.py`, capture trimmed fixtures under `tests/fixtures/<name>/`, register the
class in `qcom/platforms/registry.py`, and make `tests/contract/` green. If that needs a change
in `qcom/core/`, the abstraction is wrong; say so rather than special-casing.
