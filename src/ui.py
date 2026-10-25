"""Local web UI: watch creators, run jobs, browse transcripts. Standard library only.

    python src/ui.py            # then open http://127.0.0.1:8765

Binds to 127.0.0.1 only. Long work (check, enumerate, transcribe, build dataset) runs the
existing CLIs as ONE background subprocess at a time, so the --sleep throttle is preserved.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import config
import manifest
import watch
from schema import safe_name

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "ui_static"
PLATFORMS = ("youtube", "tiktok")
EXIT_MEANINGS = {0: "finished", 1: "finished with failures", 2: "stopped: platform is blocking your IP"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --- background jobs ---------------------------------------------------------

@dataclass
class Job:
    id: int
    name: str
    args: list[str]
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    returncode: int | None = None
    lines: deque = field(default_factory=lambda: deque(maxlen=1000))
    proc: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        return self.returncode is None

    def as_dict(self, with_lines: bool = True) -> dict[str, Any]:
        d = {"id": self.id, "name": self.name, "running": self.running, "returncode": self.returncode,
             "outcome": None if self.running else EXIT_MEANINGS.get(self.returncode, f"exit {self.returncode}"),
             "started_at": self.started_at, "finished_at": self.finished_at}
        if with_lines:
            d["lines"] = list(self.lines)
        return d


class JobRunner:
    """Runs one CLI subprocess at a time and keeps its output for the UI."""

    def __init__(self, cwd: Path = config.ROOT):
        self.cwd = cwd
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.current: Job | None = None
        self.history: deque[Job] = deque(maxlen=20)

    def start(self, name: str, args: list[str]) -> Job:
        with self._lock:
            if self.current and self.current.running:
                raise ApiError(HTTPStatus.CONFLICT, f"'{self.current.name}' is still running")
            job = Job(next(self._ids), name, args)
            env = os.environ | {"PYTHONUNBUFFERED": "1"}
            job.proc = subprocess.Popen([sys.executable, *args], cwd=self.cwd, env=env, text=True, bufsize=1,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.current = job
            self.history.appendleft(job)
        threading.Thread(target=self._pump, args=(job,), daemon=True).start()
        return job

    def _pump(self, job: Job) -> None:
        assert job.proc and job.proc.stdout
        for line in job.proc.stdout:
            job.lines.append(line.rstrip("\n"))
        job.returncode = job.proc.wait()
        job.finished_at = time.time()

    def stop(self) -> bool:
        job = self.current
        if not job or not job.running or not job.proc:
            return False
        job.proc.terminate()
        job.lines.append("[stopped from the UI]")
        return True


# --- data access -------------------------------------------------------------

def _platform(value: Any) -> str:
    if value not in PLATFORMS:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"platform must be one of {PLATFORMS}")
    return value


def _name(value: Any, what: str) -> str:
    """Accept only values that are already safe path components (blocks ../ traversal)."""
    if not isinstance(value, str) or not value or safe_name(value) != value:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {what}")
    return value


def manifest_summaries() -> list[dict[str, Any]]:
    out = []
    for platform in PLATFORMS:
        for path in sorted((config.MANIFEST_DIR / platform).glob("*.jsonl")):
            items = manifest.read_manifest(path)
            counts = {s: sum(1 for i in items if i.get("status") == s) for s in manifest.STATUSES}
            out.append({"platform": platform, "creator": path.stem, "total": len(items), **counts,
                        "permanently_failed": sum(1 for i in items if manifest.is_permanently_failed(i))})
    return out


def _iter_transcript_files():
    for platform in PLATFORMS:
        yield from sorted((config.TRANSCRIPTS_DIR / platform).glob("*/*.json"))


def search_transcripts(q: str = "", creator: str = "", limit: int = 200) -> list[dict[str, Any]]:
    q = q.strip().lower()
    results = []
    for path in _iter_transcript_files():
        if creator and path.parent.name != creator:
            continue
        rec = json.loads(path.read_text(encoding="utf-8"))
        text = rec.get("text", "")
        snippet = text[:220]
        if q:
            pos = text.lower().find(q)
            if pos < 0:
                continue
            start = max(0, pos - 100)
            snippet = ("…" if start else "") + text[start:pos + len(q) + 120] + "…"
        results.append({"platform": rec["platform"], "creator": path.parent.name, "video_id": path.stem,
                        "source_url": rec["source_url"], "method": rec["method"],
                        "duration_sec": rec.get("duration_sec"), "fetched_at": rec.get("fetched_at"),
                        "words": len(text.split()), "snippet": snippet})
    results.sort(key=lambda r: r.get("fetched_at") or "", reverse=True)
    return results[:limit]


def read_transcript(platform: str, creator: str, video_id: str) -> dict[str, Any]:
    path = config.TRANSCRIPTS_DIR / _platform(platform) / _name(creator, "creator") / f"{_name(video_id, 'video_id')}.json"
    if not path.exists():
        raise ApiError(HTTPStatus.NOT_FOUND, "transcript not found")
    return json.loads(path.read_text(encoding="utf-8"))


def recent_new_videos(limit: int = 50) -> list[dict[str, Any]]:
    path = watch.log_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(line) for line in reversed(lines) if line.strip()]


def dataset_counts() -> dict[str, int]:
    counts = {}
    for name in ("chunks", "qa"):
        p = config.DATASET_DIR / f"{name}.jsonl"
        counts[name] = sum(1 for _ in p.open(encoding="utf-8")) if p.exists() else 0
    return counts


# --- HTTP --------------------------------------------------------------------

class App:
    def __init__(self, runner: JobRunner | None = None):
        self.runner = runner or JobRunner()

    def status(self) -> dict[str, Any]:
        summaries = manifest_summaries()
        return {
            "watching": len(watch.load_watchlist()),
            "transcripts": sum(1 for _ in _iter_transcript_files()),
            "pending": sum(m["pending"] for m in summaries),
            "failed": sum(m["failed"] for m in summaries),
            "dataset": dataset_counts(),
            "job": self.runner.current.as_dict(with_lines=False) if self.runner.current else None,
            "ntfy": bool(os.environ.get("WATCH_NTFY_TOPIC", "").strip()),
        }

    def get(self, path: str, query: dict[str, str]) -> Any:
        if path == "/api/status":
            return self.status()
        if path == "/api/watchlist":
            return [{k: e.get(k) for k in ("platform", "channel", "creator", "added_at", "last_checked", "skip_shorts")}
                    for e in watch.load_watchlist()]
        if path == "/api/manifests":
            return manifest_summaries()
        if path == "/api/manifest":
            p = config.MANIFEST_DIR / _platform(query.get("platform")) / f"{_name(query.get('creator'), 'creator')}.jsonl"
            return manifest.read_manifest(p)
        if path == "/api/new":
            return recent_new_videos()
        if path == "/api/transcripts":
            return search_transcripts(query.get("q", ""), query.get("creator", ""))
        if path == "/api/transcript":
            return read_transcript(query.get("platform", ""), query.get("creator", ""), query.get("video_id", ""))
        if path == "/api/job":
            job = self.runner.current
            return job.as_dict() if job else None
        if path == "/api/jobs":
            return [j.as_dict(with_lines=False) for j in self.runner.history]
        raise ApiError(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def post(self, path: str, body: dict[str, Any]) -> Any:
        if path == "/api/watch":
            platform = _platform(body.get("platform"))
            channel = str(body.get("channel", "")).strip()
            if not re.fullmatch(r"@?[\w.\-]{2,100}|UC[\w-]{22}|https://(www\.)?(youtube|tiktok)\.com/\S+", channel):
                raise ApiError(HTTPStatus.BAD_REQUEST, "channel should look like @handle")
            try:
                entry = watch.add(platform, channel, bool(body.get("backfill")), bool(body.get("skip_shorts")))
            except ValueError as exc:
                raise ApiError(HTTPStatus.CONFLICT, str(exc)) from exc
            except Exception as exc:  # network / lookup failures are shown to the user as-is
                raise ApiError(HTTPStatus.BAD_GATEWAY, f"could not add {channel}: {exc}") from exc
            return {"creator": entry["creator"], "baseline": len(entry.get("seen", []))}
        if path == "/api/unwatch":
            platform = _platform(body.get("platform"))
            removed = watch.remove(platform, _name(body.get("creator"), "creator"))
            return {"removed": removed}
        if path == "/api/jobs/stop":
            return {"stopped": self.runner.stop()}
        if path == "/api/jobs":
            name, args = self._job_args(body)
            return self.runner.start(name, args).as_dict()
        raise ApiError(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def _job_args(self, body: dict[str, Any]) -> tuple[str, list[str]]:
        """Map a job request to a fixed CLI command. Arguments never reach a shell."""
        kind = body.get("kind")
        if kind == "check":
            args = ["src/watch.py", "check"] + (["--transcribe"] if body.get("transcribe") else [])
            return ("Check for new videos" + (" + transcribe" if body.get("transcribe") else ""), args)
        if kind == "transcribe_manifest":
            platform, creator = _platform(body.get("platform")), _name(body.get("creator"), "creator")
            path = config.MANIFEST_DIR / platform / f"{creator}.jsonl"
            if not path.exists():
                raise ApiError(HTTPStatus.NOT_FOUND, "no video list for this creator yet; list their videos first")
            return f"Transcribe {platform}/{creator}", ["src/pipeline.py", "--manifest", str(path)]
        if kind == "transcribe_url":
            url = str(body.get("url", "")).strip()
            if not re.fullmatch(r"https://(www\.|m\.|vm\.)?(youtube\.com|youtu\.be|tiktok\.com)/\S+", url):
                raise ApiError(HTTPStatus.BAD_REQUEST, "paste a full YouTube or TikTok video URL")
            args = ["src/pipeline.py", "--url", url]
            if body.get("creator"):
                args += ["--creator", _name(body["creator"], "creator")]
            return f"Transcribe {url}", args
        if kind == "enumerate":
            platform, creator = _platform(body.get("platform")), _name(body.get("creator"), "creator")
            entry = next((e for e in watch.load_watchlist()
                          if e["platform"] == platform and e["creator"] == creator), None)
            if not entry:
                raise ApiError(HTTPStatus.NOT_FOUND, "add this creator first")
            args = ["src/enumerate.py", "--platform", platform, "--channel", entry["channel"]]
            if platform == "youtube":
                tabs = body.get("tabs") or ("videos" if entry.get("skip_shorts") else "videos,shorts")
                if not re.fullmatch(r"(videos|shorts|streams)(,(videos|shorts|streams))*", tabs):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "bad tabs")
                args += ["--tabs", tabs]
            return f"List all videos: {platform}/{creator}", args
        if kind == "dataset":
            return "Build dataset", ["scripts/build_dataset.py"]
        if kind == "smoke":
            urls = [u for u in body.get("urls", []) if isinstance(u, str)]
            args = ["scripts/smoke_test.py"]
            for u in urls[:5]:
                u = u.strip()
                if "tiktok.com" in u:
                    args += ["--tiktok", u]
                elif "youtu" in u:
                    args += ["--youtube", u]
            if len(args) == 1:
                raise ApiError(HTTPStatus.BAD_REQUEST, "give 1-5 YouTube/TikTok URLs")
            return "Smoke test", args
        raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown job kind {kind!r}")


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "Apollo"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: Any) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _guard(self) -> bool:
            # Blocks DNS-rebinding: only answer requests addressed to localhost.
            if self.headers.get("Host") not in allowed_hosts:
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
                return False
            return True

        def do_GET(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self._send(HTTPStatus.OK, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            self._dispatch(lambda: app.get(url.path, query))

        def do_POST(self):
            if not self._guard():
                return
            # JSON-only POSTs can't be sent cross-site without a CORS preflight, which we never approve.
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON only"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            self._dispatch(lambda: app.post(urlparse(self.path).path, body if isinstance(body, dict) else {}))

        def _dispatch(self, fn) -> None:
            try:
                self._json(HTTPStatus.OK, fn())
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except Exception as exc:
                log.exception("request failed")
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def serve(port: int = 8765, open_browser: bool = False) -> None:
    app = App()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    url = f"http://127.0.0.1:{port}"
    log.info("Apollo UI running at %s  (Ctrl+C to stop)", url)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.runner.stop()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apollo local web UI")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the browser automatically")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    config.setup_logging(args.verbose)
    serve(args.port, args.open)
    return 0


if __name__ == "__main__":
    sys.exit(main())
