"""Token-aware chunking of timestamped segments, plus lightweight topic tagging.

Token counts are estimated (~4 chars/token for English) so no tokenizer dependency is needed.
Keep max_tokens comfortably below your embedding/fine-tune model's limit to absorb the error.
"""

from __future__ import annotations

import math
import re
from typing import TypedDict

from schema import Segment

CHARS_PER_TOKEN = 4.0


class Chunk(TypedDict):
    index: int
    start: float
    end: float
    n_tokens: int
    text: str


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def _split_long(seg: Segment, max_tokens: int) -> list[Segment]:
    """Split a single oversized segment on word boundaries, interpolating timestamps."""
    words = seg["text"].split()
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    pieces: list[list[str]] = [[]]
    for word in words:
        if pieces[-1] and len(" ".join(pieces[-1] + [word])) > max_chars:
            pieces.append([])
        pieces[-1].append(word)
    span = seg["end"] - seg["start"]
    out: list[Segment] = []
    done = 0
    for piece in pieces:
        a = seg["start"] + span * done / len(words)
        done += len(piece)
        b = seg["start"] + span * done / len(words)
        out.append(Segment(start=round(a, 3), end=round(b, 3), text=" ".join(piece)))
    return out


def chunk_segments(segments: list[Segment], max_tokens: int = 400, overlap_tokens: int = 50) -> list[Chunk]:
    """Greedy-pack consecutive segments into chunks of <= max_tokens, with ~overlap_tokens carried over."""
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be smaller than max_tokens")
    units: list[Segment] = []
    for seg in segments:
        units.extend(_split_long(seg, max_tokens) if estimate_tokens(seg["text"]) > max_tokens else [seg])

    chunks: list[Chunk] = []
    window: list[Segment] = []

    def tokens(segs: list[Segment]) -> int:
        return estimate_tokens(" ".join(s["text"] for s in segs))

    def flush() -> None:
        text = " ".join(s["text"] for s in window)
        chunks.append(Chunk(index=len(chunks), start=window[0]["start"], end=window[-1]["end"],
                            n_tokens=estimate_tokens(text), text=text))

    for unit in units:
        if window and tokens(window + [unit]) > max_tokens:
            flush()
            carry: list[Segment] = []
            for prev in reversed(window):
                if tokens([prev] + carry) > overlap_tokens:
                    break
                carry.insert(0, prev)
            window = carry
            # The carried overlap plus this unit may still not fit; drop overlap first.
            while window and tokens(window + [unit]) > max_tokens:
                window.pop(0)
        window.append(unit)
    if window:
        flush()
    return chunks


# Coarse keyword lexicon for the three target domains. Tags are hints for filtering, not labels.
TOPIC_LEXICON: dict[str, list[str]] = {
    "mental_health": [
        "anxiety", "depress", "therapy", "therapist", "trauma", "mindful", "meditat", "stress",
        "self-esteem", "burnout", "panic", "cbt", "emotion", "grief", "lonel", "adhd", "mental health",
    ],
    "fitness": [
        "workout", "exercise", "protein", "muscle", "hypertroph", "cardio", "squat", "deadlift",
        "bench press", "reps", "calorie", "mobility", "stretch", "endurance", "training", "fat loss",
    ],
    "trading": [
        "stock", "options", "futures", "forex", "crypto", "chart", "candle", "support", "resistance",
        "stop loss", "risk-reward", "risk reward", "position size", "entry", "breakout", "portfolio",
        "trend", "moving average", "rsi", "earnings", "market",
    ],
}
_TOPIC_RES = {
    topic: re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")", re.I)
    for topic, kws in TOPIC_LEXICON.items()
}


def tag_topics(text: str, min_hits: int = 2) -> list[str]:
    """Return topics whose keywords appear at least `min_hits` times in `text`."""
    return sorted(t for t, rx in _TOPIC_RES.items() if len(rx.findall(text)) >= min_hits)
