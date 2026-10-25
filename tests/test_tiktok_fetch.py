import pytest

import tiktok_fetch as tf
from config import Settings


def test_parse_url():
    assert tf.parse_url("https://www.tiktok.com/@some.user/video/7206382937372134662?lang=en") == \
        ("some.user", "7206382937372134662")
    with pytest.raises(ValueError):
        tf.parse_url("https://vm.tiktok.com/ZMabc/")


def test_video_from_dict():
    v = tf._video_from_dict({"id": "123", "author": {"uniqueId": "coach"}, "desc": "leg day",
                             "createTime": "1700000000", "video": {"duration": 31}})
    assert (v.video_id, v.username, v.create_time, v.duration_sec) == ("123", "coach", 1700000000, 31.0)
    assert v.url == "https://www.tiktok.com/@coach/video/123"


def test_errors_are_wrapped(monkeypatch):
    from TikTokApi.exceptions import EmptyResponseException

    async def boom(settings, fn):
        raise EmptyResponseException(None, "TikTok returned an empty response")
    monkeypatch.setattr(tf, "_with_api", boom)
    with pytest.raises(tf.TikTokFetchError, match="EmptyResponseException"):
        tf.list_user_videos("coach", settings=Settings())


def test_list_user_videos_keeps_partial_results(monkeypatch):
    class FakeVideo:
        def __init__(self, i):
            self.as_dict = {"id": str(i), "author": "coach", "createTime": 1700000000}

    class FakeUser:
        async def videos(self, count):
            yield FakeVideo(1)
            yield FakeVideo(2)
            raise RuntimeError("rate limited")

    class FakeApi:
        def user(self, name):
            return FakeUser()

    async def fake_with_api(settings, fn):
        return await fn(FakeApi())
    monkeypatch.setattr(tf, "_with_api", fake_with_api)
    videos = tf.list_user_videos("@coach", settings=Settings())
    assert [v.video_id for v in videos] == ["1", "2"]
