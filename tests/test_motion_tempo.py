import random

import pytest

from joust.game.motion import FAST_MAX, SLOW_MAX, SLOW_WARNING, MotionTracker, RestReference, thresholds_for
from joust.game.tempo import FAST_SPEED, SLOW_SPEED, TRANSITION_SECONDS, TempoController


def test_thresholds_interpolate_with_music_speed():
    slow = thresholds_for(2, 0.0)
    fast = thresholds_for(2, 1.0)
    mid = thresholds_for(2, 0.5)
    assert slow.death == SLOW_MAX[2] and slow.warning == SLOW_WARNING[2]
    assert fast.death == FAST_MAX[2]
    assert slow.death < mid.death < fast.death
    assert thresholds_for(99, 2.0).death == FAST_MAX[-1]
    assert thresholds_for(-5, -1.0).death == SLOW_MAX[0]


def test_sensitivity_levels_are_ordered():
    for level in range(4):
        assert thresholds_for(level, 0).death < thresholds_for(level + 1, 0).death
        assert thresholds_for(level, 0).warning < thresholds_for(level, 0).death


def test_tracker_smooths():
    t = MotionTracker()
    assert t.update(1.0) == pytest.approx(1.0)
    first = t.update(3.0)
    assert 1.0 < first < 3.0
    assert first == pytest.approx(1.4)
    for _ in range(50):
        v = t.update(3.0)
    assert v == pytest.approx(3.0, abs=0.01)
    assert t.peak == pytest.approx(3.0, abs=0.01)


def test_rest_reference_picks_quiet_window():
    r = RestReference(window=10)
    rng = random.Random(1)
    for _ in range(30):
        r.feed(1.5 + rng.uniform(-0.4, 0.4))  # noisy shake
    for _ in range(30):
        r.feed(1.08 + rng.uniform(-0.005, 0.005))  # held still
    assert r.best_mean == pytest.approx(1.08, abs=0.01)
    assert r.scale == pytest.approx(1 / 1.08, abs=0.01)
    r.locked = True
    r.feed(5.0)
    assert r.best_mean == pytest.approx(1.08, abs=0.01)


def test_tempo_alternates_and_glides():
    t = TempoController(rng=random.Random(3))
    t.reset(0.0)
    assert t.speed == SLOW_SPEED and t.fast is False
    speeds = []
    fast_seen = False
    now = 0.0
    while now < 120.0:
        s = t.update(now, 0.0)
        speeds.append(s)
        if t.fast:
            fast_seen = True
        now += 0.01
    assert fast_seen
    assert max(speeds) == pytest.approx(FAST_SPEED)
    assert min(speeds) == pytest.approx(SLOW_SPEED)
    # no jump larger than what the glide allows per 10 ms
    max_step = (FAST_SPEED - SLOW_SPEED) / TRANSITION_SECONDS * 0.01 * 1.5
    assert max(abs(a - b) for a, b in zip(speeds, speeds[1:])) <= max_step
    assert 0.0 <= t.speed_percent <= 1.0
