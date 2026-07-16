#!/usr/bin/env python3
"""LinkedIn job hunter & tracker — everything in/out via job_tracker.xlsx.

First run:

    python jobs.py init          # creates job_tracker.xlsx
    # open it, fill in the Searches / Filters / Scoring sheets, then:
    python jobs.py hunt          # pull new postings into the Jobs sheet

Daily loop: work the pipeline directly in Excel (Status dropdown +
Add Note column on the Jobs sheet), then any `hunt` or `sync` absorbs
your edits into SQLite and rebuilds the sheets. Terminal shortcuts:

    python jobs.py show 4012345678 --fetch
    python jobs.py status 4012 applied --note "via referral from Priya"
    python jobs.py stats

Schedule the hunt like the price scraper, e.g. twice a day via cron:

    0 9,18 * * *  cd /path/to/quick-commerce-scraper && .venv/bin/python jobs.py hunt

Docs: docs/job_hunter.md.
"""

from job_hunter.cli import main

if __name__ == "__main__":
    main()
