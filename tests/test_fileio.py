import subprocess
import sys
import textwrap
import time

import pytest

from fileio import LockBusy, atomic_write_text, file_lock


def test_lock_excludes_other_processes(tmp_path):
    lock = tmp_path / "x.lock"
    src = str(tmp_path.parent)
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import sys, time; sys.path.insert(0, {str(__import__('fileio').__file__.rsplit('/', 1)[0])!r})
        from pathlib import Path; from fileio import file_lock
        with file_lock(Path({str(lock)!r})):
            print("held", flush=True); time.sleep(30)
    """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        start = time.monotonic()
        with pytest.raises(LockBusy):
            with file_lock(lock, timeout=0.3):
                pass
        assert time.monotonic() - start >= 0.3
    finally:
        holder.kill()
        holder.wait()
    with file_lock(lock):  # released by the OS when the holder died: no stale lock
        pass


def test_lock_is_reentrant_after_release(tmp_path):
    for _ in range(3):
        with file_lock(tmp_path / "y.lock"):
            pass


def test_atomic_write(tmp_path):
    p = tmp_path / "a" / "b.json"
    atomic_write_text(p, "one")
    atomic_write_text(p, "two")
    assert p.read_text() == "two"
    assert not (tmp_path / "a" / "b.json.tmp").exists()
