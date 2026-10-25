import json

import pytest

import downloader
import manifest
import watch

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <entry><yt:videoId>NEW00000001</yt:videoId><title>Sleep &amp; focus</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=NEW00000001"/>
  <published>2026-10-24T10:00:00+00:00</published></entry>
 <entry><yt:videoId>OLD00000001</yt:videoId><title>Old one</title>
  <link rel="alternate" href="https://www.youtube.com/shorts/OLD00000001"/>
  <published>2026-10-01T10:00:00+00:00</published></entry>
</feed>"""


def test_parse_youtube_feed():
    items = watch.parse_youtube_feed(FEED)
    assert [(i["video_id"], i["title"], i["upload_date"]) for i in items] == [
        ("NEW00000001", "Sleep & focus", "20261024"), ("OLD00000001", "Old one", "20261001")]
    assert items[1]["url"] == "https://www.youtube.com/shorts/OLD00000001"


@pytest.fixture
def feed(monkeypatch):
    state = {"items": watch.parse_youtube_feed(FEED)[1:]}  # only the old video at first
    monkeypatch.setattr(watch, "fetch_youtube_feed", lambda cid: state["items"])
    monkeypatch.setattr(downloader, "channel_id", lambda url, settings=None: "UC" + "x" * 22)
    return state


def test_add_sets_baseline_without_queueing(data_dir, feed):
    entry = watch.add("youtube", "@Huber man")
    assert entry["creator"] == "Huber_man" and entry["channel_id"].startswith("UC")
    assert entry["seen"] == ["OLD00000001"]
    assert not (data_dir / "manifest/youtube/Huber_man.jsonl").exists()
    with pytest.raises(ValueError):
        watch.add("youtube", "@Huber man")


def test_add_backfill_queues_current(data_dir, feed):
    watch.add("youtube", "@C", backfill=True)
    rows = manifest.read_manifest(data_dir / "manifest/youtube/C.jsonl")
    assert [r["video_id"] for r in rows] == ["OLD00000001"]


def test_check_finds_only_new_and_is_idempotent(data_dir, feed, monkeypatch):
    sent = []
    monkeypatch.setattr(watch, "notify", lambda title, msg, url=None: sent.append((title, msg)))
    watch.add("youtube", "@C")
    feed["items"] = watch.parse_youtube_feed(FEED)

    found, failed = watch.check(sleep=0)
    path = str(data_dir / "manifest/youtube/C.jsonl")
    assert failed == [] and [i["video_id"] for i in found[path]] == ["NEW00000001"]
    rows = manifest.read_manifest(data_dir / "manifest/youtube/C.jsonl")
    assert [(r["video_id"], r["status"]) for r in rows] == [("NEW00000001", "pending")]
    assert sent == [("New youtube video: C", "Sleep & focus")]
    logged = [json.loads(line) for line in (data_dir / "watch_log.jsonl").read_text().splitlines()]
    assert logged[0]["video_id"] == "NEW00000001" and logged[0]["creator"] == "C"

    assert watch.check(sleep=0) == ({}, [])  # second check: nothing new


def test_check_skips_videos_already_in_manifest(data_dir, feed):
    watch.add("youtube", "@C")
    manifest.write_manifest(data_dir / "manifest/youtube/C.jsonl",
                            [{"video_id": "NEW00000001", "url": "u", "status": "done"}])
    feed["items"] = watch.parse_youtube_feed(FEED)
    assert watch.check(sleep=0) == ({}, [])


def test_one_failing_channel_does_not_stop_others(data_dir, feed, monkeypatch):
    watch.add("youtube", "@A")
    watch.add("youtube", "@B")
    feed["items"] = watch.parse_youtube_feed(FEED)
    real = watch.latest

    def flaky(entry):
        if entry["creator"] == "A":
            raise OSError("network down")
        return real(entry)
    monkeypatch.setattr(watch, "latest", flaky)
    found, failed = watch.check(sleep=0)
    assert failed == ["youtube/A"] and len(found) == 1


def test_tiktok_uses_best_effort_enumeration(data_dir, monkeypatch):
    calls = []

    def fake(channel, limit):
        calls.append((channel, limit))
        return [{"video_id": "9", "title": "t", "url": "https://www.tiktok.com/@coach/video/9",
                 "upload_date": None, "status": "pending"}]
    monkeypatch.setattr(watch.enumerate_mod, "enumerate_tiktok", fake)
    entry = watch.add("tiktok", "@coach")
    assert "channel_id" not in entry and calls == [("@coach", watch.TIKTOK_CHECK_LIMIT)]


def test_remove(data_dir, feed):
    watch.add("youtube", "@C")
    assert watch.remove("youtube", "@C") is True
    assert watch.remove("youtube", "@C") is False


def test_notify_is_noop_without_topic(monkeypatch):
    monkeypatch.delenv("WATCH_NTFY_TOPIC", raising=False)
    monkeypatch.setattr(watch.urllib.request, "urlopen", lambda *a, **k: pytest.fail("should not send"))
    watch.notify("t", "m")


def test_skip_shorts_on_check_and_backfill(data_dir, feed):
    # FEED has one regular video (NEW00000001) and one Short (OLD00000001, /shorts/ URL)
    feed["items"] = watch.parse_youtube_feed(FEED)
    entry = watch.add("youtube", "@Pod", backfill=True, skip_shorts=True)
    assert entry["skip_shorts"] is True
    rows = manifest.read_manifest(data_dir / "manifest/youtube/Pod.jsonl")
    assert [r["video_id"] for r in rows] == ["NEW00000001"]  # the Short was not queued

    # a new Short appears later: it is remembered as seen but never queued
    short = dict(watch.parse_youtube_feed(FEED)[1], video_id="SHORT000002", url="https://www.youtube.com/shorts/SHORT000002")
    feed["items"] = [short] + feed["items"]
    assert watch.check(sleep=0) == ({}, [])
    assert "SHORT000002" in watch.load_watchlist()[0]["seen"]


def test_skip_shorts_ignored_for_tiktok(data_dir, monkeypatch):
    monkeypatch.setattr(watch.enumerate_mod, "enumerate_tiktok", lambda channel, limit: [])
    assert watch.add("tiktok", "@coach", skip_shorts=True)["skip_shorts"] is False
