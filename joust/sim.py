"""Fake controllers for development without hardware.

``SimController`` mimics the public surface of ``psmove.Controller``.  It
produces reports at ~87 Hz like a real Move.  ``AutoPlayer`` drives one to
join the lobby and occasionally get jostled so a round plays itself.
"""
from __future__ import annotations

import collections
import logging
import random
import threading
import time
from typing import Optional

from .psmove.protocol import AccelCalibration, Button, InputReport, Model

log = logging.getLogger(__name__)

REPORT_HZ = 87.0


class SimController:
    def __init__(self, serial: str, model: Model = Model.ZCM2, seed: Optional[int] = None):
        self.serial = serial
        self.model = model
        self.connected = True
        self.calibration = AccelCalibration.nominal(model)
        self.latest: Optional[InputReport] = None
        self._queue: collections.deque[InputReport] = collections.deque(maxlen=1024)
        self.leds = (0, 0, 0)
        self.rumble = 0
        self.reports = 0
        self.rng = random.Random(seed)
        self._seq = 0
        self._ts = 0
        self._buttons = 0
        self._trigger = 0
        self._motion = 1.0  # |a| in g the controller "feels"
        self._noise = 0.02

    # -- the game only ever calls these ----------------------------------------------

    def set_leds(self, r: int, g: int, b: int) -> None:
        self.leds = (r, g, b)

    def set_rumble(self, value: int) -> None:
        self.rumble = value

    def drain(self) -> list[InputReport]:
        out = []
        while True:
            try:
                out.append(self._queue.popleft())
            except IndexError:
                return out

    # -- puppet strings ---------------------------------------------------------------

    def press(self, *buttons: Button) -> None:
        for b in buttons:
            self._buttons |= b

    def release(self, *buttons: Button) -> None:
        for b in buttons:
            self._buttons &= ~b

    def trigger(self, value: int) -> None:
        self._trigger = value
        if value > 0:
            self._buttons |= Button.T
        else:
            self._buttons &= ~Button.T

    def set_motion(self, g: float) -> None:
        """Magnitude of acceleration the sensor should report (1.0 = still)."""
        self._motion = g

    def emit_report(self) -> InputReport:
        """Produce one input report from the current puppet state."""
        lsb = 1.0 / self.calibration.factor[0]
        mag = max(0.0, self._motion + self.rng.gauss(0, self._noise))
        # point 'down' mostly along -y with a little tilt so axes are non-trivial
        ax, ay, az = 0.05 * mag, -0.99 * mag, 0.12 * mag
        raw = (int(ax * lsb), int(ay * lsb), int(az * lsb))
        self._seq = (self._seq + 1) & 0x0F
        self._ts = (self._ts + 1) & 0xFFFF
        rep = InputReport(
            buttons=self._buttons,
            trigger=self._trigger,
            sequence=self._seq,
            timestamp=self._ts,
            battery=0x05,
            accel=raw,
            gyro=(0, 0, 0),
            accel_frames=(raw, raw),
        )
        self.latest = rep
        self._queue.append(rep)
        self.reports += 1
        return rep


class SimBus:
    """Runs a set of SimControllers on a background thread at report rate."""

    def __init__(self, controllers: list[SimController]):
        self.controllers = controllers
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sim-bus", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        period = 1.0 / REPORT_HZ
        nxt = time.monotonic()
        while not self._stop.is_set():
            for c in self.controllers:
                c.emit_report()
            nxt += period
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.monotonic()


class AutoPlayer:
    """Scripted behaviour: join, hold still, get bumped now and then."""

    def __init__(self, controller: SimController, seed: Optional[int] = None):
        self.c = controller
        self.rng = random.Random(seed)
        self._next_action = time.monotonic() + self.rng.uniform(0.5, 2.0)
        self._bump_until = 0.0
        self._joined = False

    def update(self, now: float, phase: str) -> None:
        if phase == "lobby":
            self._bump_until = 0.0
            self.c.set_motion(1.0)
            if not self._joined and now >= self._next_action:
                self.c.trigger(255)
                self._joined = True
                self._next_action = now + 0.3
            elif self._joined and now >= self._next_action and self.c._trigger:
                self.c.trigger(0)
            return
        if phase == "countdown":
            self._joined = False
            self.c.trigger(0)
            self._next_action = now + self.rng.uniform(3.0, 12.0)
            return
        if phase == "playing":
            if now < self._bump_until:
                if getattr(self, "_release_at", 0) and now >= self._release_at:
                    self.c.release(Button.MOVE)
                    self._release_at = 0
                return
            if self._bump_until:
                self.c.set_motion(1.0)
                self._bump_until = 0.0
                self._next_action = now + self.rng.uniform(3.0, 12.0)
            elif now >= self._next_action:
                # a bump: either a nudge (warning) or a proper shove (death);
                # half the time the player tries a ninja dodge first
                if self.rng.random() < 0.5:
                    self.c.press(Button.MOVE)
                    self._release_at = now + 0.1
                self.c.set_motion(self.rng.choice([1.7, 1.9, 2.4, 3.0]))
                self._bump_until = now + self.rng.uniform(0.15, 0.4)
            if getattr(self, "_release_at", 0) and now >= self._release_at:
                self.c.release(Button.MOVE)
                self._release_at = 0
            return
        self.c.set_motion(1.0)
