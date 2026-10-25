"""Sanity-check every stage on a few real URLs before any bulk run.

    python scripts/smoke_test.py --youtube URL [--youtube URL ...] --tiktok URL [--audio FILE] [--full]

Each stage reports PASS/FAIL with the real error. Nothing is retried.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402
import downloader  # noqa: E402
import pipeline  # noqa: E402
import tiktok_fetch  # noqa: E402
import transcribe  # noqa: E402
import youtube_captions  # noqa: E402

log = logging.getLogger("smoke")
results: list[tuple[str, bool, str]] = []


def stage(name: str, fn: Callable[[], str]) -> bool:
    try:
        detail = fn()
        results.append((name, True, detail))
        log.info("PASS %s: %s", name, detail)
        return True
    except Exception as exc:  # report everything, the point is to see the real error
        msg = f"{type(exc).__name__}: {exc}".splitlines()[0][:300]
        results.append((name, False, msg))
        log.error("FAIL %s: %s", name, msg)
        log.debug(traceback.format_exc())
        return False


def check_youtube(url: str, settings: config.Settings) -> None:
    vid = youtube_captions.extract_video_id(url)

    def captions() -> str:
        cap = youtube_captions.fetch_captions(vid)
        return f"{len(cap.segments)} segments, lang={cap.language}, generated={cap.is_generated}: {cap.segments[0]['text'][:80]!r}"

    def metadata() -> str:
        info = downloader.fetch_info(url, settings)
        return f"title={info.get('title')!r} uploader={downloader.uploader_name(info)!r} duration={info.get('duration')}"

    stage(f"youtube captions [{vid}]", captions)
    stage(f"youtube yt-dlp metadata [{vid}]", metadata)


def check_tiktok(url: str, settings: config.Settings) -> None:
    _, vid = tiktok_fetch.parse_url(url)

    def tiktok_api() -> str:
        with tempfile.TemporaryDirectory() as tmp:
            meta = tiktok_fetch.download_video(url, Path(tmp), settings)
            size = meta.path.stat().st_size if meta.path else 0
            return f"{size} bytes, author={meta.username!r}, desc={meta.desc[:60]!r}"

    def ytdlp() -> str:
        info = downloader.fetch_info(url, settings)
        return f"title={(info.get('title') or '')[:60]!r} uploader={downloader.uploader_name(info)!r}"

    stage(f"tiktok TikTok-Api download [{vid}]", tiktok_api)
    stage(f"tiktok yt-dlp metadata [{vid}]", ytdlp)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--youtube", action="append", default=[], help="YouTube URL (repeatable)")
    ap.add_argument("--tiktok", action="append", default=[], help="TikTok URL (repeatable)")
    ap.add_argument("--audio", type=Path, help="local audio file to test the ASR backend offline")
    ap.add_argument("--backend", choices=transcribe.BACKENDS)
    ap.add_argument("--full", action="store_true", help="also run the full pipeline on each URL (writes to data/)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    config.setup_logging(args.verbose)
    if not (args.youtube or args.tiktok or args.audio):
        ap.error("give at least one of --youtube, --tiktok, --audio")
    settings = config.load_settings()

    if args.audio:
        def asr() -> str:
            r = transcribe.transcribe(args.audio, backend=args.backend, settings=settings)
            return f"{r.backend}: {len(r.segments)} segments, {r.duration_sec}s: {r.segments[0]['text'][:80]!r}"
        stage(f"local ASR [{args.audio.name}]", asr)

    for url in args.youtube:
        check_youtube(url, settings)
    for url in args.tiktok:
        check_tiktok(url, settings)

    if args.full:
        opts = pipeline.Options(backend=args.backend)
        for url in args.youtube + args.tiktok:
            stage(f"full pipeline [{url}]", lambda u=url: str(pipeline.process_url(u, opts, settings)))

    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} stages passed")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}\n        {detail}")
    if any(not ok and "tiktok TikTok-Api" in name for name, ok, _ in results):
        print("\nTikTok-Api failed: do not patch the library. Use the yt-dlp fallback, or update TikTokApi"
              " and set TIKTOK_MS_TOKEN in .env, then re-run this smoke test.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
