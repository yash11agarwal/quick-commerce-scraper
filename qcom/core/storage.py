"""SQLite persistence: runs, jobs, attempts, raw payloads, listings, data quality, platform state.

One ``Storage`` per thread (SQLite connections are not shared across threads). WAL mode so
readers never block writers. Every job outcome is written in one transaction.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from qcom.core.clock import iso, now_utc, to_ist
from qcom.core.errors import ErrorCode
from qcom.core.models import (
    DataQualityEvent,
    EffectiveLocation,
    Job,
    JobStatus,
    ProductListing,
    RawCapture,
)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  started_at_utc TEXT NOT NULL, started_at_ist TEXT NOT NULL,
  ended_at_utc TEXT, ended_at_ist TEXT,
  code_version TEXT, git_sha TEXT, config_hash TEXT, config_json TEXT,
  input_path TEXT, input_sha256 TEXT, run_label TEXT,
  proxy_label TEXT, adapter_versions_json TEXT,
  status TEXT NOT NULL, exit_code INTEGER, summary_json TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  platform TEXT NOT NULL, requested_pincode TEXT NOT NULL, city TEXT, state TEXT,
  search_term TEXT NOT NULL, input_row_id INTEGER NOT NULL, pincode_row_id INTEGER NOT NULL,
  brand TEXT, pack_size TEXT, category TEXT, max_results INTEGER NOT NULL,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  final_code TEXT, final_reason TEXT, strategy TEXT,
  effective_pincode TEXT, store_id TEXT, eta_minutes INTEGER, location_evidence_json TEXT,
  first_started_utc TEXT, last_finished_utc TEXT, duration_ms INTEGER,
  artifact_path TEXT, results_returned INTEGER,
  UNIQUE (run_id, platform, requested_pincode, input_row_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_run_status ON jobs (run_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_run_platform ON jobs (run_id, platform);

CREATE TABLE IF NOT EXISTS attempts (
  attempt_id INTEGER PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(job_id), attempt_no INTEGER NOT NULL,
  started_utc TEXT NOT NULL, finished_utc TEXT,
  outcome TEXT, error_code TEXT, error_message TEXT, traceback TEXT, artifact_path TEXT
);

CREATE TABLE IF NOT EXISTS raw_payloads (
  capture_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL, job_id TEXT, attempt_no INTEGER, seq INTEGER NOT NULL,
  platform TEXT NOT NULL, strategy TEXT NOT NULL, source TEXT NOT NULL,
  method TEXT, url TEXT, http_status INTEGER, content_type TEXT, request_json TEXT,
  sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, body_zlib BLOB NOT NULL,
  parse INTEGER NOT NULL DEFAULT 1,
  captured_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_run ON raw_payloads (run_id, seq);
CREATE INDEX IF NOT EXISTS idx_raw_job ON raw_payloads (job_id);

CREATE TABLE IF NOT EXISTS listings (
  listing_id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(job_id),
  capture_id TEXT NOT NULL REFERENCES raw_payloads(capture_id),
  captured_at_utc TEXT NOT NULL, captured_at_ist TEXT NOT NULL,
  platform TEXT NOT NULL, requested_pincode TEXT NOT NULL, effective_pincode TEXT,
  city TEXT, search_term TEXT NOT NULL, input_row_id INTEGER NOT NULL,
  result_rank INTEGER NOT NULL, platform_product_id TEXT NOT NULL, product_name TEXT NOT NULL,
  brand TEXT, pack_size TEXT, unit_normalised TEXT,
  mrp_paise INTEGER, selling_price_paise INTEGER, base_selling_price_paise INTEGER,
  discount_pct TEXT, price_per_unit_paise INTEGER, currency TEXT NOT NULL,
  in_stock INTEGER, stock_qty INTEGER, eta_minutes INTEGER, store_or_seller_id TEXT,
  category_path TEXT, product_url TEXT, image_url TEXT, match_score TEXT, strategy TEXT
);
CREATE INDEX IF NOT EXISTS idx_listings_run ON listings (run_id);
CREATE INDEX IF NOT EXISTS idx_listings_history ON listings (platform, requested_pincode, platform_product_id, captured_at_utc);

CREATE TABLE IF NOT EXISTS dq_events (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL, job_id TEXT NOT NULL, listing_id INTEGER,
  kind TEXT NOT NULL, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_dq_run ON dq_events (run_id, kind);

CREATE TABLE IF NOT EXISTS platform_state (
  run_id TEXT NOT NULL, platform TEXT NOT NULL,
  status TEXT NOT NULL, reason TEXT, consecutive_failures INTEGER NOT NULL DEFAULT 0, stopped_at_utc TEXT,
  PRIMARY KEY (run_id, platform)
);
"""


def _dec(v: Decimal | None) -> str | None:
    return None if v is None else format(v, "f")


def _bool(v: bool | None) -> int | None:
    return None if v is None else int(v)


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._ensure_schema()

    # ------------------------------------------------------------------ lifecycle

    def _ensure_schema(self) -> None:
        self._conn.executescript(_SCHEMA)  # executescript manages its own transaction
        with self._tx():
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] != SCHEMA_VERSION:
                raise RuntimeError(f"database schema version {row['version']} != code {SCHEMA_VERSION}")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    class _Tx:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self.conn = conn

        def __enter__(self) -> sqlite3.Connection:
            self.conn.execute("BEGIN IMMEDIATE")
            return self.conn

        def __exit__(self, exc_type: object, *_: object) -> None:
            if exc_type is None:
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")

    def _tx(self) -> "Storage._Tx":
        return Storage._Tx(self._conn)

    # ------------------------------------------------------------------ runs

    def create_run(
        self,
        run_id: str,
        *,
        started_at: datetime,
        code_version: str,
        git_sha: str | None,
        config_hash: str,
        config_json: dict[str, Any],
        input_path: str,
        input_sha256: str,
        run_label: str | None,
        proxy_label: str | None,
        adapter_versions: dict[str, str],
    ) -> None:
        with self._tx():
            self._conn.execute(
                """INSERT INTO runs (run_id, started_at_utc, started_at_ist, code_version, git_sha, config_hash, config_json,
                   input_path, input_sha256, run_label, proxy_label, adapter_versions_json, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, iso(started_at), iso(to_ist(started_at)), code_version, git_sha, config_hash,
                    json.dumps(config_json, sort_keys=True), input_path, input_sha256, run_label, proxy_label,
                    json.dumps(adapter_versions, sort_keys=True), "IN_PROGRESS",
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def finish_run(self, run_id: str, *, ended_at: datetime, status: str, exit_code: int, summary: dict[str, Any]) -> None:
        with self._tx():
            self._conn.execute(
                "UPDATE runs SET ended_at_utc=?, ended_at_ist=?, status=?, exit_code=?, summary_json=? WHERE run_id=?",
                (iso(ended_at), iso(to_ist(ended_at)), status, exit_code, json.dumps(summary, sort_keys=True, default=str), run_id),
            )

    def previous_run_id(self, before_run_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT run_id FROM runs WHERE run_id < ? AND status != 'IN_PROGRESS' ORDER BY run_id DESC LIMIT 1", (before_run_id,)
        ).fetchone()
        return row["run_id"] if row else None

    # ------------------------------------------------------------------ jobs

    def insert_jobs(self, jobs: list[Job]) -> None:
        with self._tx():
            self._conn.executemany(
                """INSERT INTO jobs (job_id, run_id, platform, requested_pincode, city, state, search_term, input_row_id,
                   pincode_row_id, brand, pack_size, category, max_results, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        j.job_id, j.run_id, j.platform, j.requested_pincode, j.city, j.state, j.search_term, j.input_row_id,
                        j.pincode_row_id, j.brand, j.pack_size, j.category, j.max_results, JobStatus.PENDING.value,
                    )
                    for j in jobs
                ],
            )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> Job:
        return Job(
            job_id=row["job_id"], run_id=row["run_id"], platform=row["platform"], requested_pincode=row["requested_pincode"],
            city=row["city"], state=row["state"], search_term=row["search_term"], input_row_id=row["input_row_id"],
            pincode_row_id=row["pincode_row_id"], brand=row["brand"], pack_size=row["pack_size"], category=row["category"],
            max_results=row["max_results"],
        )

    def pending_jobs(self, run_id: str) -> list[Job]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE run_id = ? AND status IN ('PENDING','IN_PROGRESS') ORDER BY platform, pincode_row_id, input_row_id",
            (run_id,),
        ).fetchall()
        return [self._job_from_row(r) for r in rows]

    def job_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE run_id = ? ORDER BY platform, pincode_row_id, input_row_id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def job_attempts(self, job_id: str) -> int:
        row = self._conn.execute("SELECT attempts FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return int(row["attempts"]) if row else 0

    def reset_in_progress(self, run_id: str) -> int:
        """A previous process died inside these jobs. Drop any partial rows and make them pending again."""
        with self._tx():
            rows = self._conn.execute(
                "SELECT job_id FROM jobs WHERE run_id = ? AND status = 'IN_PROGRESS'", (run_id,)
            ).fetchall()
            ids = [r["job_id"] for r in rows]
            for job_id in ids:
                self._conn.execute("DELETE FROM dq_events WHERE job_id = ?", (job_id,))
                self._conn.execute("DELETE FROM listings WHERE job_id = ?", (job_id,))
                self._conn.execute("UPDATE jobs SET status='PENDING' WHERE job_id = ?", (job_id,))
            return len(ids)

    def start_attempt(self, job_id: str, started_at: datetime) -> int:
        """Increment attempts, mark IN_PROGRESS, open an attempt row. Returns the attempt number."""
        with self._tx():
            row = self._conn.execute("SELECT attempts, first_started_utc FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            attempt_no = int(row["attempts"]) + 1
            first = row["first_started_utc"] or iso(started_at)
            self._conn.execute(
                "UPDATE jobs SET attempts=?, status='IN_PROGRESS', first_started_utc=? WHERE job_id=?",
                (attempt_no, first, job_id),
            )
            self._conn.execute(
                "INSERT INTO attempts (job_id, attempt_no, started_utc) VALUES (?,?,?)", (job_id, attempt_no, iso(started_at))
            )
            return attempt_no

    def close_attempt(
        self,
        job_id: str,
        attempt_no: int,
        *,
        finished_at: datetime,
        outcome: str,
        error_code: ErrorCode | None = None,
        error_message: str | None = None,
        traceback_text: str | None = None,
        artifact_path: str | None = None,
    ) -> None:
        with self._tx():
            self._conn.execute(
                """UPDATE attempts SET finished_utc=?, outcome=?, error_code=?, error_message=?, traceback=?, artifact_path=?
                   WHERE job_id=? AND attempt_no=?""",
                (iso(finished_at), outcome, error_code.value if error_code else None, error_message, traceback_text, artifact_path, job_id, attempt_no),
            )

    def attempt_rows(self, job_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM attempts WHERE job_id = ? ORDER BY attempt_no", (job_id,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ raw payloads

    def save_captures(self, run_id: str, job_id: str | None, attempt_no: int | None, captures: list[RawCapture]) -> list[RawCapture]:
        """Persist verbatim, compressed. Assigns capture ids. Must be called before any parse."""
        with self._tx():
            row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM raw_payloads WHERE run_id = ?", (run_id,)).fetchone()
            seq = int(row["m"])
            for cap in captures:
                seq += 1
                cap.seq = seq
                cap.capture_id = f"{run_id}:{seq:06d}"
                self._conn.execute(
                    """INSERT INTO raw_payloads (capture_id, run_id, job_id, attempt_no, seq, platform, strategy, source, method, url,
                       http_status, content_type, request_json, sha256, size_bytes, body_zlib, parse, captured_at_utc)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        cap.capture_id, run_id, job_id, attempt_no, seq, cap.platform, cap.strategy, cap.source.value, cap.method,
                        cap.url, cap.http_status, cap.content_type, json.dumps(cap.request, sort_keys=True, default=str),
                        cap.sha256, cap.size_bytes, zlib.compress(cap.body, 6), int(cap.parse), iso(cap.captured_at_utc),
                    ),
                )
        return captures

    def capture_body(self, capture_id: str) -> bytes:
        row = self._conn.execute("SELECT body_zlib, sha256 FROM raw_payloads WHERE capture_id = ?", (capture_id,)).fetchone()
        if row is None:
            raise KeyError(capture_id)
        body = zlib.decompress(row["body_zlib"])
        import hashlib

        if hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise RuntimeError(f"stored payload {capture_id} failed its checksum")
        return body

    def capture_rows(self, run_id: str, job_id: str | None = None) -> list[dict[str, Any]]:
        if job_id is None:
            rows = self._conn.execute(
                "SELECT capture_id, run_id, job_id, attempt_no, seq, platform, strategy, source, method, url, http_status, content_type, sha256, size_bytes, parse, captured_at_utc FROM raw_payloads WHERE run_id = ? ORDER BY seq",
                (run_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT capture_id, run_id, job_id, attempt_no, seq, platform, strategy, source, method, url, http_status, content_type, sha256, size_bytes, parse, captured_at_utc FROM raw_payloads WHERE job_id = ? ORDER BY seq",
                (job_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ job outcomes

    def finish_job(
        self,
        job: Job,
        *,
        status: JobStatus,
        finished_at: datetime,
        code: ErrorCode | None,
        reason: str | None,
        strategy: str | None,
        location: EffectiveLocation | None,
        listings: list[ProductListing],
        dq_events: list[DataQualityEvent],
        duration_ms: int | None,
        artifact_path: str | None = None,
    ) -> None:
        """One transaction: listings, data quality events and the job's final state together."""
        with self._tx():
            self._conn.execute("DELETE FROM listings WHERE job_id = ?", (job.job_id,))
            self._conn.execute("DELETE FROM dq_events WHERE job_id = ?", (job.job_id,))
            listing_ids: list[int] = []
            for lst in listings:
                cur = self._conn.execute(
                    """INSERT INTO listings (run_id, job_id, capture_id, captured_at_utc, captured_at_ist, platform, requested_pincode,
                       effective_pincode, city, search_term, input_row_id, result_rank, platform_product_id, product_name, brand,
                       pack_size, unit_normalised, mrp_paise, selling_price_paise, base_selling_price_paise, discount_pct,
                       price_per_unit_paise, currency, in_stock, stock_qty, eta_minutes, store_or_seller_id, category_path,
                       product_url, image_url, match_score, strategy)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job.run_id, job.job_id, lst.capture_id, iso(finished_at), iso(to_ist(finished_at)), job.platform,
                        job.requested_pincode, lst.effective_pincode, job.city, job.search_term, job.input_row_id, lst.result_rank,
                        lst.platform_product_id, lst.product_name, lst.brand, lst.pack_size, lst.unit_normalised, lst.mrp_paise,
                        lst.selling_price_paise, lst.base_selling_price_paise, _dec(lst.discount_pct), lst.price_per_unit_paise,
                        lst.currency, _bool(lst.in_stock), lst.stock_qty, lst.eta_minutes, lst.store_or_seller_id, lst.category_path,
                        lst.product_url, lst.image_url, _dec(lst.match_score), lst.strategy,
                    ),
                )
                listing_ids.append(int(cur.lastrowid))
            for ev in dq_events:
                lid = listing_ids[ev.listing_index] if ev.listing_index is not None and ev.listing_index < len(listing_ids) else None
                self._conn.execute(
                    "INSERT INTO dq_events (run_id, job_id, listing_id, kind, detail) VALUES (?,?,?,?,?)",
                    (job.run_id, job.job_id, lid, ev.kind, ev.detail),
                )
            self._conn.execute(
                """UPDATE jobs SET status=?, final_code=?, final_reason=?, strategy=?, effective_pincode=?, store_id=?, eta_minutes=?,
                   location_evidence_json=?, last_finished_utc=?, duration_ms=?, artifact_path=COALESCE(?, artifact_path), results_returned=?
                   WHERE job_id=?""",
                (
                    status.value, code.value if code else None, reason, strategy,
                    location.effective_pincode if location else None, location.store_id if location else None,
                    location.eta_minutes if location else None,
                    json.dumps(location.evidence, sort_keys=True, default=str) if location else None,
                    iso(finished_at), duration_ms, artifact_path, len(listings), job.job_id,
                ),
            )

    def skip_pending(self, run_id: str, platform: str, *, code: ErrorCode | None, reason: str, at: datetime) -> int:
        with self._tx():
            cur = self._conn.execute(
                """UPDATE jobs SET status='SKIPPED', final_code=?, final_reason=?, last_finished_utc=?
                   WHERE run_id=? AND platform=? AND status IN ('PENDING','IN_PROGRESS')""",
                (code.value if code else None, reason, iso(at), run_id, platform),
            )
            return int(cur.rowcount)

    # ------------------------------------------------------------------ platform state

    def set_platform_state(self, run_id: str, platform: str, *, status: str, reason: str | None, consecutive_failures: int) -> None:
        with self._tx():
            self._conn.execute(
                """INSERT INTO platform_state (run_id, platform, status, reason, consecutive_failures, stopped_at_utc)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(run_id, platform) DO UPDATE SET status=excluded.status, reason=excluded.reason,
                   consecutive_failures=excluded.consecutive_failures, stopped_at_utc=excluded.stopped_at_utc""",
                (run_id, platform, status, reason, consecutive_failures, iso(now_utc()) if status != "ACTIVE" else None),
            )

    def platform_states(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM platform_state WHERE run_id = ? ORDER BY platform", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ history

    def previous_selling_price(self, platform: str, pincode: str, product_id: str, *, before_run_id: str) -> tuple[str, int] | None:
        row = self._conn.execute(
            """SELECT run_id, selling_price_paise FROM listings
               WHERE platform=? AND requested_pincode=? AND platform_product_id=? AND run_id < ? AND selling_price_paise IS NOT NULL
               ORDER BY run_id DESC, captured_at_utc DESC LIMIT 1""",
            (platform, pincode, product_id, before_run_id),
        ).fetchone()
        if row is None:
            return None
        return row["run_id"], int(row["selling_price_paise"])

    # ------------------------------------------------------------------ projections for Excel and summaries

    def results_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT l.*, j.city AS job_city FROM listings l JOIN jobs j ON j.job_id = l.job_id
               WHERE l.run_id = ? ORDER BY l.platform, j.pincode_row_id, j.input_row_id, l.result_rank""",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def dq_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM dq_events WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def dq_counts(self, run_id: str) -> dict[str, int]:
        rows = self._conn.execute("SELECT kind, COUNT(*) AS n FROM dq_events WHERE run_id = ? GROUP BY kind ORDER BY kind", (run_id,)).fetchall()
        return {r["kind"]: int(r["n"]) for r in rows}

    def status_counts(self, run_id: str) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM jobs WHERE run_id = ? GROUP BY status", (run_id,)).fetchall()
        counts = {s.value: 0 for s in JobStatus}
        for r in rows:
            counts[r["status"]] = int(r["n"])
        return counts

    def code_counts(self, run_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT final_code, COUNT(*) AS n FROM jobs WHERE run_id = ? AND final_code IS NOT NULL GROUP BY final_code", (run_id,)
        ).fetchall()
        return {r["final_code"]: int(r["n"]) for r in rows}
