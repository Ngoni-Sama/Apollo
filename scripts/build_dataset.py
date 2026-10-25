"""Cleaned transcripts -> training/RAG datasets in data/dataset/.

Outputs (every row keeps creator + source_url for attribution):
  chunks.jsonl  token-bounded, timestamped chunks with topic tags (for RAG / continued pretraining)
  qa.jsonl      chat-format pairs mined from questions the creator asks and then answers
                (heuristic, no LLM; needs punctuated text, so ASR transcripts work best —
                YouTube auto-captions usually have no '?' and yield nothing)

Personal use only: do not redistribute these files or train commercial products on them.
"""

from __future__ import annotations

import argparse
import bisect
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402
from chunk import chunk_segments, tag_topics  # noqa: E402
from schema import validate_record  # noqa: E402

log = logging.getLogger("build_dataset")

_SENT_RE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$")


def iter_transcripts(root: Path, creators: set[str] | None = None) -> Iterator[dict[str, Any]]:
    for platform in ("youtube", "tiktok"):
        for path in sorted((root / platform).glob("*/*.json")):
            if creators and path.parent.name not in creators:
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            try:
                validate_record(record)
            except ValueError as exc:
                log.warning("skip %s: %s", path, exc)
                continue
            yield record


def sentences_with_times(record: dict[str, Any]) -> list[tuple[float, str]]:
    """Split the transcript into sentences, each tagged with the start time of its segment."""
    starts: list[int] = []
    times: list[float] = []
    parts: list[str] = []
    offset = 0
    for seg in record["segments"]:
        starts.append(offset)
        times.append(seg["start"])
        parts.append(seg["text"])
        offset += len(seg["text"]) + 1
    text = " ".join(parts)
    out = []
    for m in _SENT_RE.finditer(text):
        sentence = m.group().strip()
        if sentence:
            first_char = m.start() + len(m.group()) - len(m.group().lstrip())
            out.append((times[max(0, bisect.bisect_right(starts, first_char) - 1)], sentence))
    return out


def mine_qa(record: dict[str, Any], min_answer_words: int = 25, max_answer_words: int = 180) -> list[dict[str, Any]]:
    """Creator poses a question, then answers it: take the following sentences as the answer."""
    sents = sentences_with_times(record)
    pairs: list[dict[str, Any]] = []
    seen: set[str] = set()
    i = 0
    while i < len(sents):
        start, sentence = sents[i]
        n_words = len(sentence.split())
        if not sentence.endswith("?") or not 5 <= n_words <= 40 or sentence.lower() in seen:
            i += 1
            continue
        answer: list[str] = []
        j = i + 1
        while j < len(sents) and not sents[j][1].endswith("?"):
            if len(" ".join(answer + [sents[j][1]]).split()) > max_answer_words:
                break
            answer.append(sents[j][1])
            j += 1
        answer_text = " ".join(answer)
        if len(answer_text.split()) >= min_answer_words:
            seen.add(sentence.lower())
            pairs.append({
                "messages": [{"role": "user", "content": sentence},
                             {"role": "assistant", "content": answer_text}],
                "meta": {"platform": record["platform"], "creator": record["creator"],
                         "video_id": record["video_id"], "source_url": record["source_url"],
                         "start": start, "topics": tag_topics(sentence + " " + answer_text, min_hits=1)},
            })
            i = j
        else:
            i += 1
    return pairs


def build(root: Path, out_dir: Path, max_tokens: int, overlap: int,
          creators: set[str] | None = None) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {"transcripts": 0, "chunks": 0, "qa": 0}
    with (out_dir / "chunks.jsonl").open("w", encoding="utf-8") as fc, \
         (out_dir / "qa.jsonl").open("w", encoding="utf-8") as fq:
        for record in iter_transcripts(root, creators):
            counts["transcripts"] += 1
            for ch in chunk_segments(record["segments"], max_tokens, overlap):
                row = {
                    "id": f"{record['platform']}:{record['video_id']}:{ch['index']}",
                    "platform": record["platform"], "creator": record["creator"],
                    "video_id": record["video_id"], "source_url": record["source_url"],
                    "method": record["method"], "start": ch["start"], "end": ch["end"],
                    "n_tokens": ch["n_tokens"], "topics": tag_topics(ch["text"]), "text": ch["text"],
                }
                fc.write(json.dumps(row, ensure_ascii=False) + "\n")
                counts["chunks"] += 1
            for pair in mine_qa(record):
                fq.write(json.dumps(pair, ensure_ascii=False) + "\n")
                counts["qa"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcripts", type=Path, default=config.TRANSCRIPTS_DIR)
    ap.add_argument("--out", type=Path, default=config.DATASET_DIR)
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--overlap", type=int, default=50)
    ap.add_argument("--creator", action="append", help="only these creator dirs (repeatable)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    config.setup_logging(args.verbose)
    counts = build(args.transcripts, args.out, args.max_tokens, args.overlap,
                   set(args.creator) if args.creator else None)
    log.info("built %s -> %s", counts, args.out)
    return 0 if counts["transcripts"] else 1


if __name__ == "__main__":
    sys.exit(main())
