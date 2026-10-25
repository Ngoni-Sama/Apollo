"""Channel -> manifest. Enumeration only; transcription is a separate step (pipeline.py --manifest).

YouTube uses yt-dlp --flat-playlist per tab. TikTok is BEST-EFFORT (TikTok-Api, then yt-dlp)
and may silently return a partial list under rate limiting.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
import downloader
import manifest
import tiktok_fetch
from manifest import ManifestItem
from schema import safe_name

log = logging.getLogger(__name__)

YOUTUBE_TABS = ("videos", "shorts", "streams")


def _yyyymmdd(entry: dict[str, Any]) -> str | None:
    if entry.get("upload_date"):
        return str(entry["upload_date"])
    ts = entry.get("timestamp") or entry.get("release_timestamp")
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y%m%d") if ts else None


def youtube_channel_url(channel: str, tab: str) -> str:
    channel = channel.strip().rstrip("/")
    if channel.startswith("http"):
        base = channel
        for t in YOUTUBE_TABS:
            base = base.removesuffix("/" + t)
    else:
        base = f"https://www.youtube.com/{channel if channel.startswith('@') else '@' + channel}"
    return f"{base}/{tab}"


def enumerate_youtube(channel: str, tabs: list[str], limit: int | None = None) -> list[ManifestItem]:
    items: list[ManifestItem] = []
    for tab in tabs:
        url = youtube_channel_url(channel, tab)
        try:
            entries = downloader.list_flat(url, limit=limit)
        except downloader.DownloadFailed as exc:
            # A channel without a shorts/streams tab errors here; that's not fatal.
            log.warning("youtube %s: %s", tab, exc)
            continue
        log.info("youtube %s: %d entries", tab, len(entries))
        for e in entries:
            vid = e.get("id")
            if not vid:
                continue
            video_url = e.get("url") or f"https://www.youtube.com/watch?v={vid}"
            if tab == "shorts" and "/shorts/" not in video_url:
                video_url = f"https://www.youtube.com/shorts/{vid}"
            items.append(ManifestItem(video_id=vid, title=e.get("title") or "", url=video_url,
                                      upload_date=_yyyymmdd(e), status="pending"))
    return items


def enumerate_tiktok(channel: str, limit: int | None = None) -> list[ManifestItem]:
    username = channel.strip().rstrip("/").split("/")[-1].lstrip("@")
    try:
        videos = tiktok_fetch.list_user_videos(username, limit=limit or 500)
        log.info("tiktok: TikTok-Api returned %d videos", len(videos))
        return [ManifestItem(
            video_id=v.video_id, title=v.desc[:200], url=v.url,
            upload_date=datetime.fromtimestamp(v.create_time, timezone.utc).strftime("%Y%m%d") if v.create_time else None,
            status="pending") for v in videos]
    except tiktok_fetch.TikTokFetchError as exc:
        log.warning("tiktok: TikTok-Api failed (%s); falling back to yt-dlp", exc)

    entries = downloader.list_flat(f"https://www.tiktok.com/@{username}", limit=limit)
    log.info("tiktok: yt-dlp returned %d videos", len(entries))
    return [ManifestItem(
        video_id=str(e["id"]), title=(e.get("title") or e.get("description") or "")[:200],
        url=e.get("url") or f"https://www.tiktok.com/@{username}/video/{e['id']}",
        upload_date=_yyyymmdd(e), status="pending") for e in entries if e.get("id")]


def default_out(platform: str, channel: str) -> Path:
    name = channel.strip().rstrip("/").split("/")[-1]
    return config.MANIFEST_DIR / platform / f"{safe_name(name)}.jsonl"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Enumerate ONE channel into a manifest (no transcription).")
    ap.add_argument("--platform", choices=("youtube", "tiktok"), required=True)
    ap.add_argument("--channel", required=True, help='"@handle" or channel URL (one channel per run)')
    ap.add_argument("--tabs", default="videos,shorts", help="YouTube tabs: videos,shorts,streams")
    ap.add_argument("--limit", type=int, help="max entries per tab (useful for testing)")
    ap.add_argument("--out", type=Path, help="manifest path (default data/manifest/{platform}/{channel}.jsonl)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    config.setup_logging(args.verbose)

    out = args.out or default_out(args.platform, args.channel)
    if args.platform == "youtube":
        tabs = [t.strip() for t in args.tabs.split(",") if t.strip()]
        bad = set(tabs) - set(YOUTUBE_TABS)
        if bad:
            ap.error(f"unknown tabs: {sorted(bad)}")
        found = enumerate_youtube(args.channel, tabs, args.limit)
    else:
        found = enumerate_tiktok(args.channel, args.limit)

    added = manifest.add_items(out, found)
    total = len(manifest.read_manifest(out))
    log.info("enumerated %d videos (%d new) -> %s (%d total in manifest)", len(found), added, out, total)
    if args.platform == "tiktok":
        log.warning("TikTok enumeration is best-effort: %d returned, the list may be INCOMPLETE.", len(found))
    if not found:
        log.error("no videos found for %s", args.channel)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
