"""Command-line interface for the job hunter & tracker.

Subcommands:

    hunt      run every configured search, store & show new jobs
    list      show tracked jobs (filterable by status/company/search)
    show      full detail of one job (optionally fetch its description)
    status    move a job through the pipeline (applied, interviewing, ...)
    note      append a timestamped note to a job
    stats     pipeline funnel counts
    export    dump everything to CSV
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import JobHunterConfig, load_config
from .filters import passes_filters, score_job
from .parser import parse_job_description, parse_search_page
from .schema import JobStatus
from .scraper import FetchBlockedError, LinkedInGuestClient
from .store import JobStore

log = logging.getLogger("job_hunter")


# -- output helpers ----------------------------------------------------------

def _clip(value, width: int) -> str:
    text = str(value) if value is not None else "-"
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_jobs_table(rows) -> None:
    if not rows:
        print("(no jobs)")
        return
    fmt = "{:<11} {:>3} {:<12} {:<38} {:<24} {:<18} {:<10}"
    print(fmt.format("ID", "SC", "STATUS", "TITLE", "COMPANY", "LOCATION",
                     "POSTED"))
    for r in rows:
        print(fmt.format(
            _clip(r["job_id"], 11), r["score"],
            _clip(r["status"], 12), _clip(r["title"], 38),
            _clip(r["company"], 24), _clip(r["location"], 18),
            _clip(r["posted_date"], 10)))


# -- subcommands ---------------------------------------------------------------

def cmd_hunt(args, config: JobHunterConfig) -> None:
    only = set(args.searches or [])
    known = {s.name for s in config.searches}
    if unknown := only - known:
        raise SystemExit(f"unknown search(es): {sorted(unknown)}; "
                         f"configured: {sorted(known)}")
    client = LinkedInGuestClient(config)
    total_new = []
    with JobStore(config.sqlite_path) as store:
        for spec in config.searches:
            if only and spec.name not in only:
                continue
            log.info("=== search: %s (%r @ %s) ===",
                     spec.name, spec.keywords, spec.location)
            found = skipped = 0
            # Searches are isolated: throttling on one doesn't kill the sweep.
            try:
                for html in client.iter_search_pages(spec):
                    records = parse_search_page(html)
                    if not records:
                        break
                    found += len(records)
                    keep = []
                    for rec in records:
                        ok, reason = passes_filters(rec, config.filters)
                        if not ok:
                            skipped += 1
                            log.debug("skip %s (%s): %s",
                                      rec.job_id, rec.title, reason)
                            continue
                        rec.search_name = spec.name
                        rec.score = score_job(rec, config.score_keywords)
                        keep.append(rec)
                    total_new.extend(store.add_jobs(keep))
            except FetchBlockedError as exc:
                log.error("[%s] aborted: %s", spec.name, exc)
            log.info("[%s] %d listings seen, %d filtered out",
                     spec.name, found, skipped)

        print(f"\n{len(total_new)} new job(s) this hunt:\n")
        if total_new:
            new_ids = [r.job_id for r in total_new]
            rows = [store.get_job(i) for i in new_ids]
            rows.sort(key=lambda r: r["score"], reverse=True)
            _print_jobs_table(rows)
            print("\nNext: `python jobs.py show <id>` to inspect, "
                  "`python jobs.py status <id> applied` once you apply.")


def cmd_list(args, config: JobHunterConfig) -> None:
    statuses = None
    if args.all:
        statuses = []
    elif args.status:
        statuses = args.status
    with JobStore(config.sqlite_path) as store:
        _print_jobs_table(store.list_jobs(
            statuses=statuses, search_name=args.search,
            company=args.company, limit=args.limit))


def cmd_show(args, config: JobHunterConfig) -> None:
    with JobStore(config.sqlite_path) as store:
        job = store.get_job(args.job_id)
        description = job["description"]
        if args.fetch and not description:
            client = LinkedInGuestClient(config)
            html = client.fetch_job_detail(job["job_id"])
            description = parse_job_description(html)
            if description:
                store.set_description(job["job_id"], description)
            else:
                log.warning("could not extract a description "
                            "(posting may have been removed)")
        for key in ("job_id", "title", "company", "location", "url",
                    "posted_date", "search_name", "score", "status",
                    "first_seen", "last_seen", "status_updated_at"):
            print(f"{key:>18}: {job[key] if job[key] is not None else '-'}")
        if job["notes"]:
            print("\nNotes:")
            print(job["notes"].rstrip())
        history = store.history(args.job_id)
        if history:
            print("\nStatus history:")
            for h in history:
                note = f"  ({h['note']})" if h["note"] else ""
                print(f"  {h['changed_at']}  "
                      f"{h['old_status'] or '-'} -> {h['new_status']}{note}")
        if description:
            print(f"\nDescription:\n{description}")
        elif not args.fetch:
            print("\n(description not fetched; re-run with --fetch)")


def cmd_status(args, config: JobHunterConfig) -> None:
    with JobStore(config.sqlite_path) as store:
        job = store.set_status(args.job_id, args.new_status, note=args.note)
        print(f"{job['job_id']} \"{job['title']}\" @ {job['company']} "
              f"-> {job['status']}")


def cmd_note(args, config: JobHunterConfig) -> None:
    with JobStore(config.sqlite_path) as store:
        store.add_note(args.job_id, args.text)
        print("noted.")


def cmd_stats(args, config: JobHunterConfig) -> None:
    with JobStore(config.sqlite_path) as store:
        counts = store.stats()
        if not counts:
            print("(no jobs tracked yet — run `python jobs.py hunt`)")
            return
        width = max(len(s) for s in counts)
        for status, n in counts.items():
            print(f"{status:>{width}}: {n:>4}  {'#' * min(n, 60)}")
        print(f"{'total':>{width}}: {sum(counts.values()):>4}")


def cmd_export(args, config: JobHunterConfig) -> None:
    with JobStore(config.sqlite_path) as store:
        n = store.export_csv(args.out)
    print(f"wrote {n} job(s) to {args.out}")


# -- wiring ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobs.py", description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="job_config.yaml",
                        help="path to config YAML")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("hunt", help="run configured searches, store new jobs")
    p.add_argument("--search", action="append", dest="searches",
                   metavar="NAME", help="run only this search (repeatable)")
    p.set_defaults(fn=cmd_hunt)

    p = sub.add_parser("list", help="list tracked jobs")
    p.add_argument("--status", action="append",
                   choices=JobStatus.values(),
                   help="filter by status (repeatable); default: active only")
    p.add_argument("--all", action="store_true",
                   help="include rejected/archived")
    p.add_argument("--search", help="filter by search name")
    p.add_argument("--company", help="filter by company substring")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="show one job in full")
    p.add_argument("job_id", help="job id (or unique prefix)")
    p.add_argument("--fetch", action="store_true",
                   help="fetch & cache the job description from LinkedIn")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("status", help="update a job's pipeline status")
    p.add_argument("job_id", help="job id (or unique prefix)")
    p.add_argument("new_status", choices=JobStatus.values())
    p.add_argument("--note", help="optional note recorded with the change")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("note", help="append a note to a job")
    p.add_argument("job_id", help="job id (or unique prefix)")
    p.add_argument("text")
    p.set_defaults(fn=cmd_note)

    p = sub.add_parser("stats", help="pipeline funnel counts")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("export", help="export all tracked jobs to CSV")
    p.add_argument("--out", default="jobs_export.csv")
    p.set_defaults(fn=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        stream=sys.stderr,
    )
    config = load_config(args.config)
    try:
        args.fn(args, config)
    except KeyError as exc:
        # UnknownJobError / AmbiguousJobError read fine as one-liners.
        raise SystemExit(f"error: {exc.args[0]}") from exc
