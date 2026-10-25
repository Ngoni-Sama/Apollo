from unittest.mock import MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

import downloader
from config import Settings


def _fake_ydl(info, create_file=None):
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl

    def extract(url, download):
        if create_file:
            create_file.write_bytes(b"x")
        return info
    ydl.extract_info.side_effect = extract
    return ydl


def test_download_audio_options_and_result(tmp_path):
    info = {"id": "abc", "title": "T", "uploader_id": "@Creator", "duration": 61, "upload_date": "20260101"}
    s = Settings(ytdlp_cookies_file=tmp_path / "c.txt", ytdlp_js_runtimes=("node",))
    with patch.object(downloader.yt_dlp, "YoutubeDL", return_value=_fake_ydl(info, tmp_path / "abc.mp3")) as cls:
        res = downloader.download_audio("https://youtu.be/abc", tmp_path, s)
    opts = cls.call_args.args[0]
    assert opts["postprocessors"][0] == {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}
    assert opts["format"] == "bestaudio/best"
    assert opts["cookiefile"] == str(tmp_path / "c.txt")
    assert opts["js_runtimes"] == {"node": {}}
    assert (res.video_id, res.uploader, res.duration_sec) == ("abc", "Creator", 61)


def test_download_error_wrapped_and_block_detected(tmp_path):
    ydl = _fake_ydl({})
    ydl.extract_info.side_effect = DownloadError("ERROR: [youtube] x: Sign in to confirm you're not a bot")
    with patch.object(downloader.yt_dlp, "YoutubeDL", return_value=ydl):
        with pytest.raises(downloader.DownloadFailed) as ei:
            downloader.download_audio("u", tmp_path, Settings())
    assert ei.value.is_block


def test_non_block_error():
    assert not downloader.DownloadFailed("Video unavailable. This video is private").is_block


def test_list_flat_filters_empty_entries():
    info = {"entries": [{"id": "a"}, None, {"id": "b"}]}
    with patch.object(downloader.yt_dlp, "YoutubeDL", return_value=_fake_ydl(info)) as cls:
        entries = downloader.list_flat("https://www.youtube.com/@x/videos", Settings(), limit=5)
    assert [e["id"] for e in entries] == ["a", "b"]
    assert cls.call_args.args[0]["extract_flat"] == "in_playlist"
    assert cls.call_args.args[0]["playlistend"] == 5


@pytest.mark.parametrize("info,expected", [
    ({"uploader_id": "@handle", "uploader": "Display"}, "handle"),
    ({"uploader_id": "UC123", "uploader": "Display"}, "Display"),
    ({"uploader": None, "channel": "Chan"}, "Chan"),
])
def test_uploader_name(info, expected):
    assert downloader.uploader_name(info) == expected
