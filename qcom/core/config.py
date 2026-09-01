"""config.yaml plus .env, validated. Secrets never touch config.yaml."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from qcom.core.errors import ConfigError


class RunCfg(BaseModel):
    max_failure_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    output_dir: str = "output"


class BrowserCfg(BaseModel):
    headless: bool = True
    device: str = "Desktop Chrome"
    navigation_timeout_s: float = Field(default=45.0, gt=0)
    launch_attempts: int = Field(default=3, ge=1)
    executable_path: str | None = None  # a specific Chromium binary; default is Playwright's own download


class ProxyCfg(BaseModel):
    label: str | None = None
    # filled from .env, never from config.yaml
    server: str | None = None
    username: str | None = None
    password: str | None = None
    fallback_servers: list[str] = Field(default_factory=list)

    @property
    def configured(self) -> bool:
        return bool(self.server)


class ThrottleCfg(BaseModel):
    min_gap_s: float = Field(default=8.0, ge=0)
    jitter_s: float = Field(default=4.0, ge=0)


class RetryEntry(BaseModel):
    attempts: int = Field(ge=1)
    backoff_base_s: float = Field(default=0.0, ge=0)
    jitter_s: float = Field(default=0.0, ge=0)


class RetryCfg(BaseModel):
    network_timeout: RetryEntry = RetryEntry(attempts=3, backoff_base_s=2, jitter_s=1)
    rate_limited: RetryEntry = RetryEntry(attempts=2, backoff_base_s=60, jitter_s=5)
    proxy_error: RetryEntry = RetryEntry(attempts=2, backoff_base_s=5, jitter_s=1)
    location_not_set: RetryEntry = RetryEntry(attempts=2, backoff_base_s=3, jitter_s=1)
    unknown: RetryEntry = RetryEntry(attempts=1)


class CircuitBreakerCfg(BaseModel):
    consecutive_failures: int = Field(default=5, ge=1)


class ConcurrencyCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    max_platforms_in_parallel: int = Field(default=2, ge=1)

    def contexts_for(self, platform: str) -> int:
        extra = self.model_extra or {}
        value = extra.get(platform, 1)
        if not isinstance(value, int) or value < 1:
            raise ConfigError(f"concurrency.{platform} must be a positive integer, got {value!r}")
        return value


class StorageCfg(BaseModel):
    path: str = "data/qcom.sqlite"
    sessions_dir: str = "sessions"
    runs_dir: str = "runs"


class QualityCfg(BaseModel):
    price_move_warn_pct: float = Field(default=40.0, ge=0)


class AppConfig(BaseModel):
    version: int = 2
    run: RunCfg = RunCfg()
    browser: BrowserCfg = BrowserCfg()
    proxy: ProxyCfg = ProxyCfg()
    throttle: ThrottleCfg = ThrottleCfg()
    retry: RetryCfg = RetryCfg()
    circuit_breaker: CircuitBreakerCfg = CircuitBreakerCfg()
    concurrency: ConcurrencyCfg = ConcurrencyCfg()
    storage: StorageCfg = StorageCfg()
    quality: QualityCfg = QualityCfg()

    def public_dict(self) -> dict[str, Any]:
        """The config without secrets, for hashing and for run_meta."""
        data = self.model_dump(mode="json")
        data["proxy"] = {"label": self.proxy.label, "configured": self.proxy.configured}
        return data

    def config_hash(self) -> str:
        canonical = json.dumps(self.public_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """Minimal KEY=VALUE reader. Values already in the environment win."""
    p = Path(path)
    loaded: dict[str, str] = {}
    if not p.exists():
        return loaded
    for lineno, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{p}:{lineno}: expected KEY=VALUE")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
        loaded[key] = value
    return loaded


def load_config(path: str | Path = "config.yaml", env_path: str | Path = ".env") -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"{p} not found")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{p}: top level must be a mapping")
    if "proxy" in raw and isinstance(raw["proxy"], dict):
        for secret_key in ("server", "username", "password"):
            if secret_key in raw["proxy"]:
                raise ConfigError(
                    f"{p}: proxy.{secret_key} must not be in config.yaml; put it in .env as QCOM_PROXY_{secret_key.upper()}"
                )
    try:
        cfg = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{p}: {exc}") from exc

    load_dotenv(env_path)
    server = os.environ.get("QCOM_PROXY_SERVER") or None
    if server:
        cfg.proxy.server = server
        cfg.proxy.username = os.environ.get("QCOM_PROXY_USERNAME") or None
        cfg.proxy.password = os.environ.get("QCOM_PROXY_PASSWORD") or None
        fallbacks = os.environ.get("QCOM_PROXY_FALLBACK_SERVERS", "")
        cfg.proxy.fallback_servers = [s.strip() for s in fallbacks.split(",") if s.strip()]
    return cfg
