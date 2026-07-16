"""Command-line interface for the job hunter & tracker.

The Excel workbook (job_tracker.xlsx) is the primary interface: input
sheets configure the hunt, the Jobs sheet is the pipeline you review and
edit in Excel, and every hunt/sync absorbs those edits then rebuilds the
output sheets. The terminal subcommands remain for quick actions.

Subcommands:

    init      create a fresh job_tracker.xlsx template
    hunt      absorb Excel edits, run every search, refresh the workbook
    sync      absorb Excel edits & refresh the workbook (no hunting)
    list      show tracked jobs in the terminal
    show      full detail of one job (optionally fetch its description)
    status    move a job through the pipeline (applied, interviewing, ...)
    note      append a timestamped note to a job
    stats     pipeline funnel counts
    export    standalone .xlsx (or .csv) snapshot of the pipeline
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import workbook
from .config import JobHunterConfig, load_config
from .filters import passes_filters, score_job
from .parser import parse_job_description, parse_search_page
from .schema import JobStatus
from .scraper import FetchBlockedError, LinkedInGuestClient
from .store import JobStore

log = logging.getLogger("job_hunter")


# -- workbook plumbing --------------------------------------------------------

def _is_workbook(path: str) -> bool:
    return path.lower().endswith((".xlsx", ".xlsm"))


def _load_any_config(path: str) -> JobHunterConfig:
    if _is_workbook(path):
        return workbook.load_config(path)
    return load_config(path)  # legacy YAML configs keep working


def _absorb_excel_edits(args, store: JobStore) -> None:
    """Pull Status/Add Note edits out of the Jobs sheet into the store."""
    if not _is_workbook(args.config):
        return
    edits = workbook.read_job_edits(args.config)
    changed, noted, warnings = workbook.apply_edits(store, edits)
    for warning in warnings:
        log.warning(warning)
    if changed or noted:
        log.info("absorbed Excel edits: %d status change(s), %d note(s)",
                 changed, noted)


def _refresh_workbook(args, store: JobStore, new_ids=()) -> None:
    """Rebuild Jobs+Stats sheets; never let a locked file kill the run."""
    if not _is_workbook(args.config):
        return
    try:
        workbook.write_tracker_sheets(args.config, store, new_ids)
        log.info("workbook refreshed: %s", args.config)
    except PermissionError:
        log.error("could not write %s — close it in Excel and run "
                  "`python jobs.py sync` to refresh it", args.config)


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

def cmd_init(args) -> None:
    path = Path(args.config)
    if path.exists() and not args.force:
        raise SystemExit(f"{path} already exists; use --force to overwrite "
                         "(your Jobs data lives in SQLite and survives, but "
                         "customized input sheets would be reset)")
    workbook.create_template(path)
    print(f"created {path} — open it, customize the Searches / Filters / "
          f"Scoring sheets, then run: python jobs.py hunt")


def cmd_hunt(args, config: JobHunterConfig) -> None:
    only = set(args.searches or [])
    known = {s.name for s in config.searches}
    if unknown := only - known:
        raise SystemExit(f"unknown search(es): {sorted(unknown)}; "
                         f"configured: {sorted(known)}")
    client = LinkedInGuestClient(config)
    total_new = []
    with JobStore(config.sqlite_path) as store:
        _absorb_excel_edits(args, store)
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

        new_ids = [r.job_id for r in total_new]
        _refresh_workbook(args, store, new_ids)

        print(f"\n{len(total_new)} new job(s) this hunt:\n")
        if total_new:
            rows = [store.get_job(i) for i in new_ids]
            rows.sort(key=lambda r: r["score"], reverse=True)
            _print_jobs_table(rows)
            if _is_workbook(args.config):
                print(f"\nOpen {args.config} — new rows are highlighted; "
                      "work the Status / Add Note columns and they sync on "
                      "the next hunt.")


def cmd_sync(args, config: JobHunterConfig) -> None:
    if not _is_workbook(args.config):
        raise SystemExit("sync only makes sense with an .xlsx config")
    with JobStore(config.sqlite_path) as store:
        _absorb_excel_edits(args, store)
        _refresh_workbook(args, store)
        counts = store.stats()
    print(f"synced {args.config}: " + ", ".join(
        f"{n} {s}" for s, n in counts.items()) if counts else "synced (empty)")


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
        _absorb_excel_edits(args, store)
        job = store.set_status(args.job_id, args.new_status, note=args.note)
        _refresh_workbook(args, store)
        print(f"{job['job_id']} \"{job['title']}\" @ {job['company']} "
              f"-> {job['status']}")


def cmd_note(args, config: JobHunterConfig) -> None:
    with JobStore(config.sqlite_path) as store:
        _absorb_excel_edits(args, store)
        store.add_note(args.job_id, args.text)
        _refresh_workbook(args, store)
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
        print(f"{'total':>{width}}: {sum(counts.values())}")


def cmd_export(args, config: JobHunterConfig) -> None:
    with JobStore(config.sqlite_path) as store:
        if args.out.lower().endswith(".csv"):
            n = store.export_csv(args.out)
        else:
            n = workbook.export_snapshot(args.out, store)
    print(f"wrote {n} job(s) to {args.out}")


# -- wiring ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobs.py", description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=workbook.DEFAULT_WORKBOOK,
                        help="path to the tracker workbook (.xlsx); "
                             "a legacy .yaml config also works")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a fresh tracker workbook")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing workbook")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("hunt", help="absorb Excel edits, run searches, "
                                    "refresh the workbook")
    p.add_argument("--search", action="append", dest="searches",
                   metavar="NAME", help="run only this search (repeatable)")
    p.set_defaults(fn=cmd_hunt)

    p = sub.add_parser("sync", help="absorb Excel edits & refresh sheets "
                                    "without hunting")
    p.set_defaults(fn=cmd_sync)

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

    p = sub.add_parser("export", help="export the pipeline to .xlsx or .csv")
    p.add_argument("--out", default="jobs_export.xlsx")
    p.set_defaults(fn=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        stream=sys.stderr,
    )
    if args.command == "init":
        cmd_init(args)
        return
    if _is_workbook(args.config) and not Path(args.config).exists():
        # First run: hand the user a template instead of an error.
        workbook.create_template(args.config)
        raise SystemExit(
            f"created {args.config} (no workbook existed yet) — open it, "
            "customize the Searches / Filters / Scoring sheets, then re-run.")
    config = _load_any_config(args.config)
    try:
        args.fn(args, config)
    except KeyError as exc:
        # UnknownJobError / AmbiguousJobError read fine as one-liners.
        raise SystemExit(f"error: {exc.args[0]}") from exc
