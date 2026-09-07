import struct

import pytest

from joust.psmove import protocol as P


def make_report(model, accel=(0, 0, 0), gyro=(0, 0, 0), buttons=(0, 0, 0, 0), trigger=0, battery=0x05, seq=3):
    buf = bytearray(49)
    buf[0] = 0x01
    buf[1], buf[2], buf[3] = buttons[0], buttons[1], buttons[2]
    buf[4] = (buttons[3] & 0xF0) | (seq & 0x0F)
    buf[5] = trigger
    buf[0x0B] = 0x12
    buf[0x0C] = battery
    buf[0x2B] = 0x34
    x, y, z = accel
    gx, gy, gz = gyro

    def put(off, v):
        if model is P.Model.ZCM1:
            struct.pack_into("<H", buf, off, (v + 0x8000) & 0xFFFF)
        else:
            struct.pack_into("<h", buf, off, v)

    for base in (0x0D, 0x13):  # wire order is X, Z, Y
        put(base, x)
        put(base + 2, z)
        put(base + 4, y)
    for base in (0x19, 0x1F):
        put(base, gx)
        put(base + 2, gz)
        put(base + 4, gy)
    return bytes(buf)


@pytest.mark.parametrize("model", [P.Model.ZCM1, P.Model.ZCM2])
def test_accel_axes_and_sign(model):
    rep = P.parse_input_report(make_report(model, accel=(100, -4200, 7)), model)
    assert rep.accel == (100, -4200, 7)
    assert rep.gyro == (0, 0, 0)
    assert rep.sequence == 3
    assert rep.timestamp == 0x1234
    assert rep.battery_state is P.Battery.MAX


def test_zcm1_averages_the_two_half_frames():
    buf = bytearray(make_report(P.Model.ZCM1, accel=(1000, 0, 0)))
    struct.pack_into("<H", buf, 0x13, (2000 + 0x8000) & 0xFFFF)  # second frame X
    rep = P.parse_input_report(bytes(buf), P.Model.ZCM1)
    assert rep.accel[0] == 1500
    assert rep.accel_frames[0][0] == 1000 and rep.accel_frames[1][0] == 2000


def test_buttons():
    rep = P.parse_input_report(make_report(P.Model.ZCM2, buttons=(0x09, 0xF0, 0x19, 0xC0), trigger=200), P.Model.ZCM2)
    for b in (P.Button.SELECT, P.Button.START, P.Button.TRIANGLE, P.Button.CIRCLE, P.Button.CROSS, P.Button.SQUARE, P.Button.PS, P.Button.MOVE, P.Button.T):
        assert rep.pressed(b), b
    assert rep.trigger == 200
    rep = P.parse_input_report(make_report(P.Model.ZCM2, buttons=(0, 0, 0, 0x40)), P.Model.ZCM2)
    assert rep.pressed(P.Button.MOVE) and not rep.pressed(P.Button.T)


def test_short_report_rejected():
    with pytest.raises(ValueError):
        P.parse_input_report(b"\x01" + b"\x00" * 10, P.Model.ZCM2)


def test_led_report():
    rep = P.build_led_report(1, 2, 3, rumble=200)
    assert rep == bytes([0x06, 0, 1, 2, 3, 0, 200, 0, 0])
    assert len(rep) == P.LED_REPORT_SIZE
    with pytest.raises(ValueError):
        P.build_led_report(256, 0, 0)


def test_btaddr_roundtrip():
    raw = bytes([0x66, 0x55, 0x44, 0x33, 0x22, 0x11])
    assert P.btaddr_to_str(raw) == "11:22:33:44:55:66"
    assert P.btaddr_from_str("11:22:33:44:55:66") == raw
    report = bytes([0x04]) + raw + b"\0\0\0" + bytes(reversed(bytes(range(0xA0, 0xA6))))
    controller, host = P.parse_btaddr_report(report)
    assert controller == "11:22:33:44:55:66"
    assert host == "a0:a1:a2:a3:a4:a5"
    setrep = P.build_set_btaddr_report(host)
    assert setrep[0] == 0x05 and len(setrep) == 23
    assert setrep[1:7] == bytes(reversed(bytes(range(0xA0, 0xA6))))


def _zcm2_calibration_blob():
    blob = bytearray(P.CALIBRATION_BLOB_SIZE[P.Model.ZCM2])
    values = {  # orientation -> (x, y, z)
        0: (4100, 0, 0), 1: (-4000, 0, 0),
        2: (0, 4200, 0), 3: (0, -4100, 0),
        4: (0, 0, 4300), 5: (0, 0, -4150),
    }
    for o, (x, y, z) in values.items():
        struct.pack_into("<hhh", blob, 0x02 + 6 * o, x, y, z)
    return bytes(blob)


def test_zcm2_calibration_parse_and_chunks():
    blob = _zcm2_calibration_blob()
    size = P.CALIBRATION_CHUNK_SIZE
    chunk0 = blob[:size]
    chunk1 = bytes([0x10, 0x81]) + blob[size:]
    assembled = P.assemble_calibration_blob([chunk1, chunk0], P.Model.ZCM2)
    assert assembled == blob
    cal = P.parse_accel_calibration(assembled, P.Model.ZCM2)
    assert cal.apply((4100, 0, 0))[0] == pytest.approx(1.0)
    assert cal.apply((-4000, 0, 0))[0] == pytest.approx(-1.0)
    assert cal.apply((0, 0, 4300))[2] == pytest.approx(1.0)
    assert cal.magnitude((50, -4100, 75)) == pytest.approx(1.0, abs=0.03)
    with pytest.raises(ValueError):
        P.assemble_calibration_blob([chunk0], P.Model.ZCM2)


def test_zcm1_calibration_parse():
    blob = bytearray(P.CALIBRATION_BLOB_SIZE[P.Model.ZCM1])
    values = {
        0: (0, 0, 4600), 1: (-4400, 0, 0), 2: (0, 0, -4500),
        3: (4500, 0, 0), 4: (0, 4450, 0), 5: (0, -4550, 0),
    }
    for o, (x, y, z) in values.items():
        struct.pack_into("<HHH", blob, 0x04 + 6 * o, *((v + 0x8000) & 0xFFFF for v in (x, y, z)))
    cal = P.parse_accel_calibration(bytes(blob), P.Model.ZCM1)
    assert cal.apply((4500, 4450, 4600)) == pytest.approx((1.0, 1.0, 1.0))
    assert cal.apply((-4400, -4550, -4500)) == pytest.approx((-1.0, -1.0, -1.0))


def test_nominal_calibration():
    cal = P.AccelCalibration.nominal(P.Model.ZCM2)
    assert cal.magnitude((0, 4096, 0)) == pytest.approx(1.0)
