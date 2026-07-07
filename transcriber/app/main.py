"""FastAPI application: upload API, job status polling, downloads, and the
static single-page frontend."""

import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, jobs, storage

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

MEDIA_TYPES = {"txt": "text/plain; charset=utf-8",
               "srt": "application/x-subrip"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    jobs.start_workers()
    yield
    jobs.shutdown_workers()


app = FastAPI(title="Transcriber", version="1.0.0", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "model_default": config.DEFAULT_MODEL,
            "s3": storage.s3_enabled()}


@app.post("/api/transcribe")
async def create_transcription(
    file: UploadFile = File(...),
    model: str = Form(default=""),
    language: str = Form(default=""),
    timestamps_in_txt: bool = Form(default=False),
) -> dict:
    model_size = (model or config.DEFAULT_MODEL).strip()
    if model_size not in config.ALLOWED_MODELS:
        raise HTTPException(400, f"unknown model '{model_size}'; "
                                 f"choose from {sorted(config.ALLOWED_MODELS)}")

    filename = Path(file.filename or "upload").name
    upload_path = config.UPLOAD_DIR / f"{uuid.uuid4().hex}-{filename}"

    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with upload_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        413, f"file exceeds the {config.MAX_UPLOAD_MB} MB limit")
                out.write(chunk)
    except HTTPException:
        upload_path.unlink(missing_ok=True)
        raise
    except Exception:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(500, "failed to store the upload")

    if written == 0:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(400, "the uploaded file is empty")

    job = jobs.submit(
        upload_path=upload_path,
        filename=filename,
        model_size=model_size,
        language=language.strip() or None,
        timestamps_in_txt=timestamps_in_txt,
    )
    return job.to_dict()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found (it may have expired)")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/download/{fmt}")
def download(job_id: str, fmt: str):
    if fmt not in MEDIA_TYPES:
        raise HTTPException(400, "format must be 'txt' or 'srt'")
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found (it may have expired)")
    if job.status != jobs.COMPLETED:
        raise HTTPException(409, f"job is {job.status}, not completed")

    filename = jobs.result_filename(job, fmt)
    path = storage.local_path(job_id, filename)
    if path.exists():
        return FileResponse(path, media_type=MEDIA_TYPES[fmt], filename=filename)

    # Local file gone (e.g. container recycled) — fall back to S3 if configured.
    url = storage.presigned_url(job_id, filename)
    if url:
        return RedirectResponse(url)
    raise HTTPException(410, "result files are no longer available")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def cli() -> None:
    """Entry point for `python -m app.main` during local development."""
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    cli()
