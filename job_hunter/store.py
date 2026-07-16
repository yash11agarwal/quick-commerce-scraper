"""SQLite persistence for the job pipeline.

Unlike the price scraper's append-only time series, this store is keyed:
one row per LinkedIn posting id. Re-discovering a job on a later hunt
only bumps ``last_seen`` — your status/notes are never clobbered. Every
status change is also appended to ``status_history`` so you can see the
full timeline of an application.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .schema import ACTIVE_STATUSES, JobRecord, JobStatus, utcnow_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id            TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    company           TEXT,
    location          TEXT,
    url               TEXT NOT NULL,
    posted_date       TEXT,                       -- ISO date from the listing
    search_name       TEXT,                       -- which saved search found it
    score             INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'new',
    notes             TEXT NOT NULL DEFAULT '',
    description       TEXT,                       -- fetched on demand
    first_seen        TEXT NOT NULL,              -- ISO-8601 UTC
    last_seen         TEXT NOT NULL,
    status_updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_search ON jobs (search_name);

CREATE TABLE IF NOT EXISTS status_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL REFERENCES jobs (job_id),
    old_status TEXT,
    new_status TEXT NOT NULL,
    note       TEXT,
    changed_at TEXT NOT NULL
);
"""

_EXPORT_COLUMNS = [
    "job_id", "title", "company", "location", "url", "posted_date",
    "search_name", "score", "status", "notes", "first_seen", "last_seen",
    "status_updated_at",
]


class UnknownJobError(KeyError):
    """No stored job matches the given id / id prefix."""


class AmbiguousJobError(KeyError):
    """An id prefix matched more than one stored job."""


class JobStore:
    def __init__(self, db_path: str | Path):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- discovery -------------------------------------------------------

    def add_jobs(self, records: Iterable[JobRecord]) -> list[JobRecord]:
        """Insert newly discovered jobs; returns only the genuinely new ones.

        Already-known job_ids just get last_seen refreshed (a job that
        keeps reappearing in searches is still one pipeline entry).
        """
        new: list[JobRecord] = []
        now = utcnow_iso()
        with self._conn:
            for r in records:
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO jobs (
                           job_id, title, company, location, url, posted_date,
                           search_name, score, status, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r.job_id, r.title, r.company, r.location, r.url,
                     r.posted_date, r.search_name, r.score,
                     JobStatus.NEW.value, r.first_seen, now),
                )
                if cur.rowcount:
                    new.append(r)
                else:
                    self._conn.execute(
                        "UPDATE jobs SET last_seen = ? WHERE job_id = ?",
                        (now, r.job_id))
        return new

    # -- lookup ----------------------------------------------------------

    def resolve_id(self, id_or_prefix: str) -> str:
        """Accept a full job id or any unique prefix of one."""
        rows = self._conn.execute(
            "SELECT job_id FROM jobs WHERE job_id LIKE ? LIMIT 3",
            (id_or_prefix + "%",)).fetchall()
        exact = [r["job_id"] for r in rows if r["job_id"] == id_or_prefix]
        if exact:
            return exact[0]
        if not rows:
            raise UnknownJobError(f"no tracked job with id {id_or_prefix!r}")
        if len(rows) > 1:
            raise AmbiguousJobError(
                f"id prefix {id_or_prefix!r} matches multiple jobs; "
                "give more digits")
        return rows[0]["job_id"]

    def get_job(self, id_or_prefix: str) -> sqlite3.Row:
        job_id = self.resolve_id(id_or_prefix)
        return self._conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()

    def list_jobs(
        self,
        statuses: Optional[list[str]] = None,
        search_name: Optional[str] = None,
        company: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[sqlite3.Row]:
        """Filtered listing, best score first, then newest posting."""
        clauses, params = [], []
        if statuses is None:
            statuses = ACTIVE_STATUSES
        if statuses:  # empty list means "all statuses"
            clauses.append(
                f"status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        if search_name:
            clauses.append("search_name = ?")
            params.append(search_name)
        if company:
            clauses.append("LOWER(company) LIKE ?")
            params.append(f"%{company.lower()}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (f"SELECT * FROM jobs {where} "
               "ORDER BY score DESC, posted_date DESC, first_seen DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self._conn.execute(sql, params).fetchall()

    # -- pipeline updates --------------------------------------------------

    def set_status(self, id_or_prefix: str, status: str,
                   note: Optional[str] = None) -> sqlite3.Row:
        if status not in JobStatus.values():
            raise ValueError(
                f"invalid status {status!r}; valid: {JobStatus.values()}")
        job_id = self.resolve_id(id_or_prefix)
        now = utcnow_iso()
        with self._conn:
            old = self._conn.execute(
                "SELECT status FROM jobs WHERE job_id = ?",
                (job_id,)).fetchone()["status"]
            self._conn.execute(
                "UPDATE jobs SET status = ?, status_updated_at = ? "
                "WHERE job_id = ?", (status, now, job_id))
            self._conn.execute(
                "INSERT INTO status_history "
                "(job_id, old_status, new_status, note, changed_at) "
                "VALUES (?, ?, ?, ?, ?)", (job_id, old, status, note, now))
            if note:
                self._append_note(job_id, note, now)
        return self.get_job(job_id)

    def add_note(self, id_or_prefix: str, text: str) -> None:
        job_id = self.resolve_id(id_or_prefix)
        with self._conn:
            self._append_note(job_id, text, utcnow_iso())

    def _append_note(self, job_id: str, text: str, when: str) -> None:
        self._conn.execute(
            "UPDATE jobs SET notes = notes || ? WHERE job_id = ?",
            (f"[{when}] {text}\n", job_id))

    def set_description(self, job_id: str, description: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET description = ? WHERE job_id = ?",
                (description, job_id))

    def history(self, id_or_prefix: str) -> list[sqlite3.Row]:
        job_id = self.resolve_id(id_or_prefix)
        return self._conn.execute(
            "SELECT * FROM status_history WHERE job_id = ? ORDER BY id",
            (job_id,)).fetchall()

    # -- reporting ---------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """status -> count, in pipeline order (only statuses that occur)."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        return {s: counts[s] for s in JobStatus.values() if s in counts}

    def stats_by_search(self) -> dict[str, int]:
        """search_name -> count, most productive search first."""
        rows = self._conn.execute(
            "SELECT COALESCE(search_name, '(unknown)') AS s, COUNT(*) AS n "
            "FROM jobs GROUP BY s ORDER BY n DESC").fetchall()
        return {r["s"]: r["n"] for r in rows}

    def export_csv(self, out_path: str | Path) -> int:
        """Dump every tracked job to CSV; returns row count."""
        rows = self.list_jobs(statuses=[])  # all statuses
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(_EXPORT_COLUMNS)
            for row in rows:
                writer.writerow([row[c] for c in _EXPORT_COLUMNS])
        return len(rows)

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
