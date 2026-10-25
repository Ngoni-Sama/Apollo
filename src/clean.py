"""Text cleanup: caption noise, filler words, stutters, and sponsor-segment heuristics."""

from __future__ import annotations

import re

from schema import Segment

# [Music], [Applause], (laughs), ♪ ... ♪ etc. — captions and Whisper both emit these.
_NOISE_RE = re.compile(r"\[[^\]]*\]|\((?:music|applause|laughs?|laughter|inaudible|silence)\)|[♪♫]+", re.I)
# Pure fillers only. Words like "like" / "you know" / "so" carry meaning too often to strip blindly.
_FILLER_RE = re.compile(r"(?<![\w'])(?:u+m+|u+h+|e+r+m+|a+h+|h+m+|m+h+m+|uh-huh)(?![\w'])[,.]?", re.I)
_REPEAT_RE = re.compile(r"\b(\w+)(?:[,\s]+\1\b)+", re.I)
_SPACE_RE = re.compile(r"\s+")
_SPACE_PUNCT_RE = re.compile(r"\s+([,.!?;:])")

# Phrases that strongly suggest an ad read. Heuristic: tune per creator.
SPONSOR_PATTERNS = [
    r"\bsponsored by\b", r"\btoday'?s sponsor\b", r"\bthis (?:video|episode) is (?:brought to you|sponsored)\b",
    r"\bour (?:first |next |last )?sponsor\b", r"\bpromo code\b", r"\bdiscount code\b",
    r"\buse (?:my |the )?code\b", r"\blink (?:is )?in (?:the |my )?(?:description|bio)\b",
    r"\bgo to [a-z0-9-]+\s?(?:dot|\.)\s?com\s?/", r"\bfree trial\b", r"\b\d+% off\b",
    r"\bfirst \d+ (?:people|customers)\b", r"\bcheck (?:them|it) out at\b",
]
_SPONSOR_RE = re.compile("|".join(SPONSOR_PATTERNS), re.I)


def clean_text(text: str) -> str:
    """Remove caption noise and filler words, collapse stutters and whitespace."""
    text = _NOISE_RE.sub(" ", text)
    text = _FILLER_RE.sub(" ", text)
    text = _REPEAT_RE.sub(r"\1", text)
    text = _SPACE_RE.sub(" ", text)
    text = _SPACE_PUNCT_RE.sub(r"\1", text)
    return text.strip(" ,")


def clean_segments(segments: list[Segment]) -> list[Segment]:
    """Clean each segment's text; drop segments that become empty. Timestamps are kept."""
    out: list[Segment] = []
    for seg in segments:
        text = clean_text(seg["text"])
        if text:
            out.append(Segment(start=seg["start"], end=seg["end"], text=text))
    return out


def find_sponsor_spans(
    segments: list[Segment], pad_before: float = 2.0, pad_after: float = 20.0
) -> list[tuple[float, float]]:
    """Time spans likely to be ad reads: each sponsor-phrase hit, padded (mostly forward), overlaps merged."""
    spans: list[tuple[float, float]] = []
    for seg in segments:
        if _SPONSOR_RE.search(seg["text"]):
            start, end = max(0.0, seg["start"] - pad_before), seg["end"] + pad_after
            if spans and start <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], end))
            else:
                spans.append((start, end))
    return spans


def remove_sponsor_segments(segments: list[Segment], pad_after: float = 20.0) -> list[Segment]:
    """Drop segments that fall inside a detected sponsor span (heuristic; may over- or under-cut)."""
    spans = find_sponsor_spans(segments, pad_after=pad_after)
    if not spans:
        return list(segments)
    return [s for s in segments if not any(a <= s["start"] and s["end"] <= b for a, b in spans)]
