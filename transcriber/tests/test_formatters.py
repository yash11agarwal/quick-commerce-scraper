import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.formatters import readable_timestamp, srt_timestamp, to_srt, to_txt

SEGMENTS = [
    {"start": 0.0, "end": 2.5, "text": " Hello there."},
    {"start": 2.5, "end": 5.04, "text": " Welcome to the show."},
    {"start": 3661.2, "end": 3663.9, "text": " One hour later."},
]


def test_srt_timestamp_formatting():
    assert srt_timestamp(0) == "00:00:00,000"
    assert srt_timestamp(2.5) == "00:00:02,500"
    assert srt_timestamp(5.0449) == "00:00:05,045"
    assert srt_timestamp(3661.2) == "01:01:01,200"
    assert srt_timestamp(-1) == "00:00:00,000"


def test_readable_timestamp():
    assert readable_timestamp(0) == "00:00:00"
    assert readable_timestamp(75) == "00:01:15"
    assert readable_timestamp(3661.9) == "01:01:01"


def test_to_srt_structure():
    srt = to_srt(SEGMENTS)
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == 3
    assert blocks[0] == "1\n00:00:00,000 --> 00:00:02,500\nHello there."
    assert blocks[2].startswith("3\n01:01:01,200 --> 01:01:03,900")
    assert srt.endswith("\n")


def test_to_srt_skips_empty_segments():
    srt = to_srt([{"start": 0, "end": 1, "text": "  "}])
    assert srt == ""


def test_to_txt_plain_and_timestamped():
    plain = to_txt(SEGMENTS)
    assert plain == "Hello there.\nWelcome to the show.\nOne hour later.\n"

    stamped = to_txt(SEGMENTS, with_timestamps=True)
    assert stamped.splitlines()[0] == "[00:00:00] Hello there."
    assert stamped.splitlines()[2] == "[01:01:01] One hour later."
