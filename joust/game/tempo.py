"""Music speed state machine.

The music alternates between a slow phase (controllers are twitchy, everyone
creeps) and a fast phase (thresholds loosen, people lunge).  Phase lengths
are random and get shorter/punchier as players are eliminated.  Changes are
glided over ``transition`` seconds so nobody dies to a discontinuity.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .motion import clamp01, lerp

SLOW_SPEED = 1.0
FAST_SPEED = 1.3
TRANSITION_SECONDS = 1.5

# (min, max) phase lengths at the start of a round and near the end.
START_FAST = (4.0, 8.0)
START_SLOW = (10.0, 23.0)
END_FAST = (6.0, 10.0)
END_SLOW = (8.0, 12.0)


@dataclass
class TempoController:
    slow_speed: float = SLOW_SPEED
    fast_speed: float = FAST_SPEED
    transition: float = TRANSITION_SECONDS
    rng: random.Random = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.rng is None:
            self.rng = random.Random()
        self.reset(0.0)

    def reset(self, now: float) -> None:
        self.fast = False  # current *target* phase
        self.speed = self.slow_speed
        self._phase_started = now
        self._next_change = now + self._phase_length(fast=False, progress=0.0)

    def _phase_length(self, fast: bool, progress: float) -> float:
        p = clamp01(progress)
        if fast:
            lo, hi = lerp(START_FAST[0], END_FAST[0], p), lerp(START_FAST[1], END_FAST[1], p)
        else:
            lo, hi = lerp(START_SLOW[0], END_SLOW[0], p), lerp(START_SLOW[1], END_SLOW[1], p)
        return self.rng.uniform(lo, hi)

    def update(self, now: float, progress: float) -> float:
        """Advance; ``progress`` 0..1 is how far along the round is."""
        if now >= self._next_change:
            self.fast = not self.fast
            self._phase_started = now
            self._next_change = now + self.transition + self._phase_length(self.fast, progress)
        t = clamp01((now - self._phase_started) / self.transition) if self.transition > 0 else 1.0
        target = self.fast_speed if self.fast else self.slow_speed
        origin = self.slow_speed if self.fast else self.fast_speed
        self.speed = lerp(origin, target, t)
        return self.speed

    @property
    def speed_percent(self) -> float:
        span = self.fast_speed - self.slow_speed
        if span <= 0:
            return 0.0
        return clamp01((self.speed - self.slow_speed) / span)
