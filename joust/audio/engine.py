"""Real-time mixer: one variable-speed music track plus one-shot effects.

The music is resampled on the fly, so changing ``speed`` changes tempo *and*
pitch, like slowing a record down.  That is exactly the "slow-motion Bach"
effect the original game uses.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

from . import synth

log = logging.getLogger(__name__)


class AudioEngine:
    """Interface.  ``NullAudio`` implements it for tests / --no-audio."""

    rate: int = synth.RATE

    def set_music(self, samples: np.ndarray, rate: int) -> None: ...
    def play_music(self) -> None: ...
    def stop_music(self) -> None: ...
    def set_speed(self, ratio: float) -> None: ...
    def play_sfx(self, samples: np.ndarray) -> None: ...
    def close(self) -> None: ...

    @property
    def music_playing(self) -> bool:
        return False


class NullAudio(AudioEngine):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.speed = 1.0
        self._playing = False

    def set_music(self, samples: np.ndarray, rate: int) -> None:
        self.events.append("set_music")

    def play_music(self) -> None:
        self._playing = True
        self.events.append("play_music")

    def stop_music(self) -> None:
        self._playing = False
        self.events.append("stop_music")

    def set_speed(self, ratio: float) -> None:
        self.speed = ratio

    def play_sfx(self, samples: np.ndarray) -> None:
        self.events.append("sfx")

    def close(self) -> None:
        pass

    @property
    def music_playing(self) -> bool:
        return self._playing


class Mixer:
    """Pure-numpy mixing core, independent of the output device."""

    def __init__(self, rate: int):
        self.rate = rate
        self._lock = threading.Lock()
        self._music: Optional[np.ndarray] = None
        self._music_rate = rate
        self._pos = 0.0
        self._playing = False
        self._speed = 1.0
        self._target_speed = 1.0
        self._sfx: list[tuple[np.ndarray, int]] = []
        self.music_volume = 0.8
        self.sfx_volume = 1.0

    def set_music(self, samples: np.ndarray, rate: int) -> None:
        with self._lock:
            self._music = np.asarray(samples, dtype=np.float32)
            self._music_rate = rate
            self._pos = 0.0

    def play_music(self) -> None:
        with self._lock:
            self._playing = self._music is not None

    def stop_music(self) -> None:
        with self._lock:
            self._playing = False
            self._pos = 0.0

    @property
    def music_playing(self) -> bool:
        return self._playing

    def set_speed(self, ratio: float) -> None:
        self._target_speed = max(0.1, min(4.0, float(ratio)))

    def play_sfx(self, samples: np.ndarray) -> None:
        with self._lock:
            self._sfx.append((np.asarray(samples, dtype=np.float32), 0))

    def render(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, 2), dtype=np.float32)
        with self._lock:
            # glide toward the target speed to avoid zipper noise
            self._speed += (self._target_speed - self._speed) * 0.25
            if self._playing and self._music is not None and len(self._music) > 1:
                music = self._music
                n = len(music)
                step = self._speed * self._music_rate / self.rate
                idx = self._pos + step * np.arange(frames, dtype=np.float64)
                idx_mod = np.mod(idx, n)
                i0 = idx_mod.astype(np.int64)
                frac = (idx_mod - i0).astype(np.float32)[:, None]
                i1 = (i0 + 1) % n
                out += (music[i0] * (1 - frac) + music[i1] * frac) * self.music_volume
                self._pos = float(np.mod(idx[-1] + step, n))
            keep = []
            for samples, pos in self._sfx:
                chunk = samples[pos : pos + frames]
                out[: len(chunk)] += chunk * self.sfx_volume
                pos += frames
                if pos < len(samples):
                    keep.append((samples, pos))
            self._sfx = keep
        np.clip(out, -1.0, 1.0, out=out)
        return out


class SoundDeviceAudio(AudioEngine):
    """Plays through the default output device using the sounddevice package."""

    def __init__(self, device=None, rate: Optional[int] = None, blocksize: int = 1024):
        import sounddevice as sd  # imported lazily so tests do not need it

        if rate is None:
            try:
                rate = int(sd.query_devices(device, "output")["default_samplerate"])
            except Exception:  # noqa: BLE001
                rate = synth.RATE
        self.rate = rate
        self.mixer = Mixer(rate)
        self._stream = sd.OutputStream(
            samplerate=rate,
            channels=2,
            dtype="float32",
            blocksize=blocksize,
            device=device,
            callback=self._callback,
        )
        self._stream.start()
        log.info("audio: %d Hz, blocksize %d", rate, blocksize)

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        if status:
            log.debug("audio status: %s", status)
        outdata[:] = self.mixer.render(frames)

    def set_music(self, samples: np.ndarray, rate: int) -> None:
        self.mixer.set_music(samples, rate)

    def play_music(self) -> None:
        self.mixer.play_music()

    def stop_music(self) -> None:
        self.mixer.stop_music()

    def set_speed(self, ratio: float) -> None:
        self.mixer.set_speed(ratio)

    def play_sfx(self, samples: np.ndarray) -> None:
        self.mixer.play_sfx(samples)

    @property
    def music_playing(self) -> bool:
        return self.mixer.music_playing

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass


def create_audio(enabled: bool = True, device=None) -> AudioEngine:
    if not enabled:
        return NullAudio()
    try:
        return SoundDeviceAudio(device=device)
    except Exception as exc:  # noqa: BLE001
        log.warning("audio output unavailable (%s); running silent", exc)
        return NullAudio()
