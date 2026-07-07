# Transcriber

A full-stack, self-hosted transcription service. Upload **any audio or video
format** (anything ffmpeg can decode — MP4, MOV, MKV, WebM, MP3, WAV, M4A,
FLAC, OGG, Opus, 3GP, …) and get back a high-quality, timestamped transcript
in **both TXT and SRT**.

- **Engine**: [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  (OpenAI Whisper on CTranslate2) with voice-activity filtering and beam
  search for quality.
- **Backend**: FastAPI with an async job queue — uploads return immediately,
  the browser polls for progress.
- **Frontend**: zero-dependency single page (drag & drop, model/language
  selection, live progress bar, download buttons).
- **Outputs**: `transcript.srt` (subtitles with `HH:MM:SS,mmm` timestamps) and
  `transcript.txt` (clean text, optionally with `[HH:MM:SS]` line prefixes).
- **AWS-ready**: single Docker image, health check endpoint, optional S3
  persistence via IAM task role, all configuration by environment variable.

## Architecture

```
browser ── POST /api/transcribe (multipart) ──► FastAPI
                                                 │  saves upload, queues job
                                                 ▼
                                        worker thread pool
                                  ffmpeg → 16 kHz mono WAV
                                  faster-whisper → segments
                                  formatters → .txt + .srt
                                                 │
                              local disk (always) + S3 (optional)
browser ◄─ GET /api/jobs/{id} (poll) ── status/progress
browser ◄─ GET /api/jobs/{id}/download/{txt|srt}
```

## Run locally

```bash
cd transcriber
pip install -r requirements.txt
sudo apt install ffmpeg        # or brew install ffmpeg
uvicorn app.main:app --port 8000
# open http://localhost:8000
```

Or with Docker:

```bash
docker compose up --build
```

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/transcribe` | multipart form: `file` (required), `model`, `language`, `timestamps_in_txt` → returns a job |
| `GET` | `/api/jobs/{id}` | job status: `queued / processing / completed / failed`, progress 0–1, detected language, duration |
| `GET` | `/api/jobs/{id}/download/txt` | plain-text transcript |
| `GET` | `/api/jobs/{id}/download/srt` | SubRip subtitles |
| `GET` | `/api/health` | liveness probe for load balancers |

Example with `curl`:

```bash
JOB=$(curl -sF file=@interview.mp4 -F model=small http://localhost:8000/api/transcribe | jq -r .id)
watch curl -s http://localhost:8000/api/jobs/$JOB
curl -OJ http://localhost:8000/api/jobs/$JOB/download/srt
curl -OJ http://localhost:8000/api/jobs/$JOB/download/txt
```

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `WHISPER_MODEL` | `small` | default model: `tiny`, `base`, `small`, `medium`, `large-v3`, `distil-large-v3` |
| `WHISPER_DEVICE` | `auto` | `cpu`, `cuda`, or `auto` |
| `WHISPER_COMPUTE_TYPE` | `int8` | `int8` (CPU), `float16` (GPU) |
| `WORKER_CONCURRENCY` | `1` | simultaneous transcriptions per container |
| `MAX_UPLOAD_MB` | `2048` | upload size cap |
| `DATA_DIR` | `/tmp/transcriber-data` | uploads + results directory (`/data` in Docker) |
| `S3_BUCKET` | *(unset)* | persist transcripts to S3 and serve via presigned URLs |
| `S3_PREFIX` | `transcripts` | key prefix inside the bucket |
| `JOB_TTL_HOURS` | `24` | when finished jobs and their files are purged |
| `HF_HOME` | `/models` (Docker) | model weight cache — mount a volume/EFS here |

## Deploying on AWS

### Option A — ECS Fargate (recommended starting point)

1. **Build & push the image**
   ```bash
   aws ecr create-repository --repository-name transcriber
   aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
   docker build -t <acct>.dkr.ecr.<region>.amazonaws.com/transcriber:latest transcriber/
   docker push <acct>.dkr.ecr.<region>.amazonaws.com/transcriber:latest
   ```
2. **Create an S3 bucket** for transcripts and give the ECS *task role*
   `s3:PutObject` / `s3:GetObject` on it. Set `S3_BUCKET` in the task
   definition — no keys in the image.
3. **Task sizing**: Whisper is CPU-bound. For the `small` model, 2 vCPU /
   4 GB transcribes roughly in real time; use 4 vCPU / 8 GB for `medium`.
   Set `WHISPER_MODEL` accordingly.
4. **Service**: run the task behind an **Application Load Balancer**, health
   check path `/api/health`, idle timeout ≥ 120 s (large uploads). Enable
   **sticky sessions** if you run more than one task, because job state is
   per-container (see *Scaling out* below).
5. Optional: mount **EFS at `/models`** so model weights download once, not on
   every task start.

### Option B — App Runner (simplest)

App Runner can deploy straight from the ECR image: set port `8000`, health
check `/api/health`, 2 vCPU / 4 GB, and the same environment variables.
Note App Runner requests time out at ~120 s — uploads are fine (the API
returns immediately and work continues in the background), but keep
`MAX_UPLOAD_MB` modest or move uploads to S3 presigned PUTs at higher scale.

### Option C — EC2 with GPU (fastest, best quality)

On a `g4dn.xlarge` (T4 GPU), `large-v3` runs ~10× real time:

```bash
docker run -d --gpus all -p 80:8000 \
  -e WHISPER_MODEL=large-v3 -e WHISPER_DEVICE=cuda -e WHISPER_COMPUTE_TYPE=float16 \
  -v /opt/models:/models <image>
```

(Use an NVIDIA CUDA base image as noted at the top of the `Dockerfile`.)

### Scaling out

The built-in queue is in-process, which is perfect for one container and
fine for a small team behind sticky sessions. For heavy multi-tenant load,
the seams are already in place to split it:

- Upload directly to S3 with presigned PUT URLs (skip the API hop),
- replace `app/jobs.py`'s thread pool with an **SQS** queue + a separate
  worker service (same `media.py`/`transcriber.py`/`formatters.py` code),
- keep job state in **DynamoDB** instead of the in-memory dict.

## Quality notes

- `large-v3` is the most accurate model; `distil-large-v3` is ~6× faster with
  near-parity for English. `small` is the best CPU/quality trade-off.
- VAD filtering is on by default: silence and music are skipped, which
  removes most hallucinated text.
- Language auto-detection works well, but pinning `language` (e.g. `hi`, `en`)
  helps short or noisy clips.

## Tests

```bash
cd transcriber
python -m pytest tests/ -v
```
