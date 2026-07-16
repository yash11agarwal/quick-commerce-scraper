"""Job record and application-pipeline status definitions."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    """Where a job sits in the application pipeline."""

    NEW = "new"                  # just discovered, not yet reviewed
    INTERESTED = "interested"    # reviewed and shortlisted
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    ARCHIVED = "archived"        # not relevant / not pursuing

    @classmethod
    def values(cls) -> list[str]:
        return [s.value for s in cls]


#: Statuses shown by ``jobs.py list`` when no --status filter is given.
#: Terminal states (rejected/archived) are hidden unless asked for.
ACTIVE_STATUSES = [
    JobStatus.NEW.value,
    JobStatus.INTERESTED.value,
    JobStatus.APPLIED.value,
    JobStatus.INTERVIEWING.value,
    JobStatus.OFFER.value,
]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class JobRecord:
    """One job posting as discovered by a search."""

    job_id: str                    # LinkedIn numeric posting id (stable, dedupe key)
    title: str
    company: Optional[str]
    location: Optional[str]
    url: str                       # canonical https://www.linkedin.com/jobs/view/<id>
    posted_date: Optional[str]     # ISO date from the listing card, e.g. "2026-07-14"
    search_name: Optional[str] = None  # which configured search surfaced it
    score: int = 0                 # keyword relevance score (see filters.py)
    first_seen: str = field(default_factory=utcnow_iso)

    def as_dict(self) -> dict:
        return asdict(self)


def canonical_job_url(job_id: str) -> str:
    """Tracking-free permalink for a posting id."""
    return f"https://www.linkedin.com/jobs/view/{job_id}"
