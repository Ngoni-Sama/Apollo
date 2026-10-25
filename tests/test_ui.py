import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import manifest
import schema
import ui
import watch
from conftest import seg


@pytest.fixture
def server(data_dir, monkeypatch):
    app = ui.App()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = srv.server_address[1]
    srv.RequestHandlerClass = ui.make_handler(app, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def call(base, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers or (
        {"Content-Type": "application/json"} if data is not None else {}))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), e.headers


def _transcript(platform="youtube", creator="trader", vid="v1", text="Set the stop loss below support."):
    rec = schema.make_record(platform=platform, creator=creator, video_id=vid,
                             source_url=f"https://www.youtube.com/watch?v={vid}", method="asr",
                             language="en", segments=[seg(0, 4, text)])
    schema.write_record(rec, schema.transcript_path(platform, creator, vid))


def test_index_served_with_csp(server):
    _, base = server
    with urllib.request.urlopen(base + "/") as r:
        body = r.read().decode()
        assert "<title>Apollo</title>" in body
        assert "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_status_and_search(server, data_dir):
    _, base = server
    _transcript()
    _transcript(creator="coach", vid="v2", text="Squat deep and brace.")
    manifest.write_manifest(data_dir / "manifest/youtube/trader.jsonl",
                            [{"video_id": "a", "url": "u", "status": "pending"}])
    status, s, _ = call(base, "/api/status")
    assert status == 200 and (s["transcripts"], s["pending"], s["job"]) == (2, 1, None)

    _, hits, _ = call(base, "/api/transcripts?q=STOP%20LOSS")
    assert [h["creator"] for h in hits] == ["trader"] and "stop loss" in hits[0]["snippet"]
    _, only, _ = call(base, "/api/transcripts?creator=coach")
    assert [h["video_id"] for h in only] == ["v2"]

    status, rec, _ = call(base, "/api/transcript?platform=youtube&creator=trader&video_id=v1")
    assert status == 200 and rec["segments"][0]["text"].startswith("Set")


@pytest.mark.parametrize("qs", [
    "platform=youtube&creator=..&video_id=v1",
    "platform=youtube&creator=trader&video_id=../../watchlist",
    "platform=etc&creator=trader&video_id=v1",
])
def test_transcript_rejects_traversal(server, qs):
    _, base = server
    status, body, _ = call(base, "/api/transcript?" + qs)
    assert status == 400


def test_bad_host_rejected(server):
    _, base = server
    status, body, _ = call(base, "/api/status", headers={"Host": "evil.example:80"})
    assert status == 403


def test_post_requires_json(server):
    _, base = server
    status, _, _ = call(base, "/api/jobs", body=None, headers={"Content-Type": "text/plain"})
    req = urllib.request.Request(base + "/api/jobs", data=b"kind=dataset",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req)
    assert ei.value.code == 415


def test_watch_add_and_remove(server, monkeypatch):
    _, base = server
    added = {}

    def fake_add(platform, channel, backfill=False):
        added.update(platform=platform, channel=channel, backfill=backfill)
        return {"creator": "coach", "seen": ["1", "2"]}
    monkeypatch.setattr(watch, "add", fake_add)
    status, body, _ = call(base, "/api/watch", {"platform": "tiktok", "channel": "@coach", "backfill": True})
    assert status == 200 and body == {"creator": "coach", "baseline": 2}
    assert added == {"platform": "tiktok", "channel": "@coach", "backfill": True}

    status, _, _ = call(base, "/api/watch", {"platform": "youtube", "channel": "x; rm -rf /"})
    assert status == 400
    status, body, _ = call(base, "/api/unwatch", {"platform": "tiktok", "creator": "coach"})
    assert status == 200 and body == {"removed": False}


def test_job_args_are_fixed_commands(data_dir):
    app = ui.App(runner=None)
    assert app._job_args({"kind": "check", "transcribe": True})[1] == ["src/watch.py", "check", "--transcribe"]
    assert app._job_args({"kind": "dataset"})[1] == ["scripts/build_dataset.py"]
    name, args = app._job_args({"kind": "transcribe_url", "url": "https://youtu.be/dQw4w9WgXcQ", "creator": "c"})
    assert args == ["src/pipeline.py", "--url", "https://youtu.be/dQw4w9WgXcQ", "--creator", "c"]
    for bad in [{"kind": "transcribe_url", "url": "file:///etc/passwd"},
                {"kind": "transcribe_url", "url": "https://youtu.be/x", "creator": "../x"},
                {"kind": "transcribe_manifest", "platform": "youtube", "creator": "nobody"},
                {"kind": "enumerate", "platform": "youtube", "creator": "nobody"},
                {"kind": "smoke", "urls": ["https://example.com"]},
                {"kind": "rm"}]:
        with pytest.raises(ui.ApiError):
            app._job_args(bad)
    smoke = app._job_args({"kind": "smoke", "urls": ["https://youtu.be/a", "https://www.tiktok.com/@x/video/1"]})[1]
    assert smoke == ["scripts/smoke_test.py", "--youtube", "https://youtu.be/a", "--tiktok", "https://www.tiktok.com/@x/video/1"]


def test_enumerate_job_uses_watched_channel(data_dir, monkeypatch):
    monkeypatch.setattr(watch, "load_watchlist",
                        lambda: [{"platform": "youtube", "channel": "@Huber", "creator": "Huber"}])
    args = ui.App(runner=None)._job_args({"kind": "enumerate", "platform": "youtube", "creator": "Huber"})[1]
    assert args == ["src/enumerate.py", "--platform", "youtube", "--channel", "@Huber", "--tabs", "videos,shorts"]


def _wait(job, timeout=10):
    end = time.time() + timeout
    while job.running and time.time() < end:
        time.sleep(0.05)


def test_job_runner_one_at_a_time_and_stop(tmp_path):
    runner = ui.JobRunner(cwd=tmp_path)
    job = runner.start("sleepy", ["-c", "import time; print('hi', flush=True); time.sleep(30)"])
    with pytest.raises(ui.ApiError) as ei:
        runner.start("second", ["-c", "pass"])
    assert ei.value.status == 409
    end = time.time() + 5
    while not job.lines and time.time() < end:
        time.sleep(0.05)
    assert runner.stop() is True
    _wait(job)
    assert not job.running and job.lines[0] == "hi"
    done = runner.start("quick", ["-c", "import sys; print('blocked'); sys.exit(2)"])
    _wait(done)
    d = done.as_dict()
    assert d["returncode"] == 2 and "blocking your IP" in d["outcome"] and d["lines"] == ["blocked"]
