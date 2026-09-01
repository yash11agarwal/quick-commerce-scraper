"""Timestamps: timezone-aware, both IST and UTC, ISO 8601."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; qcom only handles timezone-aware datetimes")
    return dt.astimezone(IST)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; qcom only handles timezone-aware datetimes")
    return dt.isoformat(timespec="seconds")


def parse_iso(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"stored timestamp is naive: {text!r}")
    return dt


def new_run_id(now: datetime | None = None) -> str:
    """``YYYYMMDD-HHMMSS-xxxxxx`` in IST; sortable, human-readable, unique enough."""
    stamp = to_ist(now or now_utc()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"
