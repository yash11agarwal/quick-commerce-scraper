"""Run summary and exit status. Failures go at the top, always."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from qcom.core.config import AppConfig
from qcom.core.storage import Storage

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_INPUT_INVALID = 2
EXIT_ABORTED = 3


@dataclass
class RunSummary:
    run_id: str
    started_at: datetime
    ended_at: datetime
    status_counts: dict[str, int]
    code_counts: dict[str, int]
    dq_counts: dict[str, int]
    platform_states: list[dict[str, Any]]
    listings: int
    exit_code: int
    run_status: str
    failure_rate: float
    notes: list[str] = field(default_factory=list)

    @property
    def total_jobs(self) -> int:
        return sum(self.status_counts.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at_utc": self.started_at.isoformat(timespec="seconds"),
            "ended_at_utc": self.ended_at.isoformat(timespec="seconds"),
            "status_counts": self.status_counts,
            "code_counts": self.code_counts,
            "dq_counts": self.dq_counts,
            "platform_states": self.platform_states,
            "listings": self.listings,
            "exit_code": self.exit_code,
            "run_status": self.run_status,
            "failure_rate": round(self.failure_rate, 4),
            "notes": self.notes,
        }


def build_summary(storage: Storage, run_id: str, cfg: AppConfig, *, started_at: datetime, ended_at: datetime, aborted: bool = False) -> RunSummary:
    status_counts = storage.status_counts(run_id)
    code_counts = storage.code_counts(run_id)
    dq_counts = storage.dq_counts(run_id)
    states = storage.platform_states(run_id)
    listings = len(storage.results_rows(run_id))
    total = sum(status_counts.values()) or 1
    failed = status_counts.get("FAILED", 0) + status_counts.get("SKIPPED", 0)
    failure_rate = failed / total

    notes: list[str] = []
    blocked = [s["platform"] for s in states if s["status"] == "STOPPED_BLOCKED"]
    for s in states:
        if s["status"] != "ACTIVE":
            notes.append(f"platform {s['platform']} stopped: {s['status']} ({s['reason']})")
    if code_counts.get("UNKNOWN"):
        notes.append(f"{code_counts['UNKNOWN']} job(s) ended UNKNOWN: treat each as a bug to classify")
    if status_counts.get("PENDING", 0) or status_counts.get("IN_PROGRESS", 0):
        notes.append("run is incomplete: use `python -m qcom resume --run-id " + run_id + "`")

    if aborted:
        exit_code, run_status = EXIT_ABORTED, "ABORTED"
    elif blocked or failure_rate > cfg.run.max_failure_rate:
        exit_code, run_status = EXIT_FAILURES, "COMPLETED_WITH_FAILURES"
        if failure_rate > cfg.run.max_failure_rate:
            notes.insert(0, f"failure rate {failure_rate:.0%} exceeds the {cfg.run.max_failure_rate:.0%} threshold")
        if blocked:
            notes.insert(0, f"blocked on: {', '.join(blocked)}")
    else:
        exit_code, run_status = EXIT_OK, "COMPLETED"

    return RunSummary(
        run_id=run_id,
        started_at=started_at,
        ended_at=ended_at,
        status_counts=status_counts,
        code_counts=code_counts,
        dq_counts=dq_counts,
        platform_states=states,
        listings=listings,
        exit_code=exit_code,
        run_status=run_status,
        failure_rate=failure_rate,
        notes=notes,
    )


def render_text(s: RunSummary, workbook_path: str | None = None) -> str:
    lines: list[str] = []
    headline = "RUN OK" if s.exit_code == EXIT_OK else ("RUN ABORTED" if s.exit_code == EXIT_ABORTED else "RUN COMPLETED WITH FAILURES")
    lines.append(f"{headline}  run_id={s.run_id}  exit={s.exit_code}")
    for note in s.notes:
        lines.append(f"  ! {note}")
    lines.append(f"jobs: {s.total_jobs}  " + "  ".join(f"{k}={v}" for k, v in s.status_counts.items() if v))
    if s.code_counts:
        lines.append("codes: " + "  ".join(f"{k}={v}" for k, v in sorted(s.code_counts.items())))
    lines.append(f"listings written: {s.listings}")
    if s.dq_counts:
        lines.append("data quality: " + "  ".join(f"{k}={v}" for k, v in sorted(s.dq_counts.items())))
    dur = (s.ended_at - s.started_at).total_seconds()
    lines.append(f"duration: {dur:.1f}s")
    if workbook_path:
        lines.append(f"workbook: {workbook_path}")
    return "\n".join(lines)
