"""YouTube captions via youtube-transcript-api (v1.x instance API only)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    RequestBlocked,
    YouTubeTranscriptApi,
)

from schema import Segment

log = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class CaptionsError(Exception):
    """Captions could not be fetched; the caller should fall back to ASR."""


class CaptionsBlocked(CaptionsError):
    """YouTube is blocking this IP. Retrying from the same IP will not help."""


@dataclass
class CaptionResult:
    video_id: str
    language: str
    is_generated: bool
    segments: list[Segment]


def extract_video_id(url_or_id: str) -> str:
    """Return the 11-char video id from a watch/shorts/live/youtu.be URL, or the id itself."""
    candidate = url_or_id.strip()
    if _ID_RE.match(candidate):
        return candidate
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if host.endswith("youtu.be"):
        vid = parsed.path.lstrip("/").split("/")[0]
    elif "youtube" in host:
        if parsed.path == "/watch":
            vid = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [p for p in parsed.path.split("/") if p]
            vid = parts[1] if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v") else ""
    else:
        vid = ""
    if not _ID_RE.match(vid):
        raise ValueError(f"not a YouTube video URL or id: {url_or_id!r}")
    return vid


def fetch_captions(
    video_id: str,
    languages: tuple[str, ...] = ("en",),
    api: YouTubeTranscriptApi | None = None,
) -> CaptionResult:
    """Fetch captions for one video. Raises CaptionsBlocked or CaptionsError; never retries."""
    api = api or YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=languages)
    except RequestBlocked as exc:  # includes IpBlocked
        raise CaptionsBlocked(
            f"YouTube blocked the caption request for {video_id} ({type(exc).__name__}). "
            "Your IP is rate-limited or is a cloud/datacenter IP; retrying will not help."
        ) from exc
    except CouldNotRetrieveTranscript as exc:
        raise CaptionsError(f"no captions for {video_id}: {type(exc).__name__}") from exc

    segments: list[Segment] = []
    for snip in fetched.to_raw_data():
        text = snip["text"].replace("\n", " ").strip()
        if not text:
            continue
        start = float(snip["start"])
        segments.append(Segment(start=round(start, 3), end=round(start + float(snip["duration"]), 3), text=text))
    log.info("captions: %s lang=%s generated=%s segments=%d",
             video_id, fetched.language_code, fetched.is_generated, len(segments))
    return CaptionResult(video_id, fetched.language_code, fetched.is_generated, segments)
