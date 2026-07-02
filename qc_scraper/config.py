"""YAML config loading with sane defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class RateLimitConfig:
    min_delay_seconds: float = 8.0
    jitter_seconds: float = 4.0


@dataclass
class RetryConfig:
    max_attempts: int = 3
    backoff_base_seconds: float = 2.0


@dataclass
class BrowserConfig:
    headless: bool = True
    timeout_seconds: float = 45.0
    proxy: Optional[str] = None
    executable_path: Optional[str] = None  # use a specific Chromium binary


@dataclass
class AppConfig:
    pincodes: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    platforms: dict[str, bool] = field(default_factory=dict)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    sqlite_path: str = "data/observations.db"
    max_results_per_search: int = 15

    @property
    def enabled_platforms(self) -> list[str]:
        return [name for name, enabled in self.platforms.items() if enabled]


def load_config(path: str | Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}

    rl = raw.get("rate_limit", {}) or {}
    rt = raw.get("retry", {}) or {}
    br = raw.get("browser", {}) or {}
    st = raw.get("storage", {}) or {}

    return AppConfig(
        pincodes=[str(p) for p in raw.get("pincodes", [])],
        queries=[str(q) for q in raw.get("queries", [])],
        platforms={str(k): bool(v) for k, v in (raw.get("platforms", {}) or {}).items()},
        rate_limit=RateLimitConfig(
            min_delay_seconds=float(rl.get("min_delay_seconds", 8)),
            jitter_seconds=float(rl.get("jitter_seconds", 4)),
        ),
        retry=RetryConfig(
            max_attempts=int(rt.get("max_attempts", 3)),
            backoff_base_seconds=float(rt.get("backoff_base_seconds", 2)),
        ),
        browser=BrowserConfig(
            headless=bool(br.get("headless", True)),
            timeout_seconds=float(br.get("timeout_seconds", 45)),
            proxy=br.get("proxy") or None,
            executable_path=br.get("executable_path") or None,
        ),
        sqlite_path=str(st.get("sqlite_path", "data/observations.db")),
        max_results_per_search=int(raw.get("max_results_per_search", 15)),
    )
