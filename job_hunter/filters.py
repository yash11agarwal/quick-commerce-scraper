"""Relevance filtering and keyword scoring for discovered jobs.

Filters decide whether a job is stored at all; the score only affects
ordering (highest first) so the most promising postings surface at the
top of ``jobs.py hunt`` / ``jobs.py list`` output.
"""

from __future__ import annotations

from typing import Optional

from .config import FilterConfig
from .schema import JobRecord


def _contains_any(text: Optional[str], needles: list[str]) -> Optional[str]:
    """First needle found in text (case-insensitive), else None."""
    if not text:
        return None
    lowered = text.lower()
    for needle in needles:
        if needle.lower() in lowered:
            return needle
    return None


def passes_filters(job: JobRecord, filters: FilterConfig) -> tuple[bool, str]:
    """(keep?, reason) — reason is only meaningful when keep is False."""
    hit = _contains_any(job.title, filters.title_exclude)
    if hit:
        return False, f"title contains excluded keyword {hit!r}"
    hit = _contains_any(job.company, filters.company_exclude)
    if hit:
        return False, f"company matches excluded name {hit!r}"
    if filters.title_include_any:
        if not _contains_any(job.title, filters.title_include_any):
            return False, "title matches none of title_include_any"
    return True, ""


def score_job(job: JobRecord, score_keywords: dict[str, int]) -> int:
    """Sum of points for every configured keyword present in the title."""
    if not score_keywords or not job.title:
        return 0
    lowered = job.title.lower()
    return sum(points for kw, points in score_keywords.items() if kw in lowered)
