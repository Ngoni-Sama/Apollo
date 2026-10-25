"""Orchestrator: URL / URL list / manifest -> transcript JSON (raw + cleaned).

YouTube: captions -> (fail) yt-dlp audio -> ASR
TikTok:  TikTok-Api video -> ffmpeg -> ASR -> (fail) yt-dlp audio -> ASR
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import clean
import config
import downloader
import manifest
import tiktok_fetch
import transcribe
import youtube_captions
from config import Settings, load_settings
from fileio import LockBusy, file_lock
from schema import Platform, Segment, make_record, transcript_path, write_record

log = logging.getLogger(__name__)

SPONSOR_FILTER_MIN_SEC = 120  # short-form clips rarely have ad reads; "link in bio" there is content
EXIT_BUSY = 3  # another transcription job (UI or scheduled check) holds the job lock


def job_lock() -> AbstractContextManager[None]:
    """Only one transcription job at a time across processes (UI jobs + the scheduled check).
    Raises LockBusy immediately if another job is running."""
    return file_lock(config.DATA_DIR / ".job.lock")


class PipelineError(Exception):
    """This video failed; other videos may still succeed."""


class PlatformBlocked(PipelineError):
    """The platform is blocking this IP/client. Stop the run; retrying now will not help."""


@dataclass
class Options:
    creator: str | None = None
    backend: str | None = None
    model: str | None = None
    force: bool = False
    keep_audio: bool = False
    sponsor_filter: bool = True


@dataclass
class Fetched:
    platform: Platform
    video_id: str
    creator: str
    method: str
    language: str
    segments: list[Segment]
    duration_sec: float | None


def detect_platform(url: str) -> Platform:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("youtube.com") or host.endswith("youtu.be"):
        return "youtube"
    if host.endswith("tiktok.com"):
        return "tiktok"
    raise PipelineError(f"unsupported URL (YouTube/TikTok only): {url}")


def _asr_from_ytdlp(url: str, opts: Options, settings: Settings) -> tuple[transcribe.TranscriptionResult, downloader.AudioDownload]:
    try:
        audio = downloader.download_audio(url, config.AUDIO_DIR, settings)
    except downloader.DownloadFailed as exc:
        if exc.is_block:
            raise PlatformBlocked(str(exc)) from exc
        raise PipelineError(str(exc)) from exc
    try:
        result = transcribe.transcribe(audio.path, backend=opts.backend, model=opts.model, settings=settings)
    finally:
        if not opts.keep_audio:
            audio.path.unlink(missing_ok=True)
    return result, audio


def _fetch_youtube(url: str, opts: Options, settings: Settings) -> Fetched:
    video_id = youtube_captions.extract_video_id(url)
    try:
        cap = youtube_captions.fetch_captions(video_id)
        creator = opts.creator
        if not creator:
            try:
                info = downloader.fetch_info(url, settings)
                creator = downloader.uploader_name(info)
            except downloader.DownloadFailed as exc:
                raise PipelineError(f"captions OK but creator lookup failed ({exc}); pass --creator") from exc
        if not creator:
            raise PipelineError("could not determine creator; pass --creator")
        return Fetched("youtube", video_id, creator, "captions", cap.language, cap.segments, None)
    except youtube_captions.CaptionsBlocked as exc:
        log.warning("%s -> falling back to yt-dlp + local ASR", exc)
    except youtube_captions.CaptionsError as exc:
        log.info("%s -> falling back to yt-dlp + local ASR", exc)

    result, audio = _asr_from_ytdlp(url, opts, settings)
    creator = opts.creator or audio.uploader
    if not creator:
        raise PipelineError("could not determine creator; pass --creator")
    return Fetched("youtube", video_id, creator, "asr", result.language, result.segments,
                   audio.duration_sec or result.duration_sec)


def _fetch_tiktok(url: str, opts: Options, settings: Settings) -> Fetched:
    try:
        username, video_id = tiktok_fetch.parse_url(url)
    except ValueError as exc:
        raise PipelineError(f"{exc} — use the full https://www.tiktok.com/@user/video/<id> URL") from exc
    creator = opts.creator or username

    try:
        meta = tiktok_fetch.download_video(url, config.AUDIO_DIR, settings)
        video_path = meta.path or config.AUDIO_DIR / f"{video_id}.mp4"
        mp3 = video_path.with_suffix(".mp3")
        try:
            transcribe.extract_audio(video_path, mp3)
            result = transcribe.transcribe(mp3, backend=opts.backend, model=opts.model, settings=settings)
        finally:
            if not opts.keep_audio:
                video_path.unlink(missing_ok=True)
                mp3.unlink(missing_ok=True)
        return Fetched("tiktok", video_id, creator, "asr", result.language, result.segments,
                       meta.duration_sec or result.duration_sec)
    except tiktok_fetch.TikTokFetchError as exc:
        log.warning("TikTok-Api failed (%s) -> falling back to yt-dlp", exc)

    result, audio = _asr_from_ytdlp(url, opts, settings)
    return Fetched("tiktok", video_id, creator, "asr", result.language, result.segments,
                   audio.duration_sec or result.duration_sec)


def process_url(url: str, opts: Options | None = None, settings: Settings | None = None) -> Path | None:
    """Fetch + transcribe one video, write raw and cleaned JSON. Returns the cleaned path (None if skipped)."""
    opts = opts or Options()
    settings = settings or load_settings()
    platform = detect_platform(url)

    if opts.creator and not opts.force:
        vid = (youtube_captions.extract_video_id(url) if platform == "youtube"
               else tiktok_fetch.parse_url(url)[1])
        existing = transcript_path(platform, opts.creator, vid)
        if existing.exists():
            log.info("skip %s: already transcribed at %s", url, existing)
            return None

    fetched = _fetch_youtube(url, opts, settings) if platform == "youtube" else _fetch_tiktok(url, opts, settings)
    if not fetched.segments:
        raise PipelineError(f"empty transcript for {url}")

    common = dict(platform=fetched.platform, creator=fetched.creator, video_id=fetched.video_id,
                  source_url=url, method=fetched.method, language=fetched.language)
    raw = make_record(**common, segments=fetched.segments, duration_sec=fetched.duration_sec)
    write_record(raw, transcript_path(fetched.platform, fetched.creator, fetched.video_id, raw=True))

    segments = clean.clean_segments(fetched.segments)
    if opts.sponsor_filter and raw["duration_sec"] >= SPONSOR_FILTER_MIN_SEC:
        before = len(segments)
        segments = clean.remove_sponsor_segments(segments)
        if len(segments) != before:
            log.info("sponsor filter removed %d segments", before - len(segments))
    cleaned = make_record(**common, segments=segments, duration_sec=raw["duration_sec"])
    out = write_record(cleaned, transcript_path(fetched.platform, fetched.creator, fetched.video_id))
    log.info("wrote %s (%s, %d segments, %d chars)", out, fetched.method, len(segments), len(cleaned["text"]))
    return out


def process_urls(urls: list[str], opts: Options, settings: Settings, sleep: float) -> dict[str, int]:
    counts = {"done": 0, "skipped": 0, "failed": 0}
    for i, url in enumerate(urls):
        try:
            counts["done" if process_url(url, opts, settings) else "skipped"] += 1
        except PlatformBlocked as exc:
            log.error("platform is blocking requests, stopping: %s", exc)
            counts["failed"] += 1
            break
        except (PipelineError, transcribe.TranscriptionError, ValueError) as exc:
            log.error("failed %s: %s", url, exc)
            counts["failed"] += 1
        if i < len(urls) - 1:
            time.sleep(sleep)
    return counts


def process_manifest(path: Path, opts: Options, settings: Settings, sleep: float) -> dict[str, int]:
    """Resumable: skips done, retries failed once (MAX_ATTEMPTS total), saves after every item."""
    platform = path.parent.name
    if platform not in ("youtube", "tiktok"):
        raise PipelineError(f"manifest must live under data/manifest/{{youtube,tiktok}}/, got {path}")
    creator = opts.creator or path.stem
    item_opts = Options(**{**opts.__dict__, "creator": creator})
    items = manifest.read_manifest(path)
    blocked = False

    def todo() -> list[manifest.ManifestItem]:
        return [i for i in items if i["status"] != "done" and not manifest.is_permanently_failed(i)]

    for _ in range(manifest.MAX_ATTEMPTS):
        batch = todo()
        if not batch or blocked:
            break
        log.info("manifest %s: %d to process", path.name, len(batch))
        for n, item in enumerate(batch):
            try:
                process_url(item["url"], item_opts, settings)
                item["status"] = "done"
                item.pop("error", None)
                manifest.patch(path, item["video_id"], {"status": "done"}, drop=("error",))
            except PlatformBlocked as exc:
                log.error("platform is blocking requests; stopping without consuming retries: %s", exc)
                blocked = True
                break
            except (PipelineError, transcribe.TranscriptionError, ValueError) as exc:
                item["attempts"] = item.get("attempts", 0) + 1
                item["status"] = "failed"
                item["error"] = str(exc)[:500]
                manifest.patch(path, item["video_id"], {k: item[k] for k in ("status", "attempts", "error")})
                log.error("failed %s (attempt %d/%d): %s", item["video_id"], item["attempts"],
                          manifest.MAX_ATTEMPTS, exc)
            if n < len(batch) - 1:
                time.sleep(sleep)

    items = manifest.read_manifest(path)  # re-read: other processes may have queued new videos meanwhile
    counts = {s: sum(1 for i in items if i["status"] == s) for s in manifest.STATUSES}
    counts["permanently_failed"] = sum(1 for i in items if manifest.is_permanently_failed(i))
    counts["blocked"] = int(blocked)
    return counts


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    ap = argparse.ArgumentParser(description="Transcribe YouTube/TikTok videos into transcript JSON.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url")
    src.add_argument("--urls", type=Path, help="text file, one URL per line")
    src.add_argument("--manifest", type=Path, help="manifest JSONL from enumerate.py")
    ap.add_argument("--creator", help="creator handle (attribution); inferred when possible")
    ap.add_argument("--backend", choices=transcribe.BACKENDS)
    ap.add_argument("--model", help="faster-whisper model (default from .env)")
    ap.add_argument("--sleep", type=float, default=settings.sleep_sec, help="seconds between videos (default 2.0)")
    ap.add_argument("--force", action="store_true", help="re-transcribe even if output exists")
    ap.add_argument("--keep-audio", action="store_true")
    ap.add_argument("--no-sponsor-filter", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    config.setup_logging(args.verbose)

    if args.sleep < config.DEFAULT_SLEEP_SEC:
        log.warning("--sleep %.1f is below the %.1fs default throttle", args.sleep, config.DEFAULT_SLEEP_SEC)
    opts = Options(creator=args.creator, backend=args.backend, model=args.model, force=args.force,
                   keep_audio=args.keep_audio, sponsor_filter=not args.no_sponsor_filter)

    with ExitStack() as stack:
        try:
            stack.enter_context(job_lock())
        except LockBusy:
            log.warning("another Apollo transcription job is already running; try again when it finishes")
            return EXIT_BUSY
        return _run(args, opts, settings)


def _run(args: argparse.Namespace, opts: Options, settings: Settings) -> int:
    if args.url:
        try:
            out = process_url(args.url, opts, settings)
        except PlatformBlocked as exc:
            log.error("BLOCKED: %s", exc)
            return 2
        except (PipelineError, transcribe.TranscriptionError, ValueError) as exc:
            log.error("FAILED: %s", exc)
            return 1
        log.info("result: %s", out or "skipped (already exists; use --force)")
        return 0

    if args.urls:
        urls = [u.strip() for u in args.urls.read_text().splitlines() if u.strip() and not u.startswith("#")]
        counts = process_urls(urls, opts, settings, args.sleep)
    else:
        counts = process_manifest(args.manifest, opts, settings, args.sleep)
    log.info("summary: %s", counts)
    if counts.get("blocked"):
        return 2
    return 0 if counts.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
