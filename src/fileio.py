"""Cross-platform file locks and atomic writes.

Needed because the hourly scheduled check and jobs started from the UI are separate processes
that touch the same manifests. Locks are OS-level (fcntl / msvcrt), so they are released
automatically if a process crashes: no stale lock files.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockBusy(Exception):
    """Another process holds the lock."""


def _try_lock(f) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(f) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def file_lock(path: Path, timeout: float = 0.0) -> Iterator[None]:
    """Hold an exclusive lock on `path`. Waits up to `timeout` seconds, then raises LockBusy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    deadline = time.monotonic() + timeout
    try:
        while not _try_lock(f):
            if time.monotonic() >= deadline:
                raise LockBusy(str(path))
            time.sleep(0.1)
        try:
            yield
        finally:
            _unlock(f)
    finally:
        f.close()


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file + rename. On Windows the rename can briefly fail while another
    process (or antivirus) has the target open, so that specific error gets a few short retries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.1)
