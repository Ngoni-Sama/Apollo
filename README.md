# Creator Transcript Pipeline

Personal-use tool: YouTube/TikTok videos (mental health, fitness, trading creators) → timestamped transcript JSON → RAG chunks / Q&A JSONL for bot training. **Personal study only — do not republish transcripts or train commercial products on them.**

```
YouTube: youtube-transcript-api ──(fail)──► yt-dlp audio ──► local Whisper
TikTok:  TikTok-Api video ──► ffmpeg ──► local Whisper ──(fail)──► yt-dlp audio ──► local Whisper
   ▼
clean (fillers, [Music], sponsor reads) ──► data/transcripts/{platform}/{creator}/{video_id}.json
   ▼
scripts/build_dataset.py ──► data/dataset/chunks.jsonl + qa.jsonl
```

## Setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate      # 3.11+
pip install -r requirements.txt
python -m playwright install chromium                     # for TikTok-Api
cp .env.example .env
```

System requirements: `ffmpeg` on PATH. For YouTube, yt-dlp needs a JavaScript runtime: install [deno](https://deno.com) (the default) or set `YTDLP_JS_RUNTIMES=node` in `.env`.

**ASR backend** (set in `.env`):
- `faster-whisper` (default): works on CPU (int8) and NVIDIA GPU. The model downloads once into `./models`. `small.en` is a good CPU default; use `large-v3-turbo` on a GPU.
- `whisper.cpp` (best on Apple Silicon / CPU):
  ```bash
  git clone https://github.com/ggerganov/whisper.cpp && cd whisper.cpp
  cmake -B build && cmake --build build -j --config Release --target whisper-cli
  ./models/download-ggml-model.sh base.en
  ```
  then set `WHISPER_BACKEND=whisper.cpp`, `WHISPER_CPP_BIN=.../build/bin/whisper-cli`, `WHISPER_CPP_MODEL=.../models/ggml-base.en.bin`.

## The app (recommended)

```bash
python src/ui.py --open        # opens http://127.0.0.1:8765 in your browser
```

- **Dashboard:** check for new videos (optionally transcribing them), transcribe a single URL, follow the current job's live log and stop it, and see recently detected videos.
- **Creators:** add or remove creators, list a channel's whole back catalogue, and transcribe everything waiting.
- **Transcripts:** full-text search across every transcript. Click a timestamp to open the YouTube video at that moment.
- **Dataset:** rebuild `chunks.jsonl` / `qa.jsonl`, and run the smoke test on a few URLs.

The app only listens on your own computer (127.0.0.1). Long tasks run one at a time in the background, using the same commands as below, so the 2-second throttle still applies. Keep the terminal window open while you use it. Scheduled checks (see "Getting new-video updates") work whether or not the app is running.

## Command line

```bash
# 1. ALWAYS smoke-test first (3-5 real URLs; --audio checks ASR offline)
python scripts/smoke_test.py --youtube "<url>" --youtube "<url>" --tiktok "<url>" --audio some.mp3

# 2a. single video / list of URLs
python src/pipeline.py --url "<url>" [--creator handle]
python src/pipeline.py --urls data/urls.txt

# 2b. whole channel: enumerate (one channel per run) -> transcribe the manifest (resumable)
python src/enumerate.py --platform youtube --channel "@CreatorName" --tabs videos,shorts
python src/pipeline.py --manifest data/manifest/youtube/CreatorName.jsonl

python src/enumerate.py --platform tiktok --channel "@creator"     # best-effort, may be partial
python src/pipeline.py --manifest data/manifest/tiktok/creator.jsonl

# 3. watch creators for new uploads (YouTube: official RSS feed; TikTok: best-effort)
python src/watch.py add --platform youtube --channel "@CreatorName" --skip-shorts   # current uploads = baseline; never queue Shorts
python src/watch.py add --platform tiktok --channel "@creator"        # add --backfill to also queue current uploads
python src/watch.py list
python src/watch.py check --transcribe      # new videos -> manifest -> transcripts

# 4. build training data
python scripts/build_dataset.py [--creator CreatorName] [--max-tokens 400 --overlap 50]

# transcribe a local file
python src/transcribe.py --file path/to/audio.mp3 --backend faster-whisper
```

Run the tests with `pytest`. They are fully mocked and never touch the network.

## Getting new-video updates

Schedule `watch.py check` on your own machine. Downloads must come from a home connection because cloud IPs are blocked.

- **macOS/Linux** (`crontab -e`, hourly at :17):
  `17 * * * * cd /path/to/Apollo && .venv/bin/python src/watch.py check --transcribe >> data/watch.log 2>&1`
- **Windows** (Task Scheduler, hourly):
  `schtasks /Create /SC HOURLY /TN Apollo-watch /TR "C:\path\to\Apollo\.venv\Scripts\python.exe C:\path\to\Apollo\src\watch.py check --transcribe"`

New videos are logged to `data/watch_log.jsonl`. For phone alerts, set `WATCH_NTFY_TOPIC` in `.env` and subscribe to that topic in the free [ntfy](https://ntfy.sh) app. The YouTube feed shows the latest ~15 uploads, so hourly or daily checks never miss one. TikTok checks only look at the latest 10 videos and can fail under bot detection.

## Behaviour worth knowing

- **Throttle:** `--sleep 2.0` (default) between videos.
- **Manifest resume:** `done` items are skipped. A failed item is retried once (2 attempts total) and then marked permanently failed (`attempts: 2`, with `error`). Progress is saved after every item.
- **Blocks stop the run.** If YouTube or TikTok refuses your IP (`RequestBlocked`, "Sign in to confirm you're not a bot", HTTP 429, "IP address is blocked"), the run stops with exit code 2 and **does not** use up retry attempts. Wait, change network, or add cookies, then re-run.
- **Raw and cleaned output:** the cleaned transcript is at `data/transcripts/{platform}/{creator}/{video_id}.json`, and the untouched original is under `data/transcripts/raw/...`.
- **Sponsor filter** is a keyword heuristic and only applies to videos ≥ 2 min. Disable it with `--no-sponsor-filter`.
- **Q&A mining** finds questions the creator asks and answers. It needs punctuation, so Whisper transcripts work well, but YouTube *auto*-captions (unpunctuated) yield few or no pairs. Chunks are always produced.
- **Token counts** are estimated at ~4 chars/token (no tokenizer dependency).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `CaptionsBlocked` / `IpBlocked` | Your IP is blocked (common on cloud/VPN IPs). Run from home, or wait. The pipeline already falls back to yt-dlp. |
| yt-dlp "Sign in to confirm you're not a bot" | Set `YTDLP_COOKIES_FROM_BROWSER=firefox` (or a `cookies.txt` path) in `.env`. |
| yt-dlp "No supported JavaScript runtime" | Install deno, or set `YTDLP_JS_RUNTIMES=node`. |
| Any yt-dlp extractor error | `pip install -U yt-dlp` first. Code changes are rarely needed. |
| TikTok-Api `EmptyResponseException` / `InvalidResponseException` | Bot detection. Set `TIKTOK_MS_TOKEN` (the `msToken` cookie from a logged-in tiktok.com tab) and try `TIKTOK_HEADLESS=false`. Do not patch the library; the pipeline falls back to yt-dlp. |
| faster-whisper `unexpected keyword argument 'metadata_errors'` | PyAV ≥ 19 is installed. `requirements.txt` pins `av<19`. |
