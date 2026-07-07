"""Media handling via ffmpeg: accept any audio/video container or codec and
normalize it to 16 kHz mono WAV, which is what Whisper expects."""

import json
import subprocess
from pathlib import Path


class MediaError(Exception):
    """Raised when ffmpeg/ffprobe cannot decode the uploaded file."""


def probe(path: Path) -> dict:
    """Return media metadata (duration, codecs) or raise MediaError."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,format_name",
        "-show_entries", "stream=codec_type,codec_name",
        "-of", "json", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise MediaError("ffprobe is not installed on this host")
    except subprocess.TimeoutExpired:
        raise MediaError("timed out while probing the media file")

    if out.returncode != 0:
        raise MediaError(f"unrecognized media file: {out.stderr.strip()[:500]}")

    info = json.loads(out.stdout or "{}")
    streams = info.get("streams", [])
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise MediaError("the file contains no audio stream to transcribe")

    duration = float(info.get("format", {}).get("duration") or 0.0)
    return {
        "duration": duration,
        "format": info.get("format", {}).get("format_name", ""),
        "has_video": any(s.get("codec_type") == "video" for s in streams),
    }


def extract_audio(src: Path, dst: Path) -> None:
    """Decode any input format to 16 kHz mono PCM WAV at `dst`."""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(src),
        "-vn",                # drop video
        "-ac", "1",           # mono
        "-ar", "16000",       # 16 kHz
        "-c:a", "pcm_s16le",  # 16-bit PCM
        str(dst),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError:
        raise MediaError("ffmpeg is not installed on this host")
    except subprocess.TimeoutExpired:
        raise MediaError("timed out while extracting audio")

    if out.returncode != 0 or not dst.exists():
        raise MediaError(f"could not decode audio: {out.stderr.strip()[:500]}")
