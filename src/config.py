"""Paths and settings, loaded once from the environment / .env."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
RAW_TRANSCRIPTS_DIR = TRANSCRIPTS_DIR / "raw"
MANIFEST_DIR = DATA_DIR / "manifest"
DATASET_DIR = DATA_DIR / "dataset"
AUDIO_DIR = DATA_DIR / "audio"

DEFAULT_SLEEP_SEC = 2.0


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_path(name: str, default: Path | None = None) -> Path | None:
    value = _env(name)
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Settings:
    whisper_backend: str = "faster-whisper"
    whisper_model: str = "small.en"
    whisper_device: str = "auto"
    whisper_cpp_bin: Path | None = None
    whisper_cpp_model: Path | None = None
    model_cache_dir: Path = ROOT / "models"
    ytdlp_cookies_file: Path | None = None
    ytdlp_cookies_from_browser: str = ""
    ytdlp_js_runtimes: tuple[str, ...] = ("deno",)
    tiktok_ms_token: str = ""
    tiktok_headless: bool = True
    playwright_chromium_executable: Path | None = None
    sleep_sec: float = DEFAULT_SLEEP_SEC


def load_settings() -> Settings:
    """Build Settings from the current environment."""
    runtimes = tuple(r.strip() for r in _env("YTDLP_JS_RUNTIMES", "deno").split(",") if r.strip())
    return Settings(
        whisper_backend=_env("WHISPER_BACKEND", "faster-whisper"),
        whisper_model=_env("WHISPER_MODEL", "small.en"),
        whisper_device=_env("WHISPER_DEVICE", "auto"),
        whisper_cpp_bin=_env_path("WHISPER_CPP_BIN"),
        whisper_cpp_model=_env_path("WHISPER_CPP_MODEL"),
        model_cache_dir=_env_path("MODEL_CACHE_DIR", ROOT / "models") or ROOT / "models",
        ytdlp_cookies_file=_env_path("YTDLP_COOKIES_FILE"),
        ytdlp_cookies_from_browser=_env("YTDLP_COOKIES_FROM_BROWSER"),
        ytdlp_js_runtimes=runtimes,
        tiktok_ms_token=_env("TIKTOK_MS_TOKEN"),
        tiktok_headless=_env("TIKTOK_HEADLESS", "true").lower() != "false",
        playwright_chromium_executable=_env_path("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
        sleep_sec=float(_env("SLEEP_SEC", str(DEFAULT_SLEEP_SEC))),
    )


def setup_logging(verbose: bool = False) -> None:
    """Configure root logging for CLI entry points."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
