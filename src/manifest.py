"""Manifest JSONL I/O. One object per line:
{"video_id", "title", "url", "upload_date", "status": "pending|done|failed", "attempts"?, "error"?}

The manifest is the resume point between enumeration and transcription.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from fileio import atomic_write_text, file_lock

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
    """Atomic rewrite so an interrupted run never leaves a half-written manifest.
    Prefer update()/add_items()/patch() when another process might be writing too."""
    atomic_write_text(path, "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))


def update(path: Path, fn: Callable[[list[ManifestItem]], Any]) -> Any:
    """Locked read-modify-write: `fn` mutates the freshly read items in place."""
    with file_lock(path.with_name(path.name + ".lock"), timeout=30):
        items = read_manifest(path)
        result = fn(items)
        write_manifest(path, items)
        return result


def add_items(path: Path, new: list[ManifestItem]) -> int:
    """Append unseen videos as pending (locked). Returns how many were added."""
    def apply(items: list[ManifestItem]) -> int:
        merged, added = merge(items, new)
        items[:] = merged
        return added
    return update(path, apply)


def patch(path: Path, video_id: str, fields: dict[str, Any], drop: tuple[str, ...] = ()) -> None:
    """Update one item's fields (locked), leaving every other line as it is on disk."""
    def apply(items: list[ManifestItem]) -> None:
        for item in items:
            if item["video_id"] == video_id:
                item.update(fields)
                for key in drop:
                    item.pop(key, None)
    update(path, apply)


def merge(existing: list[ManifestItem], new: list[ManifestItem]) -> tuple[list[ManifestItem], int]:
    """Append unseen videos as pending; keep existing items (and their status) untouched."""
    seen = {i["video_id"] for i in existing}
    added = [i for i in new if i["video_id"] not in seen and not seen.add(i["video_id"])]
    return existing + added, len(added)


def is_permanently_failed(item: ManifestItem) -> bool:
    return item.get("status") == "failed" and item.get("attempts", 0) >= MAX_ATTEMPTS
