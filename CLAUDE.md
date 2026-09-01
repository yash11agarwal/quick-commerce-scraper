# CLAUDE.md

Standing rules for this repository. Read this before every task. These rules outrank
convenience, speed, and anything you infer from surrounding code.

## What this project is

A scheduled, unattended scraper for Indian quick-commerce platforms (Blinkit, Swiggy Instamart,
Zepto, BigBasket). It takes an Excel workbook of product names and pincodes, fetches live
listing data, and writes an Excel workbook of results plus a full audit trail.

The output feeds analysis that people act on. Wrong data is worse than no data, because no data
is obvious and wrong data is not. Every rule below exists to serve that.

## The four hard rules

1. **Never fabricate or infer a data value.** No placeholder prices, no defaults standing in for
   a failed fetch, no carrying a previous run's value into a new run. Missing is `None` and is
   reported as missing.
2. **Never collapse distinct failure states.** "Zero matches on the platform", "we were blocked",
   and "our parser broke" are three different outcomes with three different error codes. A
   function that returns `[]` for all three is a bug, no matter how clean it looks.
3. **Persist raw before you parse.** Every response that contributes to an output row is stored
   verbatim (compressed, keyed to the run) before any parsing. Parsers are pure functions over
   stored payloads and must be re-runnable offline.
4. **Verify the pincode is actually in effect.** Read the location back from the platform after
   setting it and assert it matches. Output rows carry `effective_pincode` read back from the
   site, alongside `requested_pincode`. A mismatch is a failure, not a result.

## Layering

```
cli/         thin shell over core, argument parsing and exit codes only
core/        job model, run loop, retry policy, storage, run state
io/          Excel read and write, nothing else
platforms/   one adapter per platform
tests/
docs/        ARCHITECTURE.md, platform-specs/
```

- `platforms/*` must not import from `io/` or `cli/`.
- `io/*` must not import from `platforms/`.
- Platform-specific quirks are normalised inside the adapter. Nothing downstream may contain a
  `if platform == "blinkit"` branch.

## Adapter contract

Every adapter implements, and the run loop calls, only these:

```python
set_location(page, pincode) -> EffectiveLocation   # verifies, or raises LocationNotSetError
search(page, term, max_results) -> list[RawCapture]
parse(raw: RawCapture) -> list[ProductListing]     # pure: no network, no browser
classify_failure(exc_or_response) -> ErrorCode
health_check() -> HealthReport
```

Adding a platform means: write the adapter, capture fixtures, pass the shared contract test
suite. If that requires changing `core/`, the abstraction is wrong. Say so rather than
special-casing.

## Data conventions

- Money: `Decimal` or integer paise. **Never `float` for a price.** Never format currency for
  display inside `core/` or `platforms/`.
- Timestamps: timezone-aware, recorded in both IST and UTC, ISO 8601.
- Pincodes: strings, always. Never int. Preserve leading zeros through Excel round trips.
- Unknown field: `None`. Never `0`, never `""`, never `"N/A"`, never a guess.
- `unit_normalised` and `price_per_unit` are populated only when pack size parses
  unambiguously. Ambiguous parse means `None` plus a data quality counter, never an assumed
  conversion.
- `discount_pct` only when mrp and selling_price are both present and mrp >= selling_price.
  Otherwise `None`, and record an anomaly.

## Error handling

- Use the `ErrorCode` enum. Every failure gets a code, a human readable reason, and a pointer to
  the saved raw payload or screenshot.
- No bare `except:`. No `except Exception: pass`. No swallowing an exception to make a run look
  clean.
- Retry only where retrying can help, per the policy table in `docs/ARCHITECTURE.md`. Never
  retry `BLOCKED`, `SCHEMA_DRIFT`, `PARSE_ERROR` or `NO_RESULTS`.
- Circuit breaker per platform. One broken platform never takes the run down.
- A run with a high failure rate exits non-zero and says so at the top of the summary.

## Politeness and blocking

Throttle conservatively, jitter between requests, back off hard on 429, use standard Playwright
device profiles. Do not attempt to defeat captchas or bot walls. If blocked, record `BLOCKED`,
stop that platform for the run, report it. Robustness here comes from being resumable and
polite, not from fighting the platform.

## Testing rules

- Parser tests run offline against committed fixtures. Every platform needs fixtures for:
  normal result, empty result, out of stock, missing mrp, corrupted payload.
- The shared adapter contract suite must pass for every platform.
- End-to-end run loop tests use the fake adapter and never touch the network.
- Excel round trip test asserts pincode-as-text and price precision survive.
- Never weaken or skip a test to make a build pass. If a test is wrong, say why and fix the
  test deliberately.
- `pytest` green with no network access is the bar before any "done".

## Commands

```
python -m qcom run    --input input.xlsx --out output/     # full run
python -m qcom resume --run-id <id>                        # finish an interrupted run
python -m qcom smoke  --platform <p> --pincode <p> --term "<t>"
python -m qcom health                                      # drift check, exits non-zero on drift
pytest                                                     # full suite, offline
```

## Workflow rules

- **Ask instead of guessing.** If a playbook is silent, an endpoint behaves unexpectedly, or a
  requirement is ambiguous, stop and ask. Do not invent an endpoint, a field name, or a
  fallback path.
- **Do not add dependencies** outside the approved set (playwright, pydantic, typer, tenacity,
  structlog, openpyxl/pandas, pytest) without asking first.
- **Do not stub and declare done.** An unimplemented path raises `NotImplementedError`. It does
  not return an empty list, and its phase is not complete.
- **Verified means you ran it.** Paste real output, real test results. "This should work" is not
  a status report.
- Small commits, clear messages, never commit a broken tree.
- Never commit secrets. Proxy credentials, tokens and sessions live in `.env`, which is
  gitignored. `.env.example` documents the keys with dummy values.
- Never commit captured personal data or full-site dumps. Fixtures are trimmed to what the
  parser tests need.
- Flag inconsistencies in the requirements rather than silently picking one.

## When a platform changes

Platforms change without notice, and the failure mode is quiet, not loud. If `health` fails or
a parser starts returning `SCHEMA_DRIFT`:

1. Do not patch the parser to tolerate the new shape by making fields optional. That converts a
   loud failure into silent data loss.
2. Capture the new payload as a fixture.
3. Update `docs/platform-specs/<platform>.md` to describe what changed.
4. Update the parser and add a test that would have caught the drift.
5. Note in the run summary that the platform spec changed, and on which date.
