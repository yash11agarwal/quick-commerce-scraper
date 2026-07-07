# Transcriber — CPU image suitable for ECS Fargate / App Runner / EC2.
# For GPU (large-v3 at speed), base off nvidia/cuda:12.2.2-cudnn8-runtime and
# set WHISPER_DEVICE=cuda WHISPER_COMPUTE_TYPE=float16.

FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/transcriber

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

# Model weights are downloaded on first use and cached here; mount a volume
# (or EFS) at /models to avoid re-downloading on every container start.
ENV HF_HOME=/models \
    DATA_DIR=/data \
    WHISPER_MODEL=small \
    WHISPER_COMPUTE_TYPE=int8 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /models /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
