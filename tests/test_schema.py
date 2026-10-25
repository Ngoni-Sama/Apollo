import json

import pytest

import schema
from conftest import seg


def test_make_record_fills_text_and_duration():
    r = schema.make_record(platform="youtube", creator="abc", video_id="x" * 11,
                           source_url="https://youtu.be/xxxxxxxxxxx", method="captions", language="en",
                           segments=[seg(0, 1.5, "hello "), seg(1.5, 3.25, " world")])
    assert r["text"] == "hello world"
    assert r["duration_sec"] == 3.25
    assert r["fetched_at"].endswith("+00:00")
    schema.validate_record(r)


def test_attribution_required():
    with pytest.raises(ValueError):
        schema.make_record(platform="tiktok", creator="", video_id="1", source_url="u",
                           method="asr", language="en", segments=[])


def test_validate_rejects_bad_records():
    good = schema.make_record(platform="tiktok", creator="c", video_id="1", source_url="u",
                              method="asr", language="en", segments=[seg(0, 1, "a")])
    for key, bad in [("platform", "vimeo"), ("method", "manual"), ("segments", [{"start": 0}])]:
        with pytest.raises(ValueError):
            schema.validate_record({**good, key: bad})


def test_paths_and_write(data_dir):
    p = schema.transcript_path("youtube", "@Some Creator", "abc")
    assert p == data_dir / "transcripts" / "youtube" / "Some_Creator" / "abc.json"
    assert schema.transcript_path("youtube", "c", "v", raw=True).parts[-4:] == ("raw", "youtube", "c", "v.json")
    r = schema.make_record(platform="youtube", creator="c", video_id="v", source_url="u",
                           method="asr", language="en", segments=[seg(0, 1, "a")])
    schema.write_record(r, p)
    assert json.loads(p.read_text())["creator"] == "c"
