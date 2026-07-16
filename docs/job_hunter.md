# job_hunter — LinkedIn job hunter & application tracker

Automates the tedious half of a job hunt: every run pulls fresh public
LinkedIn postings for your saved searches, filters out the noise, scores
what's left against your keywords, and tracks each job through your
application pipeline. **Everything goes in and out through one Excel
workbook** — `job_tracker.xlsx` — so the daily workflow is: run a command,
open Excel, work your pipeline there.

```
        job_tracker.xlsx                                    SQLite
┌─────────────────────────────┐                        ┌─────────────┐
│ INPUT   Searches            │──── jobs.py hunt ─────▶│ data/jobs.db│
│         Filters / Scoring   │   (guest search, no    │ statuses,   │
│         Settings            │    login, throttled)   │ notes,      │
│                             │                        │ history     │
│ OUTPUT  Jobs  ◀─────────────│◀── sheets rebuilt ─────│             │
│         (Status dropdown +  │                        │             │
│          Add Note editable) │──── edits absorbed ───▶│             │
│         Stats               │    on every hunt/sync  └─────────────┘
└─────────────────────────────┘
```

## Quick start

```bash
pip install -r requirements.txt
python jobs.py init        # creates job_tracker.xlsx
# open it in Excel, fill in the Searches / Filters / Scoring sheets
python jobs.py hunt        # pulls postings into the Jobs sheet
```

Then live in Excel:

- **Jobs sheet** — one row per posting, best matches on top, rows from the
  latest hunt highlighted yellow. Two columns are editable:
  - **Status**: a dropdown (`new / interested / applied / interviewing /
    offer / rejected / archived`)
  - **Add Note**: type anything ("recruiter: Priya", "phone screen Fri");
    it's stored as a timestamped note on the next run and the cell clears
- **Stats sheet** — funnel counts per status and per search.

Any `hunt` or `sync` first absorbs your Excel edits into SQLite (statuses
recorded with full history), then rebuilds the output sheets. **Save and
close the workbook before running** — Excel locks the file.

Run `hunt` on a schedule the same way `main.py` is scheduled:

```cron
0 9,18 * * *  cd /path/to/quick-commerce-scraper && .venv/bin/python jobs.py hunt
```

Only *new* postings are added each run — everything already tracked just
gets its `last_seen` refreshed; your statuses and notes are never touched.

## Workbook sheets

| Sheet | Direction | What goes in it |
|---|---|---|
| `ReadMe` | — | cheat sheet with allowed values |
| `Searches` | input | one row per saved search: keywords, location, posted-within (`day/week/month`), experience levels, workplace (`remote/hybrid/onsite`), job types, max pages |
| `Filters` | input | columns of drop rules: title-must-contain-any, title-exclude, company-exclude |
| `Scoring` | input | `keyword → points`, summed over the title; only affects ranking |
| `Settings` | input | request delays, retries, database path |
| `Jobs` | output + edits | the pipeline; only `Status` and `Add Note` are read back — everything else is rebuilt from the database |
| `Stats` | output | funnel counts |

Multi-value cells (experience levels, workplace, job types) take
comma-separated values, e.g. `entry, associate`. Input sheets are never
rewritten by the tool; the two output sheets are regenerated every run, so
don't reorder their columns or park your own data there.

## Commands

| Command | What it does |
|---|---|
| `init [--force]` | Create a fresh `job_tracker.xlsx` (also happens automatically on first run) |
| `hunt [--search NAME]` | Absorb Excel edits → run all (or one) searches → rebuild sheets |
| `sync` | Absorb Excel edits & rebuild sheets without hunting |
| `list / show / status / note / stats` | Terminal shortcuts for the same pipeline (`show <id> --fetch` pulls the full job description) |
| `export [--out f.xlsx\|f.csv]` | Standalone snapshot workbook (or CSV) |

Job ids accept any unique prefix — `jobs.py status 4012 applied` works if
only one tracked id starts with `4012`. Every status change (from Excel or
the CLI) is recorded in a `status_history` table; `show` prints the
timeline.

## How it fetches (and what it deliberately doesn't do)

Data comes from LinkedIn's **guest** job-search endpoints
(`linkedin.com/jobs-guest/jobs/api/...`) — the same public listings anyone
sees without an account. No login, no cookies, no credentials anywhere.

Deliberately **not** included:

- **No logged-in automation / auto-apply.** Automating a LinkedIn account
  (Easy Apply bots, connection blasts) violates LinkedIn's User Agreement
  and regularly gets accounts restricted — the last thing you need
  mid-job-hunt. This tool finds and tracks; *you* apply.
- **No aggressive crawling.** Anonymous traffic is throttled hard
  (HTTP 429/999). The client waits `min_delay_seconds` (+jitter) between
  requests and backs off exponentially on throttles. If hunts fail with
  "gave up after N attempts": raise the delays in `Settings`, lower
  `Max Pages`, and hunt less often. Twice a day with 2–4 pages per search
  is plenty.

Like the price scrapers, the guest markup is unofficial and can change;
`tests/test_job_parser.py` pins the selectors and acts as the canary.

## Layout

```
jobs.py                  CLI entry point
job_tracker.xlsx         YOUR workbook (gitignored; `jobs.py init` creates it)
job_hunter/
  workbook.py            Excel in/out: template, config load, edit absorption,
                         Jobs/Stats sheet rebuild, snapshot export
  config.py              typed config + LinkedIn filter-code mapping
  scraper.py             polite guest-endpoint HTTP client (throttle-aware)
  parser.py              HTML → JobRecord (search cards + description pages)
  filters.py             include/exclude rules + keyword scoring
  store.py               SQLite: jobs table + status_history
  cli.py                 subcommands
tests/test_job_*.py      workbook / parser / store / filters / config tests
```
