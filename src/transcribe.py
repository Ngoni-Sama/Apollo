"""Local ASR adapter: faster-whisper (Python) or whisper.cpp (CLI). Plus ffmpeg helpers."""

from __future__ import annotations

import argparse
import functools
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from config import Settings, load_settings, setup_logging
from schema import Segment

log = logging.getLogger(__name__)

BACKENDS = ("faster-whisper", "whisper.cpp")


class TranscriptionError(Exception):
    pass


@dataclass
class TranscriptionResult:
    segments: list[Segment]
    language: str
    duration_sec: float
    backend: str


# --- ffmpeg -----------------------------------------------------------------

def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise TranscriptionError(f"{binary} not found on PATH (install ffmpeg)")
    return path


def to_wav_16k(src: Path, dst: Path) -> Path:
    """Extract/convert any audio or video file to 16 kHz mono PCM WAV (what Whisper expects)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [_require("ffmpeg"), "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TranscriptionError(f"ffmpeg failed on {src}: {proc.stderr.strip()}")
    return dst


def extract_audio(video: Path, dst: Path) -> Path:
    """Video file -> mp3 audio track."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [_require("ffmpeg"), "-nostdin", "-y", "-loglevel", "error", "-i", str(video),
           "-vn", "-ac", "1", "-c:a", "libmp3lame", "-q:a", "4", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TranscriptionError(f"ffmpeg failed on {video}: {proc.stderr.strip()}")
    return dst


def probe_duration(path: Path) -> float:
    cmd = [_require("ffprobe"), "-v", "error", "-show_entries", "format=duration",
           "-of", "default=nw=1:nk=1", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


# --- faster-whisper ---------------------------------------------------------

@functools.lru_cache(maxsize=2)
def _load_faster_whisper(model: str, device: str, cache_dir: str):
    from faster_whisper import WhisperModel

    if device == "auto":
        import ctranslate2
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    log.info("loading faster-whisper %s on %s (%s), cache=%s", model, device, compute_type, cache_dir)
    return WhisperModel(model, device=device, compute_type=compute_type, download_root=cache_dir)


def _transcribe_faster_whisper(audio: Path, settings: Settings, model: str, language: str | None) -> TranscriptionResult:
    whisper = _load_faster_whisper(model, settings.whisper_device, str(settings.model_cache_dir))
    seg_iter, info = whisper.transcribe(str(audio), language=language, vad_filter=True, beam_size=5)
    segments = [
        Segment(start=round(s.start, 3), end=round(s.end, 3), text=s.text.strip())
        for s in seg_iter if s.text.strip()
    ]
    return TranscriptionResult(segments, info.language, round(info.duration, 2), "faster-whisper")


# --- whisper.cpp ------------------------------------------------------------

def _parse_whisper_cpp_json(data: dict) -> tuple[list[Segment], str]:
    segments = [
        Segment(start=item["offsets"]["from"] / 1000, end=item["offsets"]["to"] / 1000, text=item["text"].strip())
        for item in data.get("transcription", []) if item.get("text", "").strip()
    ]
    return segments, data.get("result", {}).get("language", "")


def _transcribe_whisper_cpp(audio: Path, settings: Settings, language: str | None) -> TranscriptionResult:
    binary, model = settings.whisper_cpp_bin, settings.whisper_cpp_model
    if not binary or not Path(binary).exists():
        raise TranscriptionError("WHISPER_CPP_BIN is not set or does not exist")
    if not model or not Path(model).exists():
        raise TranscriptionError("WHISPER_CPP_MODEL is not set or does not exist (use models/download-ggml-model.sh)")

    with tempfile.TemporaryDirectory() as tmp:
        wav = to_wav_16k(audio, Path(tmp) / "in.wav")
        out_base = Path(tmp) / "out"
        cmd = [str(binary), "-m", str(model), "-f", str(wav), "-l", language or "auto",
               "-oj", "-of", str(out_base), "-np"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise TranscriptionError(f"whisper.cpp failed ({proc.returncode}): {proc.stderr.strip()[-500:]}")
        data = json.loads(out_base.with_suffix(".json").read_text(encoding="utf-8"))
        duration = probe_duration(wav)
    segments, lang = _parse_whisper_cpp_json(data)
    return TranscriptionResult(segments, lang or language or "", round(duration, 2), "whisper.cpp")


# --- public API -------------------------------------------------------------

def transcribe(
    audio: Path,
    backend: str | None = None,
    model: str | None = None,
    language: str | None = "en",
    settings: Settings | None = None,
) -> TranscriptionResult:
    """Transcribe a local audio/video file with the configured backend."""
    settings = settings or load_settings()
    backend = backend or settings.whisper_backend
    if not audio.exists():
        raise TranscriptionError(f"no such file: {audio}")
    log.info("transcribing %s with %s", audio.name, backend)
    if backend == "faster-whisper":
        result = _transcribe_faster_whisper(audio, settings, model or settings.whisper_model, language)
    elif backend == "whisper.cpp":
        result = _transcribe_whisper_cpp(audio, settings, language)
    else:
        raise TranscriptionError(f"unknown backend {backend!r}; choose from {BACKENDS}")
    log.info("transcribed %s: %d segments, %.1fs", audio.name, len(result.segments), result.duration_sec)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Transcribe a local audio/video file; writes JSON segments to stdout.")
    ap.add_argument("--file", required=True, type=Path)
    ap.add_argument("--backend", choices=BACKENDS)
    ap.add_argument("--model", help="faster-whisper model name (ignored for whisper.cpp)")
    ap.add_argument("--language", default="en", help="language code, or 'auto'")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    setup_logging(args.verbose)
    language = None if args.language == "auto" else args.language
    result = transcribe(args.file, backend=args.backend, model=args.model, language=language)
    json.dump({"backend": result.backend, "language": result.language, "duration_sec": result.duration_sec,
               "segments": result.segments}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
