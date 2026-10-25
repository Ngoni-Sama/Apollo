"""Manifest JSONL I/O. One object per line:
{"video_id", "title", "url", "upload_date", "status": "pending|done|failed", "attempts"?, "error"?}

The manifest is the resume point between enumeration and transcription.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

STATUSES = ("pending", "done", "failed")
MAX_ATTEMPTS = 2  # first try + one retry, then the item is permanently failed


class ManifestItem(TypedDict, total=False):
    video_id: str
    title: str
    url: str
    upload_date: str | None
    status: str
    attempts: int
    error: str


def read_manifest(path: Path) -> list[ManifestItem]:
    if not path.exists():
        return []
    items: list[ManifestItem] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if "video_id" not in item or "url" not in item:
            raise ValueError(f"{path}:{n}: manifest line missing video_id/url")
        item.setdefault("status", "pending")
        items.append(item)
    return items


def write_manifest(path: Path, items: list[ManifestItem]) -> None:
    """Atomic rewrite so an interrupted run never leaves a half-written manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    tmp.replace(path)


def merge(existing: list[ManifestItem], new: list[ManifestItem]) -> tuple[list[ManifestItem], int]:
    """Append unseen videos as pending; keep existing items (and their status) untouched."""
    seen = {i["video_id"] for i in existing}
    added = [i for i in new if i["video_id"] not in seen and not seen.add(i["video_id"])]
    return existing + added, len(added)


def is_permanently_failed(item: ManifestItem) -> bool:
    return item.get("status") == "failed" and item.get("attempts", 0) >= MAX_ATTEMPTS
