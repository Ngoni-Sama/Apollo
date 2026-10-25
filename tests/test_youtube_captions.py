from unittest.mock import MagicMock

import pytest
from youtube_transcript_api import IpBlocked, TranscriptsDisabled

import youtube_captions as yc


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s",
    "https://youtu.be/dQw4w9WgXcQ?si=abc",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://m.youtube.com/live/dQw4w9WgXcQ",
    "dQw4w9WgXcQ",
])
def test_extract_video_id(url):
    assert yc.extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_video_id_rejects_channel():
    with pytest.raises(ValueError):
        yc.extract_video_id("https://www.youtube.com/@hubermanlab/videos")


def test_fetch_uses_instance_api_and_maps_segments():
    fetched = MagicMock(language_code="en", is_generated=True)
    fetched.to_raw_data.return_value = [
        {"text": "hello\nthere", "start": 0.0, "duration": 1.5},
        {"text": "  ", "start": 1.5, "duration": 0.5},
        {"text": "world", "start": 2.0, "duration": 1.0},
    ]
    api = MagicMock()
    api.fetch.return_value = fetched
    res = yc.fetch_captions("dQw4w9WgXcQ", api=api)
    api.fetch.assert_called_once_with("dQw4w9WgXcQ", languages=("en",))
    assert res.segments == [{"start": 0.0, "end": 1.5, "text": "hello there"},
                            {"start": 2.0, "end": 3.0, "text": "world"}]


def test_ip_block_raises_captions_blocked_without_retry():
    api = MagicMock()
    api.fetch.side_effect = IpBlocked("dQw4w9WgXcQ")
    with pytest.raises(yc.CaptionsBlocked):
        yc.fetch_captions("dQw4w9WgXcQ", api=api)
    assert api.fetch.call_count == 1


def test_no_captions_raises_captions_error():
    api = MagicMock()
    api.fetch.side_effect = TranscriptsDisabled("dQw4w9WgXcQ")
    with pytest.raises(yc.CaptionsError) as ei:
        yc.fetch_captions("dQw4w9WgXcQ", api=api)
    assert not isinstance(ei.value, yc.CaptionsBlocked)
