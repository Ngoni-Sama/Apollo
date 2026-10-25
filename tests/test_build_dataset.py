import json

import build_dataset
import schema
from conftest import seg


def _write(data_dir, platform, creator, vid, segments, method="asr"):
    rec = schema.make_record(platform=platform, creator=creator, video_id=vid, source_url=f"https://x/{vid}",
                             method=method, language="en", segments=segments)
    schema.write_record(rec, schema.transcript_path(platform, creator, vid))
    return rec


ANSWER = ("You want to keep your risk per trade small, around one percent of the account, "
          "and set the stop loss below support so a single bad entry never ruins the month. "
          "That is how you survive long enough to get good.")


def test_mine_qa_pairs_question_with_answer():
    rec = {"platform": "youtube", "creator": "c", "video_id": "v", "source_url": "u", "segments": [
        seg(0, 5, "Welcome back."), seg(5, 9, "So how much should you risk on a single trade?"),
        seg(9, 30, ANSWER), seg(30, 31, "Any questions?")]}
    pairs = build_dataset.mine_qa(rec)
    assert len(pairs) == 1
    p = pairs[0]
    assert p["messages"][0]["content"] == "So how much should you risk on a single trade?"
    assert p["messages"][1]["content"] == ANSWER
    assert p["meta"]["start"] == 5 and p["meta"]["source_url"] == "u"
    assert "trading" in p["meta"]["topics"]


def test_unpunctuated_captions_yield_no_qa():
    rec = {"platform": "youtube", "creator": "c", "video_id": "v", "source_url": "u",
           "segments": [seg(0, 5, "so how much should you risk"), seg(5, 9, ANSWER.replace(".", ""))]}
    assert build_dataset.mine_qa(rec) == []


def test_build_writes_chunks_and_qa_with_attribution(data_dir):
    _write(data_dir, "youtube", "trader", "v1", [seg(0, 5, "How much should you risk on a single trade?"),
                                                 seg(5, 30, ANSWER)])
    _write(data_dir, "tiktok", "coach", "t1", [seg(0, 3, "Squat deep.")])
    # raw copies must not be included
    rec = schema.make_record(platform="youtube", creator="trader", video_id="raw1", source_url="u",
                             method="asr", language="en", segments=[seg(0, 1, "raw")])
    schema.write_record(rec, schema.transcript_path("youtube", "trader", "raw1", raw=True))

    counts = build_dataset.build(data_dir / "transcripts", data_dir / "dataset", 400, 50)
    assert counts == {"transcripts": 2, "chunks": 2, "qa": 1}
    chunks = [json.loads(line) for line in (data_dir / "dataset/chunks.jsonl").read_text().splitlines()]
    assert {c["id"] for c in chunks} == {"youtube:v1:0", "tiktok:t1:0"}
    assert all(c["creator"] and c["source_url"] for c in chunks)
    only = build_dataset.build(data_dir / "transcripts", data_dir / "ds2", 400, 50, creators={"coach"})
    assert only["transcripts"] == 1
