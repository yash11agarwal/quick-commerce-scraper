#!/usr/bin/env python3
"""LinkedIn job hunter & tracker CLI.

Daily driver commands:

    python jobs.py hunt                   # pull new postings for all searches
    python jobs.py list                   # review the active pipeline
    python jobs.py show 4012345678 --fetch
    python jobs.py status 4012 applied --note "via referral from Priya"
    python jobs.py stats

Schedule the hunt like the price scraper, e.g. twice a day via cron:

    0 9,18 * * *  cd /path/to/quick-commerce-scraper && .venv/bin/python jobs.py hunt

Configuration lives in job_config.yaml; docs in docs/job_hunter.md.
"""

from job_hunter.cli import main

if __name__ == "__main__":
    main()
