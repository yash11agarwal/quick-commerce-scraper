"""The run loop: plan jobs, run one worker per platform, retry per policy, resume, summarise.

Read docs/ARCHITECTURE.md section 2 for the plain-language version of what happens here.
"""

from __future__ import annotations

import subprocess
import threading
import time
import traceback
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from qcom import __version__
from qcom.core.browser import BrowserManager, ContextHandle, classify_playwright_error
from qcom.core.clock import new_run_id, now_utc
from qcom.core.config import AppConfig
from qcom.core.errors import ErrorCode, LocationNotSetError, QcomError, RunAbortedError, code_of
from qcom.core.location import make_expectation
from qcom.core.logging import get_logger
from qcom.core.models import EffectiveLocation, InputSpec, Job, JobStatus, LocationExpectation
from qcom.core.quality import finalise_listings
from qcom.core.retry import CircuitBreaker, RetryPolicy
from qcom.core.storage import Storage
from qcom.core.summary import RunSummary, build_summary
from qcom.core.throttle import HostThrottle
from qcom.platforms.base import PlatformAdapter
from qcom.platforms.registry import get_adapter_class, resolve_platforms

log = get_logger(__name__)


def git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


class NoBrowserPage:
    """Stands in for a Page when the adapter declares needs_browser = False."""

    def __init__(self, pincode: str) -> None:
        self.pincode = pincode


# ----------------------------------------------------------------------------- planning


def plan_run(
    cfg: AppConfig,
    spec: InputSpec,
    *,
    platforms: list[str] | None = None,
    max_results: int | None = None,
    label: str | None = None,
    run_id: str | None = None,
) -> str:
    """Create the run record and every job. Returns the run id. Nothing is fetched."""
    try:
        names = resolve_platforms(platforms if platforms is not None else spec.settings.platforms)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"platforms (settings sheet or --platforms): {exc}") from None
    n = max_results or spec.settings.max_results_per_query
    run_id = run_id or new_run_id()
    adapters = {name: get_adapter_class(name) for name in names}

    jobs: list[Job] = []
    for name in names:
        for pin in spec.pincodes:
            for prod in spec.products:
                jobs.append(
                    Job(
                        job_id=Job.make_id(run_id, name, pin.pincode, prod.input_row_id),
                        run_id=run_id,
                        platform=name,
                        requested_pincode=pin.pincode,
                        city=pin.city,
                        state=pin.state,
                        search_term=prod.product_name,
                        input_row_id=prod.input_row_id,
                        pincode_row_id=pin.input_row_id,
                        brand=prod.brand,
                        pack_size=prod.pack_size,
                        category=prod.category,
                        max_results=n,
                    )
                )

    with Storage(cfg.storage.path) as storage:
        storage.create_run(
            run_id,
            started_at=now_utc(),
            code_version=__version__,
            git_sha=git_sha(),
            config_hash=cfg.config_hash(),
            config_json=cfg.public_dict(),
            input_path=spec.source_path,
            input_sha256=spec.sha256,
            run_label=label if label is not None else spec.settings.run_label,
            proxy_label=cfg.proxy.label if cfg.proxy.configured else None,
            adapter_versions={name: cls.version for name, cls in adapters.items()},
        )
        storage.insert_jobs(jobs)
        for name in names:
            storage.set_platform_state(run_id, name, status="ACTIVE", reason=None, consecutive_failures=0)
    log.info("run.planned", run_id=run_id, jobs=len(jobs), platforms=names, max_results=n)
    return run_id


# ----------------------------------------------------------------------------- execution


@dataclass
class _Shared:
    cfg: AppConfig
    run_id: str
    throttle: HostThrottle
    policy: RetryPolicy
    run_dir: Path
    sessions_dir: Path
    abort: threading.Event
    abort_reason: list[str]


class PlatformRunner:
    """Owns one platform for one run: its breaker, its stop flag, and N context workers."""

    def __init__(self, shared: _Shared, platform: str, jobs: list[Job]) -> None:
        self.s = shared
        self.platform = platform
        self.jobs = jobs
        self.adapter_cls = get_adapter_class(platform)
        self.breaker = CircuitBreaker(shared.cfg.circuit_breaker.consecutive_failures)
        self.stopped = threading.Event()
        self.stop_reason: tuple[ErrorCode | None, str] | None = None
        self._stop_lock = threading.Lock()

    # -- platform-level stop -------------------------------------------------

    def stop_platform(self, storage: Storage, *, status: str, code: ErrorCode | None, reason: str) -> None:
        with self._stop_lock:
            if self.stopped.is_set():
                return
            self.stopped.set()
            self.stop_reason = (code, reason)
        storage.set_platform_state(self.s.run_id, self.platform, status=status, reason=reason, consecutive_failures=self.breaker.consecutive)
        skip_code = ErrorCode.SKIPPED_PLATFORM_BLOCKED if status == "STOPPED_BLOCKED" else code
        n = storage.skip_pending(self.s.run_id, self.platform, code=skip_code, reason=f"skipped: {reason}", at=now_utc())
        log.error("platform.stopped", platform=self.platform, status=status, reason=reason, skipped_jobs=n)

    # -- entry ----------------------------------------------------------------

    def run(self) -> None:
        groups: "OrderedDict[str, list[Job]]" = OrderedDict()
        for job in self.jobs:
            groups.setdefault(job.requested_pincode, []).append(job)
        q: Queue[list[Job]] = Queue()
        for g in groups.values():
            q.put(g)
        n_ctx = self.s.cfg.concurrency.contexts_for(self.platform)
        crashes: list[BaseException] = []

        def guarded(queue: "Queue[list[Job]]") -> None:
            try:
                self._context_worker(queue)
            except BaseException as exc:  # noqa: BLE001 - a dead worker thread must surface as a dead run, not silence
                crashes.append(exc)

        threads = [threading.Thread(target=guarded, args=(q,), name=f"{self.platform}-{i}", daemon=True) for i in range(n_ctx)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if crashes:
            raise crashes[0]

    def _context_worker(self, q: "Queue[list[Job]]") -> None:
        storage = Storage(self.s.cfg.storage.path)
        adapter = self.adapter_cls(navigation_timeout_s=self.s.cfg.browser.navigation_timeout_s)
        browser: BrowserManager | None = None
        try:
            if adapter.needs_browser:
                browser = BrowserManager(self.s.cfg.browser, self.s.cfg.proxy, self.s.sessions_dir)
                try:
                    browser.start()
                except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
                    code = classify_playwright_error(exc) or ErrorCode.UNKNOWN
                    self.stop_platform(storage, status="STOPPED_BROWSER", code=code, reason=f"browser launch failed: {exc}")
                    return
            while not self.stopped.is_set() and not self.s.abort.is_set():
                try:
                    group = q.get_nowait()
                except Empty:
                    return
                try:
                    self._run_group(storage, adapter, browser, group)
                except RunAbortedError as exc:
                    self.s.abort_reason.append(str(exc))
                    self.s.abort.set()
                    self.stop_platform(storage, status="STOPPED_RUN_ABORTED", code=exc.code, reason=f"run aborted: {exc}")
                    return
            if self.s.abort.is_set() and not self.stopped.is_set():
                self.stop_platform(storage, status="STOPPED_RUN_ABORTED", code=None, reason="run aborted: " + "; ".join(self.s.abort_reason))
        finally:
            if browser is not None:
                browser.close()
            storage.close()

    # -- one pincode group ------------------------------------------------------

    def _run_group(self, storage: Storage, adapter: PlatformAdapter, browser: BrowserManager | None, group: list[Job]) -> None:
        first = group[0]
        handle, page, location, failure = self._locate(storage, adapter, browser, first)
        if location is None:
            self._fail_group(storage, group, failure)
            return
        try:
            for job in group:
                if self.stopped.is_set() or self.s.abort.is_set():
                    return
                status = self._run_job(storage, adapter, browser, handle, page, job, location)
                tripped = self.breaker.record(failed=(status == JobStatus.FAILED))
                if tripped:
                    self.stop_platform(
                        storage, status="STOPPED_CIRCUIT", code=None,
                        reason=f"circuit breaker: {self.breaker.consecutive} consecutive failures",
                    )
        finally:
            if handle is not None and browser is not None:
                browser.close_context(handle)

    def _fail_group(self, storage: Storage, group: list[Job], failure: tuple[ErrorCode, str, str | None]) -> None:
        """Every job in the pincode group fails with the location's code. It counts once toward the breaker:
        an unserviceable pincode is one event, not one per search term."""
        code, reason, artifact = failure
        at = now_utc()
        if self.stopped.is_set():
            return
        for job in group:
            attempt_no = storage.start_attempt(job.job_id, at)
            storage.close_attempt(job.job_id, attempt_no, finished_at=at, outcome="FAILED", error_code=code, error_message=reason, artifact_path=artifact)
            storage.finish_job(
                job, status=JobStatus.FAILED, finished_at=at, code=code, reason=reason, strategy=None, location=None,
                listings=[], dq_events=[], duration_ms=0, artifact_path=artifact,
            )
        if self.breaker.record(failed=True):
            self.stop_platform(storage, status="STOPPED_CIRCUIT", code=None, reason=f"circuit breaker: {self.breaker.consecutive} consecutive failures")

    # -- location with its own retry policy ----------------------------------------

    def _locate(
        self, storage: Storage, adapter: PlatformAdapter, browser: BrowserManager | None, job: Job
    ) -> tuple[ContextHandle | None, Any, EffectiveLocation | None, tuple[ErrorCode, str, str | None]]:
        pincode = job.requested_pincode
        expectation: LocationExpectation = make_expectation(pincode, job.city, job.state)
        attempts = 0
        excluded: list[str] = []
        while True:
            attempts += 1
            handle: ContextHandle | None = None
            page: Any
            if browser is not None:
                handle = browser.new_context(self.platform, pincode, use_jar=(attempts == 1))
                page = handle.page
            else:
                page = NoBrowserPage(pincode)
            artifact: str | None = None
            try:
                self.s.throttle.wait_all(adapter.hosts)
                loc = adapter.set_location(page, pincode, expectation)
                if loc.effective_pincode != pincode:
                    raise LocationNotSetError(
                        f"readback does not carry the requested pincode: effective={loc.effective_pincode!r} address={loc.address_text!r}",
                        detail={"evidence": loc.evidence},
                    )
                if handle is not None and browser is not None:
                    browser.save_session(handle)
                log.info("location.verified", platform=self.platform, pincode=pincode, store=loc.store_id, eta=loc.eta_minutes, from_jar=bool(handle and handle.loaded_from_jar))
                return handle, page, loc, (ErrorCode.UNKNOWN, "", None)
            except Exception as exc:  # noqa: BLE001 - classified, recorded, retried per policy
                code = self._classify(adapter, exc)
                message = f"{type(exc).__name__}: {exc}"
                if handle is not None and browser is not None:
                    artifact = browser.save_artifacts(handle, self.s.run_dir / "artifacts" / f"location_{self.platform}_{pincode}_attempt{attempts}")
                    browser.close_context(handle)
                    if handle.loaded_from_jar:
                        browser.discard_session(self.platform, pincode)
                if isinstance(exc, QcomError):
                    chosen = exc.detail.get("chosen_suggestion")
                    if chosen:
                        excluded.append(str(chosen))
                        expectation = expectation.model_copy(update={"exclude_suggestions": tuple(excluded)})
                log.warning("location.failed", platform=self.platform, pincode=pincode, attempt=attempts, code=code.value, error=message)
                if code == ErrorCode.BLOCKED:
                    self.stop_platform(storage, status="STOPPED_BLOCKED", code=code, reason=message)
                    return None, None, None, (code, message, artifact)
                if self.s.policy.should_retry(code, attempts):
                    time.sleep(self.s.policy.backoff_seconds(code, attempts))
                    continue
                return None, None, None, (code, f"location failed after {attempts} attempt(s): {message}", artifact)

    # -- one job with its retry policy --------------------------------------------

    def _run_job(
        self,
        storage: Storage,
        adapter: PlatformAdapter,
        browser: BrowserManager | None,
        handle: ContextHandle | None,
        page: Any,
        job: Job,
        location: EffectiveLocation,
    ) -> JobStatus:
        proxy_attempts = 0
        while True:
            started = now_utc()
            t0 = time.monotonic()
            attempt_no = storage.start_attempt(job.job_id, started)
            capture_ids: list[str] = []
            try:
                self.s.throttle.wait_all(adapter.hosts)
                captures = adapter.search(page, job.search_term, job.max_results)
                storage.save_captures(self.s.run_id, job.job_id, attempt_no, captures)  # raw before parse, always
                capture_ids = [c.capture_id for c in captures if c.capture_id]
                parsed = [(c, adapter.parse(c)) for c in captures if c.parse]
                previous_run = storage.previous_run_id(self.s.run_id)

                def previous_price(platform: str, pincode: str, product_id: str) -> tuple[str, int] | None:
                    if previous_run is None:
                        return None
                    return storage.previous_selling_price(platform, pincode, product_id, before_run_id=self.s.run_id)

                listings, dq_events = finalise_listings(
                    job, parsed, location, previous_price=previous_price, price_move_warn_pct=self.s.cfg.quality.price_move_warn_pct
                )
                strategy = next((c.strategy for c in captures if c.parse), None)
                finished = now_utc()
                if listings:
                    status, code, reason = JobStatus.OK, None, None
                else:
                    status, code, reason = JobStatus.NO_RESULTS, ErrorCode.NO_RESULTS, "platform returned a well-formed empty result"
                storage.close_attempt(job.job_id, attempt_no, finished_at=finished, outcome=status.value, error_code=code)
                storage.finish_job(
                    job, status=status, finished_at=finished, code=code, reason=reason, strategy=strategy, location=location,
                    listings=listings, dq_events=dq_events, duration_ms=int((time.monotonic() - t0) * 1000),
                    artifact_path=("captures=" + ",".join(capture_ids)) if capture_ids else None,
                )
                log.info("job.done", platform=self.platform, pincode=job.requested_pincode, term=job.search_term, status=status.value, rows=len(listings), attempt=attempt_no, strategy=strategy)
                return status
            except Exception as exc:  # noqa: BLE001 - every exception is classified and recorded
                code = self._classify(adapter, exc)
                message = f"{type(exc).__name__}: {exc}"
                tb = traceback.format_exc()
                artifact: str | None = None
                if handle is not None and browser is not None:
                    artifact = browser.save_artifacts(handle, self.s.run_dir / "artifacts" / f"{job.job_id.replace(':', '_')}_attempt{attempt_no}")
                finished = now_utc()
                storage.close_attempt(job.job_id, attempt_no, finished_at=finished, outcome="FAILED", error_code=code, error_message=message, traceback_text=tb, artifact_path=artifact)
                ref_parts = [f"captures={','.join(capture_ids)}" if capture_ids else "", f"screenshot={artifact}" if artifact else ""]
                ref = "; ".join(p for p in ref_parts if p) or None
                log.warning("job.attempt_failed", platform=self.platform, pincode=job.requested_pincode, term=job.search_term, attempt=attempt_no, code=code.value, error=message)

                def _fail(final_reason: str) -> JobStatus:
                    storage.finish_job(
                        job, status=JobStatus.FAILED, finished_at=finished, code=code, reason=final_reason, strategy=None,
                        location=location, listings=[], dq_events=[], duration_ms=int((time.monotonic() - t0) * 1000), artifact_path=ref,
                    )
                    return JobStatus.FAILED

                if code == ErrorCode.BLOCKED:
                    result = _fail(message)
                    self.stop_platform(storage, status="STOPPED_BLOCKED", code=code, reason=message)
                    return result

                if code == ErrorCode.PROXY_ERROR:
                    proxy_attempts += 1
                    if self.s.policy.should_retry(code, proxy_attempts):
                        time.sleep(self.s.policy.backoff_seconds(code, proxy_attempts))
                        continue
                    if browser is not None and browser.rotate_proxy():
                        proxy_attempts = 0
                        continue
                    _fail(f"proxy failed after {proxy_attempts} attempt(s) and no fallback proxy is configured: {message}")
                    raise RunAbortedError(f"proxy error on {self.platform}: {message}", code=ErrorCode.PROXY_ERROR)

                if self.s.policy.should_retry(code, attempt_no):
                    time.sleep(self.s.policy.backoff_seconds(code, attempt_no))
                    continue

                result = _fail(message if attempt_no == 1 else f"failed after {attempt_no} attempts: {message}")
                if code == ErrorCode.RATE_LIMITED:
                    self.stop_platform(storage, status="STOPPED_RATE_LIMIT", code=code, reason=f"rate limited after {attempt_no} attempts: {message}")
                return result

    @staticmethod
    def _classify(adapter: PlatformAdapter, exc: BaseException) -> ErrorCode:
        code = code_of(exc)
        if code is not None:
            return code
        try:
            code = adapter.classify_failure(exc)
        except Exception as classify_exc:  # noqa: BLE001 - a broken classifier must not hide the original failure
            log.error("classify_failure.raised", error=str(classify_exc))
            code = None
        return code or classify_playwright_error(exc) or ErrorCode.UNKNOWN


# ----------------------------------------------------------------------------- run / resume


def execute_run(cfg: AppConfig, run_id: str, *, started_at: Any | None = None) -> RunSummary:
    """Run every pending job of ``run_id``. Safe to call again after a crash (that is what resume does)."""
    started = started_at or now_utc()
    run_dir = Path(cfg.storage.runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    shared = _Shared(
        cfg=cfg,
        run_id=run_id,
        throttle=HostThrottle(cfg.throttle.min_gap_s, cfg.throttle.jitter_s),
        policy=RetryPolicy(cfg.retry),
        run_dir=run_dir,
        sessions_dir=Path(cfg.storage.sessions_dir),
        abort=threading.Event(),
        abort_reason=[],
    )
    with Storage(cfg.storage.path) as storage:
        run = storage.get_run(run_id)
        if run is None:
            raise KeyError(f"run {run_id} not found in {cfg.storage.path}")
        pending = storage.pending_jobs(run_id)
    by_platform: "OrderedDict[str, list[Job]]" = OrderedDict()
    for job in pending:
        by_platform.setdefault(job.platform, []).append(job)
    log.info("run.start", run_id=run_id, pending=len(pending), platforms=list(by_platform))

    runners = [PlatformRunner(shared, name, jobs) for name, jobs in by_platform.items()]
    if runners:
        with ThreadPoolExecutor(max_workers=min(len(runners), cfg.concurrency.max_platforms_in_parallel), thread_name_prefix="platform") as pool:
            futures = [pool.submit(r.run) for r in runners]
            for f in futures:
                f.result()  # re-raises a worker crash instead of hiding it

    ended = now_utc()
    with Storage(cfg.storage.path) as storage:
        summary = build_summary(storage, run_id, cfg, started_at=started, ended_at=ended, aborted=shared.abort.is_set())
        storage.finish_run(run_id, ended_at=ended, status=summary.run_status, exit_code=summary.exit_code, summary=summary.as_dict())
    return summary


def resume_run(cfg: AppConfig, run_id: str) -> RunSummary:
    with Storage(cfg.storage.path) as storage:
        if storage.get_run(run_id) is None:
            raise KeyError(f"run {run_id} not found in {cfg.storage.path}")
        reset = storage.reset_in_progress(run_id)
        pending = len(storage.pending_jobs(run_id))
    log.info("run.resume", run_id=run_id, reset_in_progress=reset, pending=pending)
    return execute_run(cfg, run_id)
