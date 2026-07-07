"""Speech-to-text engine built on faster-whisper (CTranslate2 Whisper).

Models are loaded lazily and cached per size, so the first request for a given
model pays the download/load cost and subsequent requests reuse it.
"""

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from faster_whisper import WhisperModel

from . import config

log = logging.getLogger(__name__)

_models: dict[str, WhisperModel] = {}
_models_lock = threading.Lock()


def get_model(size: str) -> WhisperModel:
    with _models_lock:
        model = _models.get(size)
        if model is None:
            log.info("loading whisper model %s (device=%s, compute=%s)",
                     size, config.DEVICE, config.COMPUTE_TYPE)
            model = WhisperModel(size, device=config.DEVICE,
                                 compute_type=config.COMPUTE_TYPE)
            _models[size] = model
        return model


def transcribe(
    wav_path: Path,
    model_size: str,
    language: Optional[str] = None,
    on_progress: Optional[Callable[[float], None]] = None,
) -> dict:
    """Transcribe a 16 kHz mono WAV file.

    Returns {"segments": [{start, end, text}, ...], "language": str,
    "language_probability": float, "duration": float}.
    `on_progress` receives a 0.0-1.0 fraction as segments are decoded.
    """
    model = get_model(model_size)
    segments_iter, info = model.transcribe(
        str(wav_path),
        language=language or None,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=True,
    )

    duration = float(info.duration or 0.0)
    segments = []
    for seg in segments_iter:
        segments.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text,
        })
        if on_progress and duration > 0:
            on_progress(min(float(seg.end) / duration, 1.0))

    return {
        "segments": segments,
        "language": info.language,
        "language_probability": float(info.language_probability or 0.0),
        "duration": duration,
    }
