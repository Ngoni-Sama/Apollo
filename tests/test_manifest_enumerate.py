import json

import pytest

import downloader
import enumerate as enum_mod
import manifest
import tiktok_fetch


def test_manifest_roundtrip_and_merge(tmp_path):
    p = tmp_path / "m.jsonl"
    manifest.write_manifest(p, [{"video_id": "a", "title": "", "url": "u/a", "upload_date": None, "status": "done"}])
    existing = manifest.read_manifest(p)
    merged, added = manifest.merge(existing, [
        {"video_id": "a", "title": "", "url": "u/a", "upload_date": None, "status": "pending"},
        {"video_id": "b", "title": "", "url": "u/b", "upload_date": None, "status": "pending"},
        {"video_id": "b", "title": "", "url": "u/b", "upload_date": None, "status": "pending"},
    ])
    assert added == 1
    assert [(i["video_id"], i["status"]) for i in merged] == [("a", "done"), ("b", "pending")]


def test_permanently_failed():
    assert not manifest.is_permanently_failed({"status": "failed", "attempts": 1})
    assert manifest.is_permanently_failed({"status": "failed", "attempts": 2})


@pytest.mark.parametrize("channel,expected", [
    ("@Creator", "https://www.youtube.com/@Creator/shorts"),
    ("Creator", "https://www.youtube.com/@Creator/shorts"),
    ("https://www.youtube.com/@Creator/videos/", "https://www.youtube.com/@Creator/shorts"),
])
def test_youtube_channel_url(channel, expected):
    assert enum_mod.youtube_channel_url(channel, "shorts") == expected


def test_enumerate_youtube_tabs(monkeypatch):
    calls = []

    def fake_list(url, limit=None):
        calls.append(url)
        if url.endswith("/streams"):
            raise downloader.DownloadFailed("This channel does not have a streams tab")
        if url.endswith("/videos"):
            return [{"id": "v1", "title": "Long", "url": "https://www.youtube.com/watch?v=v1", "timestamp": 1700000000}]
        return [{"id": "s1", "title": "Short", "url": "https://www.youtube.com/watch?v=s1"}]
    monkeypatch.setattr(downloader, "list_flat", fake_list)
    items = enum_mod.enumerate_youtube("@C", ["videos", "shorts", "streams"])
    assert len(calls) == 3
    assert [(i["video_id"], i["url"], i["upload_date"]) for i in items] == [
        ("v1", "https://www.youtube.com/watch?v=v1", "20231114"),
        ("s1", "https://www.youtube.com/shorts/s1", None),
    ]


def test_enumerate_tiktok_falls_back_to_ytdlp(monkeypatch):
    def fail(*a, **k):
        raise tiktok_fetch.TikTokFetchError("EmptyResponseException")
    monkeypatch.setattr(tiktok_fetch, "list_user_videos", fail)
    monkeypatch.setattr(downloader, "list_flat", lambda url, limit=None: [{"id": "77", "title": "t"}])
    items = enum_mod.enumerate_tiktok("@coach")
    assert items[0]["url"] == "https://www.tiktok.com/@coach/video/77"


def test_main_writes_manifest_and_preserves_status(data_dir, monkeypatch, caplog):
    out = data_dir / "manifest" / "youtube" / "C.jsonl"
    manifest.write_manifest(out, [{"video_id": "v1", "title": "", "url": "x", "upload_date": None, "status": "done"}])
    monkeypatch.setattr(downloader, "list_flat", lambda url, limit=None: [{"id": "v1"}, {"id": "v2"}])
    assert enum_mod.main(["--platform", "youtube", "--channel", "@C", "--tabs", "videos"]) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [(r["video_id"], r["status"]) for r in rows] == [("v1", "done"), ("v2", "pending")]


def test_main_tiktok_warns_incomplete(data_dir, monkeypatch, caplog):
    monkeypatch.setattr(enum_mod, "enumerate_tiktok", lambda channel, limit: [
        {"video_id": "1", "title": "", "url": "u", "upload_date": None, "status": "pending"}])
    assert enum_mod.main(["--platform", "tiktok", "--channel", "@coach"]) == 0
    assert "INCOMPLETE" in caplog.text
