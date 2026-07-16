"""Typed config loaded from job_config.yaml.

Search filters map onto LinkedIn's public guest-search query parameters;
the human-readable names accepted in YAML are translated to the wire codes
here so the config file never needs magic strings like ``f_E=4``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# --- LinkedIn guest-search parameter code maps -----------------------------

#: f_TPR — how recently the job was posted ("r" + seconds).
POSTED_WITHIN = {
    "day": "r86400",
    "week": "r604800",
    "month": "r2592000",
}

#: f_E — experience level.
EXPERIENCE_LEVELS = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid_senior": "4",
    "director": "5",
    "executive": "6",
}

#: f_WT — workplace type.
WORKPLACE_TYPES = {
    "onsite": "1",
    "remote": "2",
    "hybrid": "3",
}

#: f_JT — job type.
JOB_TYPES = {
    "full_time": "F",
    "part_time": "P",
    "contract": "C",
    "temporary": "T",
    "internship": "I",
}


def _map_codes(values: list[str], table: dict[str, str], what: str) -> list[str]:
    unknown = [v for v in values if v not in table]
    if unknown:
        raise ValueError(
            f"unknown {what} value(s) {unknown}; valid: {sorted(table)}")
    return [table[v] for v in values]


@dataclass
class SearchSpec:
    """One saved search: keywords + location + optional LinkedIn filters."""

    name: str
    keywords: str
    location: str
    posted_within: str | None = None        # day | week | month
    experience_levels: list[str] = field(default_factory=list)
    workplace: list[str] = field(default_factory=list)   # onsite/remote/hybrid
    job_types: list[str] = field(default_factory=list)   # full_time/contract/...
    max_pages: int = 4                      # 25 results per page

    def query_params(self) -> dict[str, str]:
        """Translate to guest-endpoint query params (excluding pagination)."""
        params = {"keywords": self.keywords, "location": self.location}
        if self.posted_within:
            if self.posted_within not in POSTED_WITHIN:
                raise ValueError(
                    f"posted_within must be one of {sorted(POSTED_WITHIN)}, "
                    f"got {self.posted_within!r}")
            params["f_TPR"] = POSTED_WITHIN[self.posted_within]
        if self.experience_levels:
            params["f_E"] = ",".join(
                _map_codes(self.experience_levels, EXPERIENCE_LEVELS,
                           "experience_levels"))
        if self.workplace:
            params["f_WT"] = ",".join(
                _map_codes(self.workplace, WORKPLACE_TYPES, "workplace"))
        if self.job_types:
            params["f_JT"] = ",".join(
                _map_codes(self.job_types, JOB_TYPES, "job_types"))
        return params


@dataclass
class FilterConfig:
    """Hard include/exclude rules applied before a job is stored."""

    #: If non-empty, the title must contain at least one of these
    #: (case-insensitive substring match).
    title_include_any: list[str] = field(default_factory=list)
    #: Drop the job if the title contains any of these.
    title_exclude: list[str] = field(default_factory=list)
    #: Drop the job if the company name contains any of these
    #: (useful for staffing agencies you don't want to see again).
    company_exclude: list[str] = field(default_factory=list)


@dataclass
class RateLimitConfig:
    min_delay_seconds: float = 5.0
    jitter_seconds: float = 3.0


@dataclass
class RetryConfig:
    max_attempts: int = 3
    backoff_base_seconds: float = 5.0


@dataclass
class JobHunterConfig:
    searches: list[SearchSpec]
    filters: FilterConfig
    #: keyword -> points; each keyword found in the title adds its points.
    score_keywords: dict[str, int]
    rate_limit: RateLimitConfig
    retry: RetryConfig
    sqlite_path: str = "data/jobs.db"
    request_timeout_seconds: float = 30.0


def validate_searches(searches: list[SearchSpec], source: str) -> None:
    """Shared sanity checks; fail fast at load time instead of mid-run."""
    if not searches:
        raise ValueError(f"{source}: config needs at least one search")
    names = [s.name for s in searches]
    if len(names) != len(set(names)):
        raise ValueError(f"{source}: search names must be unique, got {names}")
    for s in searches:
        s.query_params()  # raises on bad filter values


def load_config(path: str | Path) -> JobHunterConfig:
    """YAML loader (legacy). The Excel workbook is the primary interface —
    see workbook.load_config — but a .yaml config keeps working."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    searches = [SearchSpec(**s) for s in raw.get("searches", [])]
    validate_searches(searches, str(path))

    return JobHunterConfig(
        searches=searches,
        filters=FilterConfig(**raw.get("filters", {})),
        score_keywords={str(k).lower(): int(v)
                        for k, v in (raw.get("score_keywords") or {}).items()},
        rate_limit=RateLimitConfig(**raw.get("rate_limit", {})),
        retry=RetryConfig(**raw.get("retry", {})),
        sqlite_path=(raw.get("storage") or {}).get("sqlite_path", "data/jobs.db"),
        request_timeout_seconds=float(raw.get("request_timeout_seconds", 30.0)),
    )
