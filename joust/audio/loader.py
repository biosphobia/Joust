"""Load music from disk (wav / flac / ogg / mp3 via libsndfile)."""
from __future__ import annotations

import logging
import random
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MUSIC_EXTENSIONS = {".wav", ".flac", ".ogg", ".oga", ".mp3", ".aiff", ".aif"}


def find_music_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() in MUSIC_EXTENSIONS)
    return []


def load_audio_file(path: Path) -> tuple[np.ndarray, int]:
    """Return (stereo float32 samples, sample rate)."""
    try:
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - environment specific
        raise RuntimeError("the 'soundfile' package is required to load music files") from exc
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]
    return np.ascontiguousarray(data, dtype=np.float32), int(rate)


def pick_music(path: Path | None) -> Path | None:
    if path is None:
        return None
    files = find_music_files(path)
    if not files:
        log.warning("no music files found at %s", path)
        return None
    choice = random.choice(files)
    log.info("music: %s", choice)
    return choice
