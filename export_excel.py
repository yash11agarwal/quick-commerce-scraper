#!/usr/bin/env python3
"""Export the scraped observations database to an Excel workbook.

    python export_excel.py                          # everything -> data/export_<timestamp>.xlsx
    python export_excel.py --pincode 110001         # one pincode only
    python export_excel.py --query "amul butter 500g" --days 30
    python export_excel.py --out my_report.xlsx

The workbook has two sheets: "Latest snapshot" (the most recent observation
of each product per platform per pincode) and "All observations" (the full
time series). The dashboard's Download Excel button produces the same file
scoped to whatever filters are selected on screen.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from qc_scraper.excel_io import export_observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="data/observations.db", help="SQLite database path")
    parser.add_argument("--out", default=None,
                        help="output .xlsx path (default: data/export_<timestamp>.xlsx)")
    parser.add_argument("--pincode", default=None, help="only this pincode")
    parser.add_argument("--query", default=None, help="only this search term")
    parser.add_argument("--days", type=int, default=None, help="only the last N days")
    args = parser.parse_args()

    if not Path(args.db).exists():
        raise SystemExit(
            f"{args.db} not found — run 'python main.py' first to collect data")

    out = args.out or f"data/export_{datetime.now():%Y%m%d_%H%M}.xlsx"
    count = export_observations(
        args.db, out, pincode=args.pincode, query=args.query, days=args.days)
    if count == 0:
        print(f"wrote {out} (no observations matched the filters — sheets are empty)")
    else:
        print(f"wrote {out} ({count} observations)")


if __name__ == "__main__":
    main()
