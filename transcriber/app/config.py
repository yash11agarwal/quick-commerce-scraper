"""Application configuration, driven entirely by environment variables so the
same image runs unchanged on a laptop, in docker-compose, or on AWS."""

import os
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Where uploads and results live. On AWS, mount an EFS volume here or leave it
# ephemeral and set S3_BUCKET so results are persisted to S3.
DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/transcriber-data"))
UPLOAD_DIR = DATA_DIR / "uploads"
RESULTS_DIR = DATA_DIR / "results"

# Whisper model to load: tiny, base, small, medium, large-v3, distil-large-v3.
# "small" is a good CPU default; use "large-v3" on GPU for best quality.
DEFAULT_MODEL = os.environ.get("WHISPER_MODEL", "small")
ALLOWED_MODELS = {"tiny", "base", "small", "medium", "large-v3", "distil-large-v3"}

# int8 is the fastest CPU compute type with near-identical quality.
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
DEVICE = os.environ.get("WHISPER_DEVICE", "auto")  # auto -> cuda if available

# Upload cap in megabytes (2 GB default).
MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 2048)

# How many transcriptions run concurrently. Whisper saturates CPU cores, so 1
# is right for most single-container deployments; scale out horizontally.
WORKER_CONCURRENCY = _env_int("WORKER_CONCURRENCY", 1)

# Optional S3 persistence. When set, finished transcripts are uploaded to
# s3://$S3_BUCKET/$S3_PREFIX/<job_id>/ and downloads redirect to presigned URLs.
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_PREFIX = os.environ.get("S3_PREFIX", "transcripts")
S3_PRESIGN_EXPIRY = _env_int("S3_PRESIGN_EXPIRY", 3600)

# Jobs older than this are purged from disk and memory by the janitor.
JOB_TTL_HOURS = _env_int("JOB_TTL_HOURS", 24)


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
