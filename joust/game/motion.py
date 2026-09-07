"""Turning accelerometer samples into "did you move too much".

The metric is the same one JoustMania settled on after years of play: the
magnitude of the acceleration vector in g (1.0 at rest), smoothed with a
short exponential moving average, compared against a threshold that depends
on the sensitivity setting and how fast the music currently is.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

SENSITIVITY_NAMES = ["ultra slow", "slow", "medium", "fast", "ultra fast"]
DEFAULT_SENSITIVITY = 2

# Thresholds in g of |a|.  Index = sensitivity level.
SLOW_WARNING = [1.2, 1.3, 1.6, 2.0, 2.5]
SLOW_MAX = [1.3, 1.5, 1.8, 2.5, 3.2]
FAST_WARNING = [1.4, 1.6, 1.9, 2.7, 2.8]
FAST_MAX = [1.6, 1.8, 2.8, 3.2, 3.5]

EMA_KEEP = 0.8  # change = change*0.8 + sample*0.2


def lerp(a: float, b: float, p: float) -> float:
    return a * (1 - p) + b * p


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


@dataclass(frozen=True)
class Thresholds:
    warning: float
    death: float


def thresholds_for(sensitivity: int, speed_percent: float) -> Thresholds:
    """Warning / death thresholds for a sensitivity level and music speed.

    ``speed_percent`` is 0 for the slow phase and 1 for the fast phase.
    """
    s = max(0, min(len(SLOW_MAX) - 1, int(sensitivity)))
    p = clamp01(speed_percent)
    return Thresholds(
        warning=lerp(SLOW_WARNING[s], FAST_WARNING[s], p),
        death=lerp(SLOW_MAX[s], FAST_MAX[s], p),
    )


@dataclass
class RestReference:
    """Estimates what |a| looks like when the controller is held still.

    Factory calibration gets |a| close to 1.0 at rest but not exactly, and
    with the nominal fallback scale it can be off by 10%.  While a controller
    sits in the lobby we track the quietest magnitude window and use it to
    normalise, so 1.0 really means "at rest" for every controller.
    """

    window: int = 60
    samples: list[float] = field(default_factory=list)
    best_mean: float = 1.0
    best_spread: float = math.inf
    locked: bool = False

    def feed(self, magnitude: float) -> None:
        if self.locked:
            return
        self.samples.append(magnitude)
        if len(self.samples) < self.window:
            return
        win = self.samples[-self.window :]
        mean = sum(win) / len(win)
        spread = max(win) - min(win)
        if spread < self.best_spread and 0.5 < mean < 2.0:
            self.best_spread = spread
            self.best_mean = mean
        if len(self.samples) > self.window * 4:
            del self.samples[: -self.window]

    @property
    def scale(self) -> float:
        return 1.0 / self.best_mean if self.best_mean > 0 else 1.0

    def reset(self) -> None:
        self.samples.clear()
        self.best_spread = math.inf
        self.best_mean = 1.0
        self.locked = False


class MotionTracker:
    """Per-player smoothed movement metric."""

    def __init__(self) -> None:
        self.change = 1.0
        self.peak = 1.0

    def reset(self) -> None:
        self.change = 1.0
        self.peak = 1.0

    def update(self, magnitude: float) -> float:
        self.change = self.change * EMA_KEEP + magnitude * (1 - EMA_KEEP)
        self.peak = max(self.peak, self.change)
        return self.change


def magnitude(vec: tuple[float, float, float]) -> float:
    x, y, z = vec
    return math.sqrt(x * x + y * y + z * z)
