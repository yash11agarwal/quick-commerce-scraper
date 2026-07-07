"""Turn transcription segments into deliverable formats: SRT and TXT."""

from typing import Iterable


def srt_timestamp(seconds: float) -> str:
    """Format seconds as an SRT timestamp: HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def readable_timestamp(seconds: float) -> str:
    """Format seconds as [HH:MM:SS] for the timestamped TXT output."""
    if seconds < 0:
        seconds = 0.0
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def to_srt(segments: Iterable[dict]) -> str:
    """Build an SRT document from segments of {start, end, text}."""
    blocks = []
    for i, seg in enumerate(segments, start=1):
        text = seg["text"].strip()
        if not text:
            continue
        blocks.append(
            f"{i}\n{srt_timestamp(seg['start'])} --> {srt_timestamp(seg['end'])}\n{text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_txt(segments: Iterable[dict], with_timestamps: bool = False) -> str:
    """Build a plain-text transcript; optionally prefix each line with [HH:MM:SS]."""
    lines = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if with_timestamps:
            lines.append(f"[{readable_timestamp(seg['start'])}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines) + ("\n" if lines else "")
