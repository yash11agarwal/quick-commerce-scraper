"""Asynchronous job pipeline: upload -> ffmpeg normalize -> whisper -> txt/srt.

Jobs run on a bounded thread pool so the API stays responsive while a
transcription is in flight. State is kept in memory and result files on disk
(and S3 when configured) — for a multi-instance AWS deployment put an ALB with
sticky sessions in front, or swap this module for an SQS + DynamoDB pipeline
(see the README's scaling section).
"""

import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config, formatters, media, storage, transcriber

log = logging.getLogger(__name__)

QUEUED = "queued"
PROCESSING = "processing"
COMPLETED = "completed"
FAILED = "failed"


@dataclass
class Job:
    id: str
    filename: str
    model_size: str
    language: Optional[str]
    timestamps_in_txt: bool
    status: str = QUEUED
    progress: float = 0.0
    stage: str = "waiting in queue"
    error: Optional[str] = None
    language_detected: Optional[str] = None
    duration: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "model": self.model_size,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "error": self.error,
            "language": self.language_detected,
            "duration": self.duration,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "outputs": ["txt", "srt"] if self.status == COMPLETED else [],
        }


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_executor: Optional[ThreadPoolExecutor] = None


def start_workers() -> None:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=config.WORKER_CONCURRENCY,
            thread_name_prefix="transcribe",
        )
        threading.Thread(target=_janitor, daemon=True, name="janitor").start()


def shutdown_workers() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


def submit(upload_path: Path, filename: str, model_size: str,
           language: Optional[str], timestamps_in_txt: bool) -> Job:
    job = Job(
        id=uuid.uuid4().hex[:12],
        filename=filename,
        model_size=model_size,
        language=language,
        timestamps_in_txt=timestamps_in_txt,
    )
    with _jobs_lock:
        _jobs[job.id] = job
    assert _executor is not None, "workers not started"
    _executor.submit(_run, job, upload_path)
    return job


def get(job_id: str) -> Optional[Job]:
    with _jobs_lock:
        return _jobs.get(job_id)


def _run(job: Job, upload_path: Path) -> None:
    wav_path = upload_path.with_suffix(".norm.wav")
    try:
        job.status = PROCESSING

        job.stage = "analyzing media"
        info = media.probe(upload_path)
        job.duration = info["duration"] or None

        job.stage = "extracting audio"
        media.extract_audio(upload_path, wav_path)

        job.stage = "transcribing"

        def on_progress(fraction: float) -> None:
            job.progress = fraction

        result = transcriber.transcribe(
            wav_path,
            model_size=job.model_size,
            language=job.language,
            on_progress=on_progress,
        )
        job.language_detected = result["language"]
        job.duration = result["duration"] or job.duration

        job.stage = "writing outputs"
        base = Path(job.filename).stem or "transcript"
        storage.save_result(job.id, f"{base}.txt",
                            formatters.to_txt(result["segments"],
                                              with_timestamps=job.timestamps_in_txt))
        storage.save_result(job.id, f"{base}.srt",
                            formatters.to_srt(result["segments"]))

        job.progress = 1.0
        job.stage = "done"
        job.status = COMPLETED
    except media.MediaError as exc:
        job.status = FAILED
        job.error = str(exc)
    except Exception as exc:
        log.exception("job %s failed", job.id)
        job.status = FAILED
        job.error = f"internal error: {exc}"
    finally:
        job.finished_at = time.time()
        for path in (upload_path, wav_path):
            path.unlink(missing_ok=True)


def result_filename(job: Job, fmt: str) -> str:
    base = Path(job.filename).stem or "transcript"
    return f"{base}.{fmt}"


def _janitor() -> None:
    """Purge jobs and their result files after JOB_TTL_HOURS."""
    ttl = config.JOB_TTL_HOURS * 3600
    while True:
        time.sleep(600)
        cutoff = time.time() - ttl
        with _jobs_lock:
            expired = [j for j in _jobs.values()
                       if j.finished_at and j.finished_at < cutoff]
            for job in expired:
                _jobs.pop(job.id, None)
        for job in expired:
            shutil.rmtree(config.RESULTS_DIR / job.id, ignore_errors=True)
