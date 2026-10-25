"""TikTok via davidteather/TikTok-Api. Isolated so it can be swapped when TikTok breaks it.

This library breaks whenever TikTok changes its web front-end. On failure, report it and
use the yt-dlp fallback — do not patch the library inline.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import Settings, load_settings

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"tiktok\.com/@(?P<user>[^/?#]+)/(?:video|photo)/(?P<id>\d+)")


class TikTokFetchError(Exception):
    """TikTok-Api failed (bot detection, empty response, front-end change, ...)."""


@dataclass
class TikTokVideo:
    video_id: str
    username: str
    url: str
    desc: str
    create_time: int | None
    duration_sec: float | None
    path: Path | None = None


def parse_url(url: str) -> tuple[str, str]:
    """Return (username, video_id) from a canonical tiktok.com/@user/video/<id> URL."""
    m = _URL_RE.search(url)
    if not m:
        raise ValueError(f"not a canonical TikTok video URL: {url!r}")
    return m.group("user"), m.group("id")


def _video_from_dict(d: dict[str, Any], fallback_user: str = "") -> TikTokVideo:
    author = d.get("author")
    username = (author.get("uniqueId") if isinstance(author, dict) else author) or fallback_user
    vid = str(d.get("id", ""))
    duration = (d.get("video") or {}).get("duration")
    create_time = d.get("createTime")
    return TikTokVideo(
        video_id=vid,
        username=username,
        url=f"https://www.tiktok.com/@{username}/video/{vid}",
        desc=d.get("desc") or "",
        create_time=int(create_time) if create_time not in (None, "") else None,
        duration_sec=float(duration) if duration else None,
    )


async def _with_api(settings: Settings, fn):
    # Imported lazily: TikTokApi pulls in Playwright, which is heavy and not needed for YouTube-only runs.
    from TikTokApi import TikTokApi

    async with TikTokApi() as api:
        await api.create_sessions(
            num_sessions=1,
            headless=settings.tiktok_headless,
            ms_tokens=[settings.tiktok_ms_token] if settings.tiktok_ms_token else None,
            sleep_after=3,
            executable_path=str(settings.playwright_chromium_executable)
            if settings.playwright_chromium_executable else None,
            timeout=60_000,
        )
        return await fn(api)


def _run(settings: Settings, fn):
    from TikTokApi.exceptions import TikTokException

    try:
        return asyncio.run(_with_api(settings, fn))
    except TikTokException as exc:
        raise TikTokFetchError(f"TikTok-Api: {type(exc).__name__}: {exc}") from exc
    except Exception as exc:  # Playwright/network errors surface as many different types
        raise TikTokFetchError(f"TikTok-Api session failed: {type(exc).__name__}: {exc}") from exc


def download_video(url: str, out_dir: Path, settings: Settings | None = None) -> TikTokVideo:
    """Fetch metadata and the video file (mp4) for one TikTok URL."""
    settings = settings or load_settings()
    username, video_id = parse_url(url)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{video_id}.mp4"

    async def fetch(api):
        video = api.video(url=url)
        info = await video.info()
        data = await video.bytes()
        if not data:
            raise TikTokFetchError(f"empty video bytes for {url}")
        dest.write_bytes(data)
        return info

    info = _run(settings, fetch)
    meta = _video_from_dict(info, fallback_user=username)
    meta.path = dest
    log.info("tiktok: downloaded %s (%d bytes)", dest.name, dest.stat().st_size)
    return meta


def list_user_videos(username: str, limit: int = 500, settings: Settings | None = None) -> list[TikTokVideo]:
    """List a user's videos. BEST-EFFORT: TikTok rate-limits and may return a partial list."""
    settings = settings or load_settings()
    username = username.lstrip("@")

    async def fetch(api):
        out: list[TikTokVideo] = []
        try:
            async for video in api.user(username).videos(count=limit):
                out.append(_video_from_dict(video.as_dict, fallback_user=username))
        except Exception as exc:
            if not out:
                raise
            log.warning("tiktok: listing stopped early after %d videos: %s", len(out), exc)
        return out

    return _run(settings, fetch)
