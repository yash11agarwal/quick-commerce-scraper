"""Result storage: local disk always; S3 additionally when S3_BUCKET is set.

On AWS you would typically run the container with an IAM task role that grants
s3:PutObject/s3:GetObject on the bucket — no credentials in the image.
"""

import logging
from pathlib import Path
from typing import Optional

from . import config

log = logging.getLogger(__name__)

_s3_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client("s3")
    return _s3_client


def s3_enabled() -> bool:
    return bool(config.S3_BUCKET)


def _s3_key(job_id: str, filename: str) -> str:
    prefix = config.S3_PREFIX.strip("/")
    return f"{prefix}/{job_id}/{filename}" if prefix else f"{job_id}/{filename}"


def save_result(job_id: str, filename: str, content: str) -> Path:
    """Write a result file locally and mirror it to S3 when configured."""
    job_dir = config.RESULTS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / filename
    path.write_text(content, encoding="utf-8")

    if s3_enabled():
        try:
            _s3().put_object(
                Bucket=config.S3_BUCKET,
                Key=_s3_key(job_id, filename),
                Body=content.encode("utf-8"),
                ContentType="text/plain; charset=utf-8",
            )
        except Exception:
            log.exception("failed to upload %s/%s to s3", job_id, filename)

    return path


def local_path(job_id: str, filename: str) -> Path:
    return config.RESULTS_DIR / job_id / filename


def presigned_url(job_id: str, filename: str) -> Optional[str]:
    """Presigned S3 GET URL for a result, or None when S3 is not configured."""
    if not s3_enabled():
        return None
    try:
        return _s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": config.S3_BUCKET, "Key": _s3_key(job_id, filename)},
            ExpiresIn=config.S3_PRESIGN_EXPIRY,
        )
    except Exception:
        log.exception("failed to presign %s/%s", job_id, filename)
        return None
