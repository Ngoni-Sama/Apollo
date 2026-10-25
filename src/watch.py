"""Watch creators for new uploads and queue them in their manifests.

YouTube: the official channel RSS feed (latest ~15 uploads, one request per channel).
TikTok:  BEST-EFFORT re-enumeration of the latest few videos; may miss posts.

    python src/watch.py add --platform youtube --channel @CreatorName
    python src/watch.py check [--transcribe]     # run from cron / Task Scheduler
    python src/watch.py list
    python src/watch.py remove --platform youtube --channel @CreatorName

New videos are appended to data/manifest/{platform}/{creator}.jsonl as pending, logged to
data/watch_log.jsonl, and (opt-in) pushed to your phone via ntfy if WATCH_NTFY_TOPIC is set.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict
from urllib.error import URLError

import config
import downloader
import enumerate as enumerate_mod
import manifest
from fileio import LockBusy, atomic_write_text
from manifest import ManifestItem
from schema import safe_name

log = logging.getLogger(__name__)

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
SEEN_CAP = 500
TIKTOK_CHECK_LIMIT = 10


class WatchEntry(TypedDict, total=False):
    platform: str
    channel: str       # "@handle" as given
    creator: str       # manifest name: data/manifest/{platform}/{creator}.jsonl
    channel_id: str    # YouTube UC... id (for the RSS feed)
    seen: list[str]    # video ids already known (baseline + everything found since)
    skip_shorts: bool  # YouTube: never queue Shorts (they're usually clips of full episodes)
    added_at: str
    last_checked: str


def watchlist_path() -> Path:
    return config.DATA_DIR / "watchlist.json"


def log_path() -> Path:
    return config.DATA_DIR / "watch_log.jsonl"


def load_watchlist() -> list[WatchEntry]:
    path = watchlist_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_watchlist(entries: list[WatchEntry]) -> None:
    path = watchlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(entries, ensure_ascii=False, indent=2))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _manifest_path(entry: WatchEntry) -> Path:
    return config.MANIFEST_DIR / entry["platform"] / f"{entry['creator']}.jsonl"


# --- fetching ---------------------------------------------------------------

def parse_youtube_feed(xml_text: str) -> list[ManifestItem]:
    root = ET.fromstring(xml_text)
    items: list[ManifestItem] = []
    for e in root.findall("atom:entry", _NS):
        vid = e.findtext("yt:videoId", default="", namespaces=_NS)
        if not vid:
            continue
        published = e.findtext("atom:published", default="", namespaces=_NS)
        link = e.find("atom:link", _NS)
        url = link.get("href") if link is not None else f"https://www.youtube.com/watch?v={vid}"
        items.append(ManifestItem(
            video_id=vid, title=e.findtext("atom:title", default="", namespaces=_NS), url=url,
            upload_date=published[:10].replace("-", "") or None, status="pending"))
    return items


def fetch_youtube_feed(channel_id: str, timeout: float = 30.0) -> list[ManifestItem]:
    req = urllib.request.Request(FEED_URL.format(channel_id), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return parse_youtube_feed(resp.read().decode("utf-8"))


def is_short(item: ManifestItem) -> bool:
    return "/shorts/" in item.get("url", "")


def latest(entry: WatchEntry) -> list[ManifestItem]:
    if entry["platform"] == "youtube":
        return fetch_youtube_feed(entry["channel_id"])
    return enumerate_mod.enumerate_tiktok(entry["channel"], limit=TIKTOK_CHECK_LIMIT)


def notify(title: str, message: str, url: str | None = None) -> None:
    """Opt-in push via ntfy (free, no account): set WATCH_NTFY_TOPIC to a hard-to-guess topic name."""
    topic = os.environ.get("WATCH_NTFY_TOPIC", "").strip()
    if not topic:
        return
    server = os.environ.get("WATCH_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore")}
    if url:
        headers["Click"] = url
    req = urllib.request.Request(f"{server}/{topic}", data=message.encode("utf-8"), headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).close()
    except URLError as exc:
        log.warning("ntfy notification failed: %s", exc)


# --- commands ---------------------------------------------------------------

def add(platform: str, channel: str, backfill: bool = False, skip_shorts: bool = False) -> WatchEntry:
    """Start watching a channel. By default the current uploads are the baseline (not queued)."""
    entries = load_watchlist()
    creator = safe_name(channel.rstrip("/").split("/")[-1])
    if any(e["platform"] == platform and e["creator"] == creator for e in entries):
        raise ValueError(f"already watching {platform}/{creator}")
    entry = WatchEntry(platform=platform, channel=channel, creator=creator, seen=[], added_at=_now(),
                       skip_shorts=skip_shorts and platform == "youtube")
    if platform == "youtube":
        entry["channel_id"] = (channel if channel.startswith("UC") and len(channel) == 24
                               else downloader.channel_id(enumerate_mod.youtube_channel_url(channel, "videos")))
    current = latest(entry)
    if backfill:
        queue = [i for i in current if not (entry["skip_shorts"] and is_short(i))]
        added = manifest.add_items(_manifest_path(entry), queue)
        log.info("queued %d current uploads in %s", added, _manifest_path(entry))
    entry["seen"] = [i["video_id"] for i in current]
    entry["last_checked"] = _now()
    entries.append(entry)
    save_watchlist(entries)
    log.info("watching %s/%s (%d current uploads as baseline)", platform, creator, len(current))
    return entry


def remove(platform: str, channel: str) -> bool:
    creator = safe_name(channel.rstrip("/").split("/")[-1])
    entries = load_watchlist()
    kept = [e for e in entries if not (e["platform"] == platform and e["creator"] == creator)]
    save_watchlist(kept)
    return len(kept) != len(entries)


def check(sleep: float = config.DEFAULT_SLEEP_SEC) -> tuple[dict[str, list[ManifestItem]], list[str]]:
    """Poll every watched channel once. Returns ({manifest path: new items}, [failed channels])."""
    entries = load_watchlist()
    found: dict[str, list[ManifestItem]] = {}
    failed: list[str] = []
    for n, entry in enumerate(entries):
        name = f"{entry['platform']}/{entry['creator']}"
        try:
            current = latest(entry)
        except Exception as exc:  # one broken channel must not stop the others
            log.error("check failed for %s: %s", name, exc)
            failed.append(name)
            continue
        finally:
            if n < len(entries) - 1:
                time.sleep(sleep)

        path = _manifest_path(entry)
        known = set(entry.get("seen", [])) | {i["video_id"] for i in manifest.read_manifest(path)}
        unseen = [i for i in current if i["video_id"] not in known]
        entry["seen"] = (entry.get("seen", []) + [i["video_id"] for i in unseen])[-SEEN_CAP:]
        new = [i for i in unseen if not (entry.get("skip_shorts") and is_short(i))]
        if len(new) != len(unseen):
            log.info("%s: skipped %d new Short(s)", name, len(unseen) - len(new))
        entry["last_checked"] = _now()
        if not new:
            log.info("%s: nothing new", name)
            continue
        manifest.add_items(path, new)
        found[str(path)] = new
        with log_path().open("a", encoding="utf-8") as f:
            for item in new:
                log.info("NEW %s: %s %s", name, item["title"][:80], item["url"])
                f.write(json.dumps({"detected_at": _now(), "platform": entry["platform"],
                                    "creator": entry["creator"], **item}, ensure_ascii=False) + "\n")
                notify(f"New {entry['platform']} video: {entry['creator']}", item["title"] or item["url"], item["url"])
    save_watchlist(entries)
    if any(e["platform"] == "tiktok" for e in entries):
        log.warning("TikTok checks are best-effort and only look at the latest %d videos.", TIKTOK_CHECK_LIMIT)
    return found, failed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add", "remove"):
        p = sub.add_parser(name)
        p.add_argument("--platform", choices=("youtube", "tiktok"), required=True)
        p.add_argument("--channel", required=True, help='"@handle" (YouTube also accepts a UC... id or channel URL)')
        if name == "add":
            p.add_argument("--backfill", action="store_true", help="also queue the current latest uploads")
            p.add_argument("--skip-shorts", action="store_true", help="YouTube: never queue Shorts")
    sub.add_parser("list")
    p = sub.add_parser("check")
    p.add_argument("--transcribe", action="store_true", help="run the pipeline on manifests that got new videos")
    p.add_argument("--sleep", type=float, default=config.load_settings().sleep_sec)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--log-file", type=Path, help="also append logs here (used by the scheduled task)")
    args = ap.parse_args(argv)
    config.setup_logging(args.verbose, args.log_file)

    if args.cmd == "add":
        try:
            add(args.platform, args.channel, args.backfill, args.skip_shorts)
        except (ValueError, downloader.DownloadFailed, URLError) as exc:
            log.error("could not add %s: %s", args.channel, exc)
            return 1
        return 0
    if args.cmd == "remove":
        ok = remove(args.platform, args.channel)
        log.info("removed" if ok else "not in watchlist")
        return 0 if ok else 1
    if args.cmd == "list":
        for e in load_watchlist():
            sys.stdout.write(f"{e['platform']:8} {e['creator']:30} last checked {e.get('last_checked', '-')}\n")
        return 0

    found, failed = check(args.sleep)
    total = sum(len(v) for v in found.values())
    log.info("check done: %d new video(s) across %d channel(s); %d channel(s) failed", total, len(found), len(failed))
    if args.transcribe and found:
        import pipeline  # imported here: pulls in the ASR stack, not needed for a plain check

        settings = config.load_settings()
        try:
            with pipeline.job_lock():
                for path in found:
                    counts = pipeline.process_manifest(Path(path), pipeline.Options(), settings, args.sleep)
                    log.info("transcribed %s: %s", Path(path).name, counts)
                    if counts.get("blocked"):
                        log.error("platform blocked this IP; stopping transcription for this check")
                        return 2
        except LockBusy:
            log.warning("another transcription job is running; new videos stay queued for it or the next check")
            return pipeline.EXIT_BUSY
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
