# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Project: Multi-Platform Creator Transcript Pipeline

A personal-use tool that extracts transcripts from YouTube and TikTok videos of mental health, fitness, and trading creators, then structures that text for downstream bot training (fine-tuning or RAG).

**Scope of use:** personal research and study only. Do not republish transcripts or train commercial products on creator content.

---

## Prime Directives

1. **Free / local-first.** No paid APIs unless the user explicitly opts in. Prefer local Whisper (`whisper.cpp` or `faster-whisper`) over the OpenAI Whisper API.
2. **Official endpoints before scraping.** Always try official or community-maintained caption/transcript routes first. Fall back to download-and-transcribe only when captions are unavailable.
3. **Verify before scaling.** Never build a large pipeline on a scraper that has not been smoke-tested on 3–5 real URLs in this session.
4. **Check the last commit date, not the star count.** A repo with 5,000 stars and no commits in 18 months is a dead dependency. Do not add it.
5. **Never hardcode secrets or cookies.** Use `.env` + `.gitignore`.

---

## Architecture

```
input URL
   │
   ├─ YouTube ──► youtube-transcript-api ──► transcript (success)
   │                    │
   │                    └─ failure ──► yt-dlp audio ──► whisper ──► transcript
   │
   └─ TikTok ───► TikTok-Api (smoke-tested) ──► video ──► ffmpeg audio ──► whisper
                       │
                       └─ failure ──► yt-dlp audio ──► whisper
   │
   ▼
clean + sponsor filter ──► data/transcripts/{platform}/{creator}/{video_id}.json
                           (untouched copy: data/transcripts/raw/{platform}/{creator}/{video_id}.json)
   │
   ▼
scripts/build_dataset.py: chunk + topic-tag + Q&A mining ──► data/dataset/{chunks,qa}.jsonl
```

---

## Approved Dependencies (verified 2026-10-25)

Only add a library if it appears here, or the user approves a new one after you show its last-commit date and latest release. Versions are pinned in `requirements.txt`.

### YouTube Captions
- **`youtube-transcript-api`** 1.2.4: `https://github.com/jdepoix/youtube-transcript-api` (last commit 2026-10-20)
  - **Use the instance API only.** The old static methods (`get_transcript`, `get_transcripts`, `list_transcripts`) are gone in 1.2.x:
    ```python
    from youtube_transcript_api import YouTubeTranscriptApi
    ytt_api = YouTubeTranscriptApi()
    transcript = ytt_api.fetch(video_id)  # returns FetchedTranscript
    raw = transcript.to_raw_data()        # list of dicts
    ```
  - `RequestBlocked` (and its subclass `IpBlocked`) is raised when YouTube blocks the caller's IP; handle it explicitly, do not silently retry. Cloud/datacenter IPs are blocked by default.

### Downloader
- **`yt-dlp`** 2026.10.21 (last commit 2026-10-23). Fix breakage with `pip install -U yt-dlp`.
  - YouTube extraction needs a JavaScript runtime (deno by default; `YTDLP_JS_RUNTIMES=node` to use node).
  - Audio only: `yt-dlp -x --audio-format mp3 URL` (`downloader.download_audio`).
  - Channel enumeration: `--flat-playlist` per tab (`videos`, `shorts`, `streams`) (`downloader.list_flat`).
  - `curl_cffi` (yt-dlp's impersonation extra) is NOT approved; it did not get past TikTok's IP block in testing.

### TikTok
- **`TikTokApi`** 7.3.3 (davidteather, last commit 2026-08-23): `https://github.com/davidteather/TikTok-Api`
  - Needs a Playwright Chromium (`python -m playwright install chromium`). TikTok frequently changes its frontend; this library **will** break without warning.
  - `TIKTOK_MS_TOKEN` in `.env` improves success rate.
  - Expect partial results under rate limiting. Always report the count returned and warn the user it may be incomplete.

### Local Transcription
- **`faster-whisper`** 1.2.1 (SYSTRAN, last commit 2026-10-16): CPU int8 or NVIDIA GPU. **Requires `av<19`**: PyAV 19 removed the `metadata_errors=` kwarg that 1.2.1 passes.
- **`whisper.cpp`** (ggerganov, last commit 2026-10-19): CPU/Apple Silicon. Build `whisper-cli` with cmake; models via `./models/download-ggml-model.sh`.
- `transcribe-anything` is approved but unused (one more wrapper layer for no gain).

### Audio Extraction
- **`ffmpeg`** (system binary): required for every Whisper route.

### Blacklisted (Do Not Install)
- **`drawrowfly/tiktok-scraper`**: stale for 2+ years and returns empty results.

---

## Repo Layout

```
src/
  config.py            # paths, .env loading (Settings)
  schema.py            # transcript JSON schema: make/validate/write, paths
  youtube_captions.py  # youtube-transcript-api wrapper (v1.x API)
  downloader.py        # yt-dlp wrapper (audio-only, metadata, flat listing)
  tiktok_fetch.py      # TikTok-Api wrapper (isolated, easy to swap)
  transcribe.py        # faster-whisper / whisper.cpp adapter + ffmpeg helpers
  manifest.py          # manifest JSONL I/O (kept out of enumerate.py: `import enumerate` would shadow the builtin)
  enumerate.py         # channel -> manifest
  watch.py             # new-upload watcher: YouTube RSS feed / TikTok best-effort -> manifest (+ opt-in ntfy alert)
  clean.py             # filler/noise removal, sponsor-segment heuristics
  chunk.py             # token-estimate chunking + topic tags
  pipeline.py          # orchestrator: URL / URL list / manifest -> transcript JSON
scripts/
  smoke_test.py        # per-stage PASS/FAIL on real URLs (+ --audio for offline ASR)
  build_dataset.py     # transcripts -> chunks.jsonl + qa.jsonl
tests/                 # pytest, fully mocked
data/{transcripts,manifest,dataset,audio}/
```

`src/` modules import each other as top-level modules (`import config`), so run them as `python src/x.py`. Scripts/tests add `src/` to `sys.path`.

---

## Channel Enumeration (opt-in)

Enumeration is separate from transcription. The manifest is the resume point. **Never enumerate and transcribe in one command.**

- Manifest line: `{"video_id", "title", "url", "upload_date": "YYYYMMDD"|null, "status": "pending|done|failed"}` plus `attempts`/`error` once a video fails.
- `pipeline.py --manifest` skips `done`, retries `failed` once (2 attempts total), then leaves it permanently failed. It saves after every item and logs counts at the end.
- A platform block (`PlatformBlocked`) stops the run **without** consuming attempts (exit 2).
- Creator = manifest filename stem; platform = parent dir name.
- Throttle: `--sleep 2.0` default between downloads. Do not remove.
- Never enumerate more than one channel per invocation without user confirmation.
- `watch.py check` polls each watched channel once (YouTube RSS = 1 request/channel, TikTok = latest 10), with the same sleep between channels. Schedule it hourly at most.

---

## Conventions

- **Python 3.11+.** Type hints on every public function.
- **All network I/O goes through `src/` modules.** No ad-hoc `requests` calls in `scripts/` or notebooks.
- **Transcript JSON schema** (stable; enforced by `schema.validate_record`):
  ```json
  {"platform": "youtube"|"tiktok", "creator": "string", "video_id": "string", "source_url": "string",
   "fetched_at": "ISO-8601", "method": "captions"|"asr", "language": "en", "duration_sec": 0,
   "segments": [{"start": 0.0, "end": 0.0, "text": "..."}], "text": "full concatenated text"}
  ```
- **Logging:** use the `logging` module, never `print`, in `src/`.

---

## Testing Rules

- Every new module gets at least one test in `tests/`. Mock network calls; never hit live platforms in tests.
- Before declaring a task done on any scraping/transcription module: **run it against a real URL and paste the observed output into the response**. Do not claim success from code inspection alone.
- If a scraper fails, say so plainly and propose the fallback path. Do not silently retry in a loop.
- Note: Claude Code cloud containers are IP-blocked by YouTube and TikTok (verified 2026-10-25). Live smoke tests must run on the user's machine.

---

## Legal & Ethical Guardrails

- Personal study only, unless the user confirms otherwise.
- No high-concurrency bulk scraping. Default `--sleep 2.0`.
- Never strip attribution: `creator` and `source_url` are required (`make_record` raises without them).
- If asked to build redistribution or monetization features, flag the copyright risk explicitly before proceeding.

---

## Known Fragilities

- `youtube-transcript-api`: `RequestBlocked`/`IpBlocked` on blocked IPs. Report it clearly and do not retry.
- `TikTok-Api`: breaks on TikTok front-end changes. If `scripts/smoke_test.py` fails on TikTok, **stop and report**. Do not patch the library inline.
- `yt-dlp`: fix is almost always `pip install -U yt-dlp`.
- Model weights are large. They are cached once in `MODEL_CACHE_DIR` (`./models`), never re-downloaded per run.

---

## When in Doubt

- Prefer the approach with fewer moving parts.
- Prefer the library with the most recent commit over the one with the most stars.
- Prefer reading the actual error over adding a retry loop.
- Ask before introducing any paid service, any unapproved dependency, or anything that scales up request volume.
