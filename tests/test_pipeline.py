import json
from unittest.mock import MagicMock

import pytest

import downloader
import manifest
import pipeline
import tiktok_fetch
import transcribe
import youtube_captions as yc
from conftest import seg

YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
TT = "https://www.tiktok.com/@coach/video/7206382937372134662"


@pytest.fixture
def asr(monkeypatch):
    res = transcribe.TranscriptionResult([seg(0, 2, "um so protein matters"), seg(2, 4, "[Music]")], "en", 4.0, "fw")
    m = MagicMock(return_value=res)
    monkeypatch.setattr(transcribe, "transcribe", m)
    monkeypatch.setattr(transcribe, "extract_audio", lambda v, d: d)
    return m


def _fake_audio(tmp_path, uploader="Creator"):
    path = tmp_path / "audio" / "a.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return downloader.AudioDownload(path, "dQw4w9WgXcQ", "T", uploader, 4.0, None)


def test_detect_platform():
    assert pipeline.detect_platform(YT) == "youtube"
    assert pipeline.detect_platform("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert pipeline.detect_platform(TT) == "tiktok"
    with pytest.raises(pipeline.PipelineError):
        pipeline.detect_platform("https://vimeo.com/1")


def test_youtube_captions_path(data_dir, settings, monkeypatch):
    monkeypatch.setattr(yc, "fetch_captions", lambda vid: yc.CaptionResult(vid, "en", True, [seg(0, 1, "uh hi")]))
    out = pipeline.process_url(YT, pipeline.Options(creator="Creator"), settings)
    rec = json.loads(out.read_text())
    assert (rec["method"], rec["creator"], rec["text"], rec["source_url"]) == ("captions", "Creator", "hi", YT)
    raw = json.loads((data_dir / "transcripts/raw/youtube/Creator/dQw4w9WgXcQ.json").read_text())
    assert raw["text"] == "uh hi"
    # second run skips
    assert pipeline.process_url(YT, pipeline.Options(creator="Creator"), settings) is None


def test_youtube_blocked_falls_back_to_asr(data_dir, settings, monkeypatch, asr, tmp_path, caplog):
    def blocked(vid):
        raise yc.CaptionsBlocked("YouTube blocked the caption request")
    monkeypatch.setattr(yc, "fetch_captions", blocked)
    audio = _fake_audio(tmp_path)
    monkeypatch.setattr(downloader, "download_audio", lambda url, out, s: audio)
    out = pipeline.process_url(YT, pipeline.Options(), settings)
    rec = json.loads(out.read_text())
    assert rec["method"] == "asr" and rec["creator"] == "Creator"
    assert rec["text"] == "so protein matters"
    assert not audio.path.exists()  # audio cleaned up
    assert "falling back" in caplog.text


def test_youtube_fully_blocked_raises_platform_blocked(data_dir, settings, monkeypatch):
    monkeypatch.setattr(yc, "fetch_captions", lambda vid: (_ for _ in ()).throw(yc.CaptionsBlocked("blocked")))

    def dl(url, out, s):
        raise downloader.DownloadFailed("Sign in to confirm you're not a bot")
    monkeypatch.setattr(downloader, "download_audio", dl)
    with pytest.raises(pipeline.PlatformBlocked):
        pipeline.process_url(YT, pipeline.Options(creator="c"), settings)


def test_tiktok_api_path(data_dir, settings, monkeypatch, asr):
    def dl(url, out, s):
        out.mkdir(parents=True, exist_ok=True)
        p = out / "7206382937372134662.mp4"
        p.write_bytes(b"v")
        return tiktok_fetch.TikTokVideo("7206382937372134662", "coach", url, "", None, 4.0, p)
    monkeypatch.setattr(tiktok_fetch, "download_video", dl)
    out = pipeline.process_url(TT, pipeline.Options(), settings)
    assert out == data_dir / "transcripts/tiktok/coach/7206382937372134662.json"
    assert not (data_dir / "audio/7206382937372134662.mp4").exists()


def test_tiktok_falls_back_to_ytdlp(data_dir, settings, monkeypatch, asr, tmp_path):
    monkeypatch.setattr(tiktok_fetch, "download_video",
                        lambda *a: (_ for _ in ()).throw(tiktok_fetch.TikTokFetchError("EmptyResponse")))
    dl = MagicMock(return_value=_fake_audio(tmp_path, uploader="coach"))
    monkeypatch.setattr(downloader, "download_audio", dl)
    out = pipeline.process_url(TT, pipeline.Options(), settings)
    assert json.loads(out.read_text())["creator"] == "coach"
    dl.assert_called_once()


def test_sponsor_filter_only_on_long_videos(data_dir, settings, monkeypatch):
    segs = [seg(0, 10, "Intro."), seg(200, 205, "This video is sponsored by Acme."), seg(300, 310, "Content.")]
    monkeypatch.setattr(yc, "fetch_captions", lambda vid: yc.CaptionResult(vid, "en", False, segs))
    rec = json.loads(pipeline.process_url(YT, pipeline.Options(creator="c"), settings).read_text())
    assert rec["text"] == "Intro. Content."
    assert rec["duration_sec"] == 310


def test_manifest_resume_retry_and_permanent_failure(data_dir, settings, monkeypatch):
    path = data_dir / "manifest" / "youtube" / "Creator.jsonl"
    manifest.write_manifest(path, [
        {"video_id": "a", "url": "ua", "status": "done"},
        {"video_id": "b", "url": "ub", "status": "pending"},
        {"video_id": "c", "url": "uc", "status": "pending"},
        {"video_id": "d", "url": "ud", "status": "failed", "attempts": 2},
    ])
    calls = []

    def fake(url, opts, s):
        calls.append(url)
        assert opts.creator == "Creator"
        if url == "uc":
            raise pipeline.PipelineError("no audio")
        return data_dir / "x.json"
    monkeypatch.setattr(pipeline, "process_url", fake)
    counts = pipeline.process_manifest(path, pipeline.Options(), settings, sleep=0)
    assert calls == ["ub", "uc", "uc"]  # done + permanent skipped; c retried exactly once
    assert counts == {"pending": 0, "done": 2, "failed": 2, "permanently_failed": 2, "blocked": 0}
    rows = {r["video_id"]: r for r in manifest.read_manifest(path)}
    assert rows["c"]["attempts"] == 2 and "no audio" in rows["c"]["error"]


def test_manifest_stops_on_block_without_consuming_attempts(data_dir, settings, monkeypatch):
    path = data_dir / "manifest" / "tiktok" / "coach.jsonl"
    manifest.write_manifest(path, [{"video_id": "1", "url": "u1"}, {"video_id": "2", "url": "u2"}])
    calls = []

    def fake(url, opts, s):
        calls.append(url)
        raise pipeline.PlatformBlocked("IP address is blocked")
    monkeypatch.setattr(pipeline, "process_url", fake)
    counts = pipeline.process_manifest(path, pipeline.Options(), settings, sleep=0)
    assert calls == ["u1"] and counts["blocked"] == 1
    assert all(r["status"] == "pending" and "attempts" not in r for r in manifest.read_manifest(path))


def test_manifest_sleeps_between_items(data_dir, settings, monkeypatch):
    path = data_dir / "manifest" / "youtube" / "C.jsonl"
    manifest.write_manifest(path, [{"video_id": str(i), "url": f"u{i}"} for i in range(3)])
    monkeypatch.setattr(pipeline, "process_url", lambda *a: None)
    sleeps = []
    monkeypatch.setattr(pipeline.time, "sleep", sleeps.append)
    pipeline.process_manifest(path, pipeline.Options(), settings, sleep=2.0)
    assert sleeps == [2.0, 2.0]


def test_videos_queued_during_a_run_are_not_lost(data_dir, settings, monkeypatch):
    """watch.py may add videos to the manifest while a transcription job is running."""
    path = data_dir / "manifest" / "youtube" / "C.jsonl"
    manifest.write_manifest(path, [{"video_id": "a", "url": "ua"}, {"video_id": "b", "url": "ub"}])

    def fake(url, opts, s):
        if url == "ua":  # simulate the scheduled check queueing a new upload mid-run
            manifest.add_items(path, [{"video_id": "new", "url": "unew", "status": "pending"}])
        return data_dir / "x.json"
    monkeypatch.setattr(pipeline, "process_url", fake)
    counts = pipeline.process_manifest(path, pipeline.Options(), settings, sleep=0)
    rows = {r["video_id"]: r["status"] for r in manifest.read_manifest(path)}
    assert rows == {"a": "done", "b": "done", "new": "pending"}
    assert counts["pending"] == 1


def test_second_job_exits_busy(data_dir, monkeypatch, caplog):
    path = data_dir / "manifest" / "youtube" / "C.jsonl"
    manifest.write_manifest(path, [{"video_id": "a", "url": "ua"}])
    monkeypatch.setattr(pipeline, "process_url", lambda *a: pytest.fail("must not run while busy"))
    with pipeline.job_lock():
        assert pipeline.main(["--manifest", str(path)]) == pipeline.EXIT_BUSY
    assert "already running" in caplog.text
