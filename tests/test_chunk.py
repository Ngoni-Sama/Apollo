import pytest

from chunk import chunk_segments, estimate_tokens, tag_topics
from conftest import seg


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd" * 10) == 10


def test_chunks_respect_limit_and_overlap():
    segs = [seg(i, i + 1, f"sentence number {i:03d} here.") for i in range(100)]  # ~7 tokens each
    chunks = chunk_segments(segs, max_tokens=60, overlap_tokens=15)
    assert len(chunks) > 5
    assert all(c["n_tokens"] <= 60 for c in chunks)
    assert [c["index"] for c in chunks] == list(range(len(chunks)))
    # consecutive chunks overlap in time and text
    a, b = chunks[0], chunks[1]
    assert b["start"] < a["end"]
    assert a["text"].split(".")[-2] in b["text"]
    # everything is covered
    assert "sentence number 099" in chunks[-1]["text"]
    assert chunks[0]["start"] == 0 and chunks[-1]["end"] == 100


def test_oversized_segment_is_split_with_interpolated_times():
    long = seg(0.0, 100.0, " ".join(["word"] * 400))  # ~500 tokens
    chunks = chunk_segments([long], max_tokens=100, overlap_tokens=0)
    assert len(chunks) >= 5
    assert all(c["n_tokens"] <= 100 for c in chunks)
    assert chunks[0]["start"] == 0.0 and chunks[-1]["end"] == 100.0


def test_bad_overlap():
    with pytest.raises(ValueError):
        chunk_segments([], max_tokens=10, overlap_tokens=10)


def test_tag_topics():
    assert tag_topics("Set your stop loss below support before the breakout.") == ["trading"]
    assert tag_topics("Protein and progressive overload build muscle; more reps.") == ["fitness"]
    assert tag_topics("Hello world") == []
