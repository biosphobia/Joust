"""Procedurally generated sounds so the game works with zero asset files.

All generators return float32 stereo arrays shaped (n, 2) in -1..1.
"""
from __future__ import annotations

import math

import numpy as np

RATE = 48000


def _stereo(mono: np.ndarray) -> np.ndarray:
    mono = np.asarray(mono, dtype=np.float32)
    return np.stack([mono, mono], axis=1)


def _env(n: int, attack: float, release: float, rate: int = RATE) -> np.ndarray:
    a = max(1, int(attack * rate))
    r = max(1, int(release * rate))
    env = np.ones(n, dtype=np.float32)
    env[:a] = np.linspace(0, 1, a, dtype=np.float32)
    tail = np.linspace(1, 0, r, dtype=np.float32)
    if r >= n:
        env *= tail[-n:] if n <= r else tail
    else:
        env[-r:] *= tail
    return env


def tone(freq: float, duration: float, volume: float = 0.5, rate: int = RATE, harmonics=(1.0, 0.4, 0.2)) -> np.ndarray:
    n = int(duration * rate)
    t = np.arange(n, dtype=np.float32) / rate
    sig = np.zeros(n, dtype=np.float32)
    for i, amp in enumerate(harmonics, start=1):
        sig += amp * np.sin(2 * math.pi * freq * i * t)
    sig /= sum(harmonics)
    return _stereo(sig * _env(n, 0.005, min(0.08, duration / 2), rate) * volume)


def pluck(freq: float, duration: float, volume: float = 0.5, rate: int = RATE) -> np.ndarray:
    """Harpsichord-ish pluck: bright harmonics with a fast exponential decay."""
    n = int(duration * rate)
    t = np.arange(n, dtype=np.float32) / rate
    sig = np.zeros(n, dtype=np.float32)
    for k, amp in enumerate((1.0, 0.6, 0.45, 0.3, 0.2, 0.12), start=1):
        sig += amp * np.sin(2 * math.pi * freq * k * t) * np.exp(-t * (2.5 + 1.5 * k))
    sig *= np.exp(-t * 3.0)
    sig /= 2.67
    sig[: min(n, 40)] *= np.linspace(0, 1, min(n, 40), dtype=np.float32)
    return _stereo(sig * volume)


def concat(*parts: np.ndarray) -> np.ndarray:
    return np.concatenate(parts, axis=0)


def silence(duration: float, rate: int = RATE) -> np.ndarray:
    return np.zeros((int(duration * rate), 2), dtype=np.float32)


def mix(*parts: np.ndarray) -> np.ndarray:
    n = max(p.shape[0] for p in parts)
    out = np.zeros((n, 2), dtype=np.float32)
    for p in parts:
        out[: p.shape[0]] += p
    return out


# ---------------------------------------------------------------------------
# Game sound effects
# ---------------------------------------------------------------------------


def sfx_join(rate: int = RATE) -> np.ndarray:
    return concat(tone(660, 0.07, 0.35, rate), tone(990, 0.10, 0.35, rate))


def sfx_leave(rate: int = RATE) -> np.ndarray:
    return concat(tone(990, 0.07, 0.3, rate), tone(660, 0.10, 0.3, rate))


def sfx_countdown_beep(rate: int = RATE) -> np.ndarray:
    return tone(880, 0.18, 0.5, rate)


def sfx_start(rate: int = RATE) -> np.ndarray:
    return concat(tone(523.25, 0.10, 0.5, rate), tone(659.25, 0.10, 0.5, rate), tone(783.99, 0.10, 0.5, rate), tone(1046.5, 0.45, 0.6, rate))


def sfx_menu_tick(rate: int = RATE) -> np.ndarray:
    return tone(1320, 0.05, 0.3, rate)


def sfx_explosion(rate: int = RATE) -> np.ndarray:
    """Noise burst with a low thump: a player just got knocked out."""
    dur = 1.1
    n = int(dur * rate)
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(n).astype(np.float32)
    # crude low-pass by moving average to make it 'boomy'
    kernel = np.ones(24, dtype=np.float32) / 24
    noise = np.convolve(noise, kernel, mode="same")
    t = np.arange(n, dtype=np.float32) / rate
    noise *= np.exp(-t * 4.0)
    thump = np.sin(2 * math.pi * (70 - 40 * t) * t) * np.exp(-t * 6.0)
    sig = 0.8 * noise + 0.9 * thump
    sig = np.tanh(sig * 1.5)
    return _stereo(sig * 0.9)


def sfx_game_over(rate: int = RATE) -> np.ndarray:
    return concat(tone(392.0, 0.25, 0.5, rate), tone(349.23, 0.25, 0.5, rate), tone(311.13, 0.25, 0.5, rate), tone(261.63, 0.7, 0.55, rate))


def sfx_victory(rate: int = RATE) -> np.ndarray:
    notes = [523.25, 659.25, 783.99, 1046.5, 783.99, 1046.5]
    parts = [tone(f, 0.12 if i < 5 else 0.6, 0.5, rate) for i, f in enumerate(notes)]
    return concat(*parts)


def sfx_whoosh(rate: int = RATE) -> np.ndarray:
    """Ninja dodge: a fast noise sweep, low to high to low."""
    dur = 0.45
    n = int(dur * rate)
    rng = np.random.default_rng(11)
    noise = rng.standard_normal(n).astype(np.float32)
    t = np.arange(n, dtype=np.float32) / rate
    # one-pole low-pass whose cutoff sweeps up then down
    cutoff = 300 + 5000 * np.sin(math.pi * t / dur) ** 2
    alpha = (2 * math.pi * cutoff / rate).astype(np.float32)
    alpha = np.clip(alpha / (1 + alpha), 0.0, 0.99)
    out = np.empty(n, dtype=np.float32)
    y = 0.0
    for i in range(n):
        y += alpha[i] * (noise[i] - y)
        out[i] = y
    env = np.sin(math.pi * t / dur) ** 1.5
    sig = out * env
    peak = float(np.max(np.abs(sig))) or 1.0
    return _stereo(sig / peak * 0.8)


def sfx_denied(rate: int = RATE) -> np.ndarray:
    """Soft low double-blip: that dodge is already spent."""
    return concat(tone(220, 0.06, 0.25, rate), silence(0.03, rate), tone(196, 0.08, 0.25, rate))


def sfx_beeps(count: int, rate: int = RATE) -> np.ndarray:
    """N short beeps: used to announce the sensitivity level."""
    parts = []
    for _ in range(count):
        parts.append(tone(1000, 0.08, 0.4, rate))
        parts.append(silence(0.08, rate))
    return concat(*parts)


def sfx_team_mode(on: bool, rate: int = RATE) -> np.ndarray:
    if on:
        return concat(tone(440, 0.12, 0.4, rate), tone(440, 0.12, 0.4, rate))
    return concat(tone(440, 0.12, 0.4, rate), tone(330, 0.2, 0.4, rate))


# ---------------------------------------------------------------------------
# Fallback music: a baroque-flavoured loop
# ---------------------------------------------------------------------------

_NOTE_INDEX = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_freq(name: str) -> float:
    """'A4' -> 440.0, supports '#' and 'b'."""
    letter = name[0].upper()
    rest = name[1:]
    acc = 0
    while rest and rest[0] in "#b":
        acc += 1 if rest[0] == "#" else -1
        rest = rest[1:]
    octave = int(rest)
    midi = 12 * (octave + 1) + _NOTE_INDEX[letter] + acc
    return 440.0 * 2 ** ((midi - 69) / 12)


# Pachelbel-style progression in D: D A Bm F#m G D G A, spelled as chord tones
_PROGRESSION = [
    ("D3", ["D4", "F#4", "A4", "D5"]),
    ("A2", ["A3", "C#4", "E4", "A4"]),
    ("B2", ["B3", "D4", "F#4", "B4"]),
    ("F#2", ["F#3", "A3", "C#4", "F#4"]),
    ("G2", ["G3", "B3", "D4", "G4"]),
    ("D3", ["D4", "F#4", "A4", "D5"]),
    ("G2", ["G3", "B3", "D4", "G4"]),
    ("A2", ["A3", "C#4", "E4", "G4"]),
]


def generate_baroque_loop(bpm: float = 104.0, passes: int = 2, rate: int = RATE) -> np.ndarray:
    """Arpeggiated harpsichord loop; ~37 s at the default tempo."""
    beat = 60.0 / bpm
    sixteenth = beat / 4
    bars = []
    for p in range(passes):
        for bass, chord in _PROGRESSION:
            bar_len = int(4 * beat * rate)
            bar = np.zeros((bar_len, 2), dtype=np.float32)
            # bass on beats 1 and 3, fifth on 2 and 4
            for b in range(4):
                f = note_freq(bass) * (1.0 if b % 2 == 0 else 1.5)
                s = pluck(f, beat * 0.95, 0.35, rate)
                off = int(b * beat * rate)
                end = min(bar_len, off + s.shape[0])
                bar[off:end] += s[: end - off]
            # arpeggio in sixteenths, up-down pattern; second pass an octave up
            pattern = [0, 1, 2, 3, 2, 1, 0, 1, 2, 3, 2, 3, 2, 1, 0, 1]
            for i, idx in enumerate(pattern):
                f = note_freq(chord[idx]) * (2.0 if p % 2 else 1.0)
                s = pluck(f, sixteenth * 1.8, 0.22, rate)
                off = int(i * sixteenth * rate)
                end = min(bar_len, off + s.shape[0])
                bar[off:end] += s[: end - off]
            bars.append(bar)
    track = np.concatenate(bars, axis=0)
    peak = float(np.max(np.abs(track))) or 1.0
    return (track / peak * 0.7).astype(np.float32)
