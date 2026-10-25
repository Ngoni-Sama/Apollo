import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import transcribe
from config import Settings


def test_parse_whisper_cpp_json():
    data = {"result": {"language": "en"}, "transcription": [
        {"offsets": {"from": 0, "to": 2500}, "text": " Hello there."},
        {"offsets": {"from": 2500, "to": 3000}, "text": "  "},
        {"offsets": {"from": 3000, "to": 7250}, "text": " General Kenobi."},
    ]}
    segs, lang = transcribe._parse_whisper_cpp_json(data)
    assert lang == "en"
    assert segs == [{"start": 0.0, "end": 2.5, "text": "Hello there."},
                    {"start": 3.0, "end": 7.25, "text": "General Kenobi."}]


def test_faster_whisper_adapter(tmp_path, monkeypatch):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    model = MagicMock()
    model.transcribe.return_value = (
        iter([SimpleNamespace(start=0.0, end=1.234567, text=" hi "), SimpleNamespace(start=1.3, end=2, text=" ")]),
        SimpleNamespace(language="en", duration=2.0),
    )
    loader = MagicMock(return_value=model)
    monkeypatch.setattr(transcribe, "_load_faster_whisper", loader)
    res = transcribe.transcribe(audio, backend="faster-whisper", model="tiny.en", settings=Settings())
    loader.assert_called_once()
    assert res.segments == [{"start": 0.0, "end": 1.235, "text": "hi"}]
    assert model.transcribe.call_args.kwargs["vad_filter"] is True


def test_whisper_cpp_requires_config(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    with pytest.raises(transcribe.TranscriptionError, match="WHISPER_CPP_BIN"):
        transcribe.transcribe(audio, backend="whisper.cpp", settings=Settings())


def test_unknown_backend_and_missing_file(tmp_path):
    with pytest.raises(transcribe.TranscriptionError, match="no such file"):
        transcribe.transcribe(tmp_path / "nope.wav", settings=Settings())
    (tmp_path / "a.wav").write_bytes(b"x")
    with pytest.raises(transcribe.TranscriptionError, match="unknown backend"):
        transcribe.transcribe(tmp_path / "a.wav", backend="openai", settings=Settings())


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_to_wav_16k_local(tmp_path):
    src = tmp_path / "tone.mp3"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(src)], check=True)
    out = transcribe.to_wav_16k(src, tmp_path / "out.wav")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert probe == "16000,1"
    assert abs(transcribe.probe_duration(out) - 1.0) < 0.1
