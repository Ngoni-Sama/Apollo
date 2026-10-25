"""The stable transcript JSON schema (see CLAUDE.md) and helpers to build/validate/store it."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict

import config

Platform = Literal["youtube", "tiktok"]
Method = Literal["captions", "asr"]

REQUIRED_KEYS = (
    "platform", "creator", "video_id", "source_url", "fetched_at", "method",
    "language", "duration_sec", "segments", "text",
)


class Segment(TypedDict):
    start: float
    end: float
    text: str


class TranscriptRecord(TypedDict):
    platform: Platform
    creator: str
    video_id: str
    source_url: str
    fetched_at: str
    method: Method
    language: str
    duration_sec: float
    segments: list[Segment]
    text: str


def join_segments(segments: list[Segment]) -> str:
    """Concatenate segment texts into one normalized string."""
    return re.sub(r"\s+", " ", " ".join(s["text"] for s in segments)).strip()


def make_record(
    *,
    platform: Platform,
    creator: str,
    video_id: str,
    source_url: str,
    method: Method,
    language: str,
    segments: list[Segment],
    duration_sec: float | None = None,
) -> TranscriptRecord:
    """Build a schema-conformant record. `creator` and `source_url` are mandatory (attribution)."""
    if not creator or not source_url:
        raise ValueError("creator and source_url are required for attribution")
    if duration_sec is None:
        duration_sec = max((s["end"] for s in segments), default=0.0)
    return TranscriptRecord(
        platform=platform,
        creator=creator,
        video_id=video_id,
        source_url=source_url,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        method=method,
        language=language,
        duration_sec=round(float(duration_sec), 2),
        segments=segments,
        text=join_segments(segments),
    )


def validate_record(record: dict) -> None:
    """Raise ValueError if `record` doesn't match the transcript schema."""
    missing = [k for k in REQUIRED_KEYS if k not in record]
    if missing:
        raise ValueError(f"missing keys: {missing}")
    if record["platform"] not in ("youtube", "tiktok"):
        raise ValueError(f"bad platform: {record['platform']!r}")
    if record["method"] not in ("captions", "asr"):
        raise ValueError(f"bad method: {record['method']!r}")
    if not record["creator"] or not record["source_url"]:
        raise ValueError("creator and source_url must be non-empty")
    for seg in record["segments"]:
        if set(seg) != {"start", "end", "text"}:
            raise ValueError(f"bad segment keys: {sorted(seg)}")


def safe_name(value: str) -> str:
    """Make a creator handle / id safe for use as a path component."""
    value = value.strip().lstrip("@")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value) or "_"


def transcript_path(platform: str, creator: str, video_id: str, *, raw: bool = False) -> Path:
    """data/transcripts/{platform}/{creator}/{video_id}.json (raw copies live under transcripts/raw/)."""
    base = config.RAW_TRANSCRIPTS_DIR if raw else config.TRANSCRIPTS_DIR
    return base / platform / safe_name(creator) / f"{safe_name(video_id)}.json"


def write_record(record: TranscriptRecord, path: Path) -> Path:
    """Validate and atomically write a record as pretty JSON."""
    validate_record(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path
