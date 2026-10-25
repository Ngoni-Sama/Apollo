from clean import clean_segments, clean_text, find_sponsor_spans, remove_sponsor_segments
from conftest import seg


def test_clean_text():
    assert clean_text("[Music] So um, I I think uh the the key is, hmm, sleep.") == "So I think the key is, sleep."
    assert clean_text("♪ ♪") == ""
    # meaningful words that look like fillers stay
    assert clean_text("Uhh the human body. Umbrella. Ahead.") == "the human body. Umbrella. Ahead."


def test_clean_segments_drops_empty():
    out = clean_segments([seg(0, 1, "[Applause]"), seg(1, 2, "uh hello")])
    assert out == [seg(1, 2, "hello")]


def test_sponsor_spans_merge_and_remove():
    segs = [seg(0, 10, "Today we talk about sleep."),
            seg(60, 65, "This video is sponsored by AcmeGreens."),
            seg(65, 75, "They make a drink."),
            seg(75, 80, "Use code SLEEP for 20% off."),
            seg(120, 130, "Back to sleep science.")]
    spans = find_sponsor_spans(segs)
    assert spans == [(58.0, 100.0)]
    kept = remove_sponsor_segments(segs)
    assert [s["start"] for s in kept] == [0, 120]


def test_no_sponsor_is_noop():
    segs = [seg(0, 1, "Deadlift form matters.")]
    assert remove_sponsor_segments(segs) == segs
