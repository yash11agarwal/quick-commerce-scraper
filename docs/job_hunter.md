# job_hunter — LinkedIn job hunter & application tracker

Automates the tedious half of a job hunt: every run pulls fresh public
LinkedIn postings for your saved searches, filters out the noise, scores
what's left against your keywords, and tracks each job through your
application pipeline in SQLite. You review a short ranked list instead of
scrolling LinkedIn.

```
┌─────────────┐   guest search    ┌────────┐  filters &  ┌──────────┐
│ jobs.py hunt│ ────────────────▶ │ parser │ ──────────▶ │ SQLite   │
└─────────────┘   (no login)      └────────┘   scoring   │ data/    │
                                                         │ jobs.db  │
   list / show / status / note / stats / export ◀─────── └──────────┘
```

## Quick start

```bash
pip install -r requirements.txt        # adds requests + beautifulsoup4
# 1. Edit job_config.yaml: your searches, filters, score keywords
# 2. Hunt:
python jobs.py hunt
# 3. Review and work the pipeline:
python jobs.py list
python jobs.py show 4012345678 --fetch     # pulls the full description
python jobs.py status 4012 applied --note "referred by Priya"
python jobs.py stats
```

Run `hunt` on a schedule the same way `main.py` is scheduled, e.g.:

```cron
0 9,18 * * *  cd /path/to/quick-commerce-scraper && .venv/bin/python jobs.py hunt
```

Only *new* postings are reported each run — everything already tracked is
just refreshed (`last_seen`), and your statuses/notes are never touched.

## Commands

| Command | What it does |
|---|---|
| `hunt [--search NAME]` | Run all (or one) configured searches; store & print new jobs, best score first |
| `list [--status s]... [--all] [--company x] [--search n]` | Show the pipeline; hides `rejected`/`archived` unless asked |
| `show <id> [--fetch]` | Everything about one job; `--fetch` downloads & caches the description |
| `status <id> <status> [--note ...]` | Move a job: `new → interested → applied → interviewing → offer` (or `rejected`/`archived`) |
| `note <id> <text>` | Append a timestamped note (recruiter names, interview dates…) |
| `stats` | Funnel counts per status |
| `export [--out f.csv]` | Full CSV dump (spreadsheet-friendly) |

Job ids accept any unique prefix — `jobs.py status 4012 applied` works if
only one tracked id starts with `4012`. Every status change is recorded in
a `status_history` table, so `show` displays the full timeline.

## Configuration (`job_config.yaml`)

- **searches** — each has `keywords`, `location`, and optional LinkedIn
  filters (`posted_within: day|week|month`, `experience_levels`,
  `workplace: [remote|hybrid|onsite]`, `job_types`, `max_pages`).
- **filters** — hard drops applied before storing: `title_exclude`,
  `company_exclude`, and optional `title_include_any` allowlist.
- **score_keywords** — `keyword: points` summed over the title; only
  affects ranking so your best matches sort to the top.
- **rate_limit / retry** — politeness controls (see below).

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
  (HTTP 429/999). The client waits `rate_limit.min_delay_seconds` (+jitter)
  between every request and backs off exponentially on throttles. If hunts
  start failing with "gave up after N attempts": raise the delays, lower
  `max_pages`, and hunt less often. A scheduled run twice a day with 2–4
  pages per search is plenty and stays well under the radar.

Like the price scrapers, the guest markup is unofficial and can change;
`tests/test_job_parser.py` pins the selectors and acts as the canary.

## Layout

```
jobs.py                  CLI entry point
job_config.yaml          your searches / filters / scoring
job_hunter/
  config.py              YAML → typed config; LinkedIn filter-code mapping
  scraper.py             polite guest-endpoint HTTP client (throttle-aware)
  parser.py              HTML → JobRecord (search cards + description pages)
  filters.py             include/exclude rules + keyword scoring
  store.py               SQLite: jobs table + status_history, CSV export
  cli.py                 subcommands
tests/test_job_*.py      parser / store / filters / config tests
```
