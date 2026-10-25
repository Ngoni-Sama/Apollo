import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import config  # noqa: E402


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Redirect every data path into tmp_path so tests never touch the real data/ tree."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(config, "RAW_TRANSCRIPTS_DIR", tmp_path / "transcripts" / "raw")
    monkeypatch.setattr(config, "MANIFEST_DIR", tmp_path / "manifest")
    monkeypatch.setattr(config, "DATASET_DIR", tmp_path / "dataset")
    monkeypatch.setattr(config, "AUDIO_DIR", tmp_path / "audio")
    return tmp_path


@pytest.fixture
def settings():
    return config.Settings(sleep_sec=0.0)


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}
