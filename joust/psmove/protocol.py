"""Wire-level description of the PlayStation Move motion controller.

Everything in here is pure data handling: parsing input reports, building
output reports and decoding the calibration blob.  No I/O happens here, so it
is fully unit-testable without hardware.

Sources: the moveonpc wiki (nitsch/moveonpc) and thp/psmoveapi.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import Enum, IntEnum

# ---------------------------------------------------------------------------
# Identification
# ---------------------------------------------------------------------------

VENDOR_ID = 0x054C
PRODUCT_ID_ZCM1 = 0x03D5  # CECH-ZCM1 (PS3 era)
PRODUCT_ID_ZCM2 = 0x0C5E  # CECH-ZCM2 (PS4 era)
PRODUCT_IDS = (PRODUCT_ID_ZCM1, PRODUCT_ID_ZCM2)


class Model(Enum):
    ZCM1 = "ZCM1"
    ZCM2 = "ZCM2"

    @classmethod
    def from_pid(cls, pid: int) -> "Model":
        if pid == PRODUCT_ID_ZCM2:
            return cls.ZCM2
        if pid == PRODUCT_ID_ZCM1:
            return cls.ZCM1
        raise ValueError(f"not a PS Move product id: {pid:#06x}")


# ---------------------------------------------------------------------------
# Report ids
# ---------------------------------------------------------------------------

REPORT_INPUT = 0x01
REPORT_GET_BTADDR = 0x04  # feature: controller + host bluetooth address
REPORT_SET_BTADDR = 0x05  # feature: set host bluetooth address
REPORT_SET_LEDS = 0x06  # output: sphere colour + rumble
REPORT_GET_CALIBRATION = 0x10  # feature: calibration blob

INPUT_REPORT_MIN_SIZE = 0x25  # need everything up to and including gyro
LED_REPORT_SIZE = 9
BTADDR_GET_SIZE = 16
BTADDR_SET_SIZE = 23
CALIBRATION_CHUNK_SIZE = 49
CALIBRATION_BLOB_SIZE = {
    Model.ZCM1: CALIBRATION_CHUNK_SIZE * 3 - 2 * 2,
    Model.ZCM2: CALIBRATION_CHUNK_SIZE * 2 - 2 * 1,
}


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


class Button(IntEnum):
    """Bit flags, combined into one integer from bytes 1..4 of the report."""

    # byte 1
    SELECT = 1 << 0
    START = 1 << 3
    # byte 2
    TRIANGLE = 1 << 12
    CIRCLE = 1 << 13
    CROSS = 1 << 14
    SQUARE = 1 << 15
    # byte 3 (the MOVE / T bits are mirrored in byte 4 and folded in by the parser)
    PS = 1 << 16
    MOVE = 1 << 19
    T = 1 << 20


class Battery(IntEnum):
    MIN = 0x00
    PERCENT_20 = 0x01
    PERCENT_40 = 0x02
    PERCENT_60 = 0x03
    PERCENT_80 = 0x04
    MAX = 0x05
    CHARGING = 0xEE
    CHARGED = 0xEF

    @property
    def label(self) -> str:
        return {
            Battery.MIN: "empty",
            Battery.PERCENT_20: "20%",
            Battery.PERCENT_40: "40%",
            Battery.PERCENT_60: "60%",
            Battery.PERCENT_80: "80%",
            Battery.MAX: "100%",
            Battery.CHARGING: "charging",
            Battery.CHARGED: "charged",
        }[self]


# ---------------------------------------------------------------------------
# Input report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputReport:
    """Decoded controller state.  Raw sensor values are in sensor units."""

    buttons: int
    trigger: int  # 0..255
    sequence: int  # 4 bit rolling counter
    timestamp: int  # 16 bit rolling counter
    battery: int
    accel: tuple[int, int, int]  # raw, zero-centred, x/y/z
    gyro: tuple[int, int, int]  # raw, zero-centred, x/y/z
    accel_frames: tuple[tuple[int, int, int], tuple[int, int, int]]

    def pressed(self, button: Button) -> bool:
        return bool(self.buttons & button)

    @property
    def battery_state(self) -> Battery | None:
        try:
            return Battery(self.battery)
        except ValueError:
            return None


def _u16(data: bytes, off: int) -> int:
    return data[off] | (data[off + 1] << 8)


def _s16(data: bytes, off: int) -> int:
    return struct.unpack_from("<h", data, off)[0]


def _triple(data: bytes, off: int, model: Model) -> tuple[int, int, int]:
    """Read an X, Z, Y triple starting at *off* and return it as (x, y, z)."""
    if model is Model.ZCM1:
        x = _u16(data, off) - 0x8000
        z = _u16(data, off + 2) - 0x8000
        y = _u16(data, off + 4) - 0x8000
    else:
        x = _s16(data, off)
        z = _s16(data, off + 2)
        y = _s16(data, off + 4)
    return (x, y, z)


def parse_input_report(data: bytes, model: Model) -> InputReport:
    """Decode HID input report 0x01 (with the report id at byte 0)."""
    if len(data) < INPUT_REPORT_MIN_SIZE:
        raise ValueError(f"input report too short: {len(data)} bytes")
    if data[0] != REPORT_INPUT:
        raise ValueError(f"unexpected report id {data[0]:#04x}")

    buttons = data[1] | (data[2] << 8) | (data[3] << 16) | ((data[4] & 0xF0) << 20)
    # normalise the MOVE / T bits so callers only need one flag each
    if data[3] & 0x08 or data[4] & 0x40:
        buttons |= Button.MOVE
    if data[3] & 0x10 or data[4] & 0x80:
        buttons |= Button.T
    buttons &= 0x1FFFFF  # keep the flags we define

    sequence = data[4] & 0x0F
    trigger = data[5]
    timestamp = (data[0x0B] << 8) | (data[0x2B] if len(data) > 0x2B else 0)
    battery = data[0x0C]

    a1 = _triple(data, 0x0D, model)
    a2 = _triple(data, 0x13, model)
    g1 = _triple(data, 0x19, model)
    g2 = _triple(data, 0x1F, model)

    if model is Model.ZCM1:
        # two genuine half-frames; average them like psmoveapi does
        accel = tuple((p + q) // 2 for p, q in zip(a1, a2))
        gyro = tuple((p + q) // 2 for p, q in zip(g1, g2))
    else:
        # ZCM2 duplicates the sample; the first copy is authoritative
        accel = a1
        gyro = g1

    return InputReport(
        buttons=buttons,
        trigger=trigger,
        sequence=sequence,
        timestamp=timestamp,
        battery=battery,
        accel=accel,  # type: ignore[arg-type]
        gyro=gyro,  # type: ignore[arg-type]
        accel_frames=(a1, a2),
    )


# ---------------------------------------------------------------------------
# Output report (sphere LEDs + rumble)
# ---------------------------------------------------------------------------


def build_led_report(r: int, g: int, b: int, rumble: int = 0) -> bytes:
    """Build output report 0x06.  All values 0..255."""
    for v in (r, g, b, rumble):
        if not 0 <= v <= 255:
            raise ValueError("LED / rumble values must be 0..255")
    return bytes([REPORT_SET_LEDS, 0x00, r, g, b, 0x00, rumble, 0x00, 0x00])


# ---------------------------------------------------------------------------
# Bluetooth address feature reports
# ---------------------------------------------------------------------------


def btaddr_to_str(raw: bytes) -> str:
    """The controller stores addresses least-significant byte first."""
    if len(raw) != 6:
        raise ValueError("bluetooth address must be 6 bytes")
    return ":".join(f"{b:02x}" for b in reversed(raw))


def btaddr_from_str(text: str) -> bytes:
    parts = text.strip().replace("-", ":").split(":")
    if len(parts) != 6:
        raise ValueError(f"malformed bluetooth address: {text!r}")
    return bytes(int(p, 16) for p in reversed(parts))


def parse_btaddr_report(data: bytes) -> tuple[str, str]:
    """Decode feature report 0x04 into (controller_addr, host_addr)."""
    if len(data) < BTADDR_GET_SIZE:
        raise ValueError("bluetooth address report too short")
    return btaddr_to_str(bytes(data[1:7])), btaddr_to_str(bytes(data[10:16]))


def build_set_btaddr_report(host_addr: str) -> bytes:
    body = bytearray(BTADDR_SET_SIZE)
    body[0] = REPORT_SET_BTADDR
    body[1:7] = btaddr_from_str(host_addr)
    return bytes(body)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

# Rough LSB-per-g when the calibration blob is unavailable.  The game
# additionally normalises against the measured resting magnitude, so these
# only need to be in the right ballpark.
NOMINAL_LSB_PER_G = {Model.ZCM1: 4500.0, Model.ZCM2: 4096.0}


@dataclass(frozen=True)
class AccelCalibration:
    """Linear map raw -> g per axis: g = raw * factor + offset."""

    factor: tuple[float, float, float]
    offset: tuple[float, float, float]

    @classmethod
    def nominal(cls, model: Model) -> "AccelCalibration":
        f = 1.0 / NOMINAL_LSB_PER_G[model]
        return cls(factor=(f, f, f), offset=(0.0, 0.0, 0.0))

    @classmethod
    def from_extremes(cls, low: tuple[int, int, int], high: tuple[int, int, int]) -> "AccelCalibration":
        factor = []
        offset = []
        for lo, hi in zip(low, high):
            if hi - lo <= 0:
                raise ValueError("calibration extremes are not ordered")
            f = 2.0 / float(hi - lo)
            factor.append(f)
            offset.append(-(f * lo) - 1.0)
        return cls(factor=tuple(factor), offset=tuple(offset))  # type: ignore[arg-type]

    def apply(self, raw: tuple[int, int, int]) -> tuple[float, float, float]:
        return tuple(r * f + o for r, f, o in zip(raw, self.factor, self.offset))  # type: ignore[return-value]

    def magnitude(self, raw: tuple[int, int, int]) -> float:
        x, y, z = self.apply(raw)
        return math.sqrt(x * x + y * y + z * z)


def assemble_calibration_blob(chunks: list[bytes], model: Model) -> bytes:
    """Stitch the 0x10 feature report chunks into one blob (psmoveapi layout).

    Each chunk is 49 bytes: report id, chunk index, 47 bytes of payload.  The
    first chunk is copied whole; later chunks drop their two header bytes.
    """
    size = CALIBRATION_CHUNK_SIZE
    dest = bytearray(CALIBRATION_BLOB_SIZE[model])
    seen = set()
    for chunk in chunks:
        if len(chunk) < size:
            raise ValueError("calibration chunk too short")
        idx = chunk[1]
        if idx == 0x00:
            dest[0:size] = chunk[:size]
        elif idx in (0x01, 0x81):
            dest[size : 2 * size - 2] = chunk[2:size]
        elif idx == 0x82:
            dest[2 * size - 2 : 3 * size - 4] = chunk[2:size]
        else:
            raise ValueError(f"unknown calibration chunk index {idx:#04x}")
        seen.add(idx)
    expected = {0x00, 0x01, 0x82} if model is Model.ZCM1 else {0x00, 0x81}
    if seen != expected:
        raise ValueError(f"incomplete calibration data, got chunks {sorted(seen)}")
    return bytes(dest)


def parse_accel_calibration(blob: bytes, model: Model) -> AccelCalibration:
    """Extract the +-1g accelerometer readings from the calibration blob."""
    if model is Model.ZCM1:
        base = 0x04

        def rd(orientation: int, axis: int) -> int:
            return _u16(blob, base + 6 * orientation + 2 * axis) - 0x8000

        low = (rd(1, 0), rd(5, 1), rd(2, 2))
        high = (rd(3, 0), rd(4, 1), rd(0, 2))
    else:
        base = 0x02

        def rd(orientation: int, axis: int) -> int:
            return _s16(blob, base + 6 * orientation + 2 * axis)

        low = (rd(1, 0), rd(3, 1), rd(5, 2))
        high = (rd(0, 0), rd(2, 1), rd(4, 2))
    return AccelCalibration.from_extremes(low, high)
