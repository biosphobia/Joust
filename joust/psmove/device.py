"""High-level PS Move controller: a reader thread plus LED/rumble output.

The controller only keeps its sphere lit while it receives LED reports every
few seconds, so an output thread re-sends the last colour periodically.  The
firmware also gets unhappy if it is flooded, so updates are rate limited to
one per ~120 ms unless nothing changed.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Callable, Optional

from . import protocol as P
from .backends import DeviceInfo, Transport, open_transport

log = logging.getLogger(__name__)

LED_MIN_INTERVAL = 0.12  # seconds between updates with changed values
LED_REFRESH_INTERVAL = 3.0  # re-send unchanged values this often (sphere times out ~5s)
READ_TIMEOUT_MS = 50
INPUT_READ_SIZE = 64


class Controller:
    """One connected PS Move.

    Public state (``latest``, ``connected``) is updated by a background thread;
    ``set_leds`` / ``set_rumble`` are cheap and thread-safe.
    """

    def __init__(self, info: DeviceInfo, transport: Optional[Transport] = None):
        self.info = info
        self.model = info.model
        self.serial = info.label
        self._transport = transport or open_transport(info)
        self.calibration = self._load_calibration()

        self.latest: Optional[P.InputReport] = None
        self._queue: collections.deque[P.InputReport] = collections.deque(maxlen=1024)
        self.last_report_time = 0.0
        self.connected = True
        self.reports = 0

        self._lock = threading.Lock()
        self._leds = (0, 0, 0)
        self._rumble = 0
        self._dirty = True
        self._last_sent: tuple[tuple[int, int, int], int] | None = None
        self._last_send_time = 0.0
        self._stop = threading.Event()
        self._listeners: list[Callable[[P.InputReport], None]] = []

        self._reader = threading.Thread(target=self._read_loop, name=f"psmove-read-{self.serial}", daemon=True)
        self._writer = threading.Thread(target=self._write_loop, name=f"psmove-write-{self.serial}", daemon=True)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._reader.start()
        self._writer.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._send(0, 0, 0, 0)
        except OSError:
            pass
        self._transport.close()
        self.connected = False

    # -- calibration -------------------------------------------------------

    def _load_calibration(self) -> P.AccelCalibration:
        try:
            chunks = []
            count = 3 if self.model is P.Model.ZCM1 else 2
            for _ in range(count):
                chunks.append(self._transport.get_feature_report(P.REPORT_GET_CALIBRATION, P.CALIBRATION_CHUNK_SIZE))
            blob = P.assemble_calibration_blob(chunks, self.model)
            cal = P.parse_accel_calibration(blob, self.model)
            log.info("%s: loaded factory accelerometer calibration", self.serial)
            return cal
        except Exception as exc:  # noqa: BLE001 - any failure -> nominal
            log.info("%s: no calibration (%s); using nominal scale", self.serial, exc)
            return P.AccelCalibration.nominal(self.model)

    # -- input -------------------------------------------------------------

    def add_listener(self, fn: Callable[[P.InputReport], None]) -> None:
        self._listeners.append(fn)

    def drain(self) -> list[P.InputReport]:
        """Return every report received since the last call, oldest first."""
        out = []
        while True:
            try:
                out.append(self._queue.popleft())
            except IndexError:
                return out

    def _read_loop(self) -> None:
        silent_since = time.monotonic()
        while not self._stop.is_set():
            try:
                data = self._transport.read(INPUT_READ_SIZE, READ_TIMEOUT_MS)
            except OSError as exc:
                log.warning("%s: read failed (%s); marking disconnected", self.serial, exc)
                self.connected = False
                return
            if not data:
                if time.monotonic() - silent_since > 5.0 and self.reports:
                    if self.connected:
                        log.warning("%s: no input for 5s; marking disconnected", self.serial)
                    self.connected = False
                continue
            silent_since = time.monotonic()
            if data[0] != P.REPORT_INPUT:
                continue
            try:
                report = P.parse_input_report(data, self.model)
            except ValueError:
                continue
            self.latest = report
            self._queue.append(report)
            self.last_report_time = time.monotonic()
            self.reports += 1
            self.connected = True
            for fn in self._listeners:
                try:
                    fn(report)
                except Exception:  # noqa: BLE001
                    log.exception("listener failed")

    # -- output ------------------------------------------------------------

    def set_leds(self, r: int, g: int, b: int) -> None:
        rgb = (int(max(0, min(255, r))), int(max(0, min(255, g))), int(max(0, min(255, b))))
        with self._lock:
            if rgb != self._leds:
                self._leds = rgb
                self._dirty = True

    def set_rumble(self, value: int) -> None:
        v = int(max(0, min(255, value)))
        with self._lock:
            if v != self._rumble:
                self._rumble = v
                self._dirty = True

    def _send(self, r: int, g: int, b: int, rumble: int) -> None:
        self._transport.write(P.build_led_report(r, g, b, rumble))

    def _write_loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            with self._lock:
                leds, rumble, dirty = self._leds, self._rumble, self._dirty
            since = now - self._last_send_time
            should = (dirty and since >= LED_MIN_INTERVAL) or since >= LED_REFRESH_INTERVAL
            if should:
                try:
                    self._send(*leds, rumble)
                except OSError as exc:
                    log.warning("%s: write failed (%s)", self.serial, exc)
                    self.connected = False
                    time.sleep(0.5)
                    continue
                self._last_send_time = now
                with self._lock:
                    if (self._leds, self._rumble) == (leds, rumble):
                        self._dirty = False
            time.sleep(0.02)

    # -- convenience ---------------------------------------------------------

    @property
    def battery(self) -> Optional[P.Battery]:
        return self.latest.battery_state if self.latest else None

    def accel_g(self) -> Optional[tuple[float, float, float]]:
        if self.latest is None:
            return None
        return self.calibration.apply(self.latest.accel)

    def __repr__(self) -> str:
        return f"<Controller {self.serial} {self.model.value} {self.info.transport}>"
