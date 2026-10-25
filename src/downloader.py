"""yt-dlp wrapper: audio-only downloads, metadata, and flat channel listing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError

from config import Settings, load_settings

log = logging.getLogger(__name__)


class DownloadFailed(Exception):
    """yt-dlp could not fetch the media. Try `pip install -U yt-dlp` before changing code."""

    @property
    def is_block(self) -> bool:
        """True when the platform is refusing this IP/client rather than this particular video."""
        msg = str(self).lower()
        return any(p in msg for p in _BLOCK_MARKERS)


_BLOCK_MARKERS = (
    "sign in to confirm you", "http error 429", "too many requests",
    "ip address is blocked", "your ip", "rate-limit", "rate limit",
)


@dataclass
class AudioDownload:
    path: Path
    video_id: str
    title: str
    uploader: str
    duration_sec: float | None
    upload_date: str | None


def _base_opts(settings: Settings) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "logger": log,
        "js_runtimes": {name: {} for name in settings.ytdlp_js_runtimes},
    }
    if settings.ytdlp_cookies_file:
        opts["cookiefile"] = str(settings.ytdlp_cookies_file)
    elif settings.ytdlp_cookies_from_browser:
        opts["cookiesfrombrowser"] = (settings.ytdlp_cookies_from_browser,)
    return opts


def uploader_name(info: dict[str, Any]) -> str:
    """Prefer the stable handle (@name) over the display name."""
    handle = info.get("uploader_id") or info.get("channel_id") or ""
    if isinstance(handle, str) and handle.startswith("@"):
        return handle[1:]
    return info.get("uploader") or info.get("channel") or handle or ""


def fetch_info(url: str, settings: Settings | None = None) -> dict[str, Any]:
    """Metadata only (no download)."""
    settings = settings or load_settings()
    opts = _base_opts(settings) | {"skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise DownloadFailed(f"metadata fetch failed for {url}: {exc}") from exc


def download_audio(url: str, out_dir: Path, settings: Settings | None = None) -> AudioDownload:
    """Download audio only and convert to mp3 (equivalent to `yt-dlp -x --audio-format mp3`)."""
    settings = settings or load_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = _base_opts(settings) | {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        raise DownloadFailed(f"audio download failed for {url}: {exc}") from exc

    path = out_dir / f"{info['id']}.mp3"
    if not path.exists():
        raise DownloadFailed(f"yt-dlp reported success but {path} is missing")
    log.info("downloaded audio %s (%s)", path.name, info.get("title"))
    return AudioDownload(
        path=path,
        video_id=str(info["id"]),
        title=info.get("title") or "",
        uploader=uploader_name(info),
        duration_sec=info.get("duration"),
        upload_date=info.get("upload_date"),
    )


def list_flat(url: str, settings: Settings | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Flat playlist listing (equivalent to `yt-dlp --flat-playlist`). Returns raw entries."""
    settings = settings or load_settings()
    opts = _base_opts(settings) | {"extract_flat": "in_playlist", "skip_download": True}
    if limit:
        opts["playlistend"] = limit
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise DownloadFailed(f"listing failed for {url}: {exc}") from exc
    return [e for e in (info.get("entries") or []) if e]


def channel_id(url: str, settings: Settings | None = None) -> str:
    """Resolve a channel URL (e.g. https://www.youtube.com/@handle/videos) to its UC... channel id."""
    settings = settings or load_settings()
    opts = _base_opts(settings) | {"extract_flat": "in_playlist", "skip_download": True, "playlistend": 1}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise DownloadFailed(f"channel lookup failed for {url}: {exc}") from exc
    cid = info.get("channel_id") or ""
    if not cid.startswith("UC"):
        raise DownloadFailed(f"no channel id found for {url}")
    return cid
