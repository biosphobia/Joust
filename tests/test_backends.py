from pathlib import Path

from joust.psmove.backends import HIDIOCGFEATURE, HIDIOCSFEATURE, DeviceInfo, enumerate_hidraw
from joust.psmove.protocol import PRODUCT_ID_ZCM1, PRODUCT_ID_ZCM2, VENDOR_ID, Model


def test_ioctl_numbers_match_linux_headers():
    # values computed from <linux/hidraw.h>: _IOC(_IOC_READ|_IOC_WRITE, 'H', 0x06/0x07, len)
    assert HIDIOCSFEATURE(49) == 0xC0314806
    assert HIDIOCGFEATURE(49) == 0xC0314807
    assert HIDIOCGFEATURE(16) == 0xC0104807


def test_enumerate_hidraw_from_fake_sysfs(tmp_path: Path):
    def node(name, hid_id, uniq=""):
        d = tmp_path / name / "device"
        d.mkdir(parents=True)
        (d / "uevent").write_text(f"DRIVER=sony\nHID_ID={hid_id}\nHID_NAME=Motion Controller\nHID_UNIQ={uniq}\n")

    node("hidraw0", "0005:0000054C:000003D5", "00:06:F7:AA:BB:CC")
    node("hidraw1", "0003:0000054C:00000C5E")
    node("hidraw2", "0003:0000046D:0000C52B")
    node("hidraw3", "garbage")
    found = list(enumerate_hidraw(tmp_path))
    assert [d.path for d in found] == ["/dev/hidraw0", "/dev/hidraw1"]
    assert found[0].bluetooth and found[0].address == "00:06:f7:aa:bb:cc" and found[0].model is Model.ZCM1
    assert not found[1].bluetooth and found[1].address == "" and found[1].model is Model.ZCM2
    assert found[0].label == "00:06:f7:aa:bb:cc" and found[1].label == "/dev/hidraw1"


def test_device_info_model():
    assert DeviceInfo("/dev/x", VENDOR_ID, PRODUCT_ID_ZCM1, True, "", "hidraw").model is Model.ZCM1
    assert DeviceInfo("/dev/x", VENDOR_ID, PRODUCT_ID_ZCM2, False, "", "hidraw").transport == "usb"


def test_normalize_btaddr():
    from joust.psmove.backends import normalize_btaddr

    assert normalize_btaddr("00-06-F7-AA-BB-CC") == "00:06:f7:aa:bb:cc"
    assert normalize_btaddr("0006f7aabbcc") == "00:06:f7:aa:bb:cc"
    assert normalize_btaddr("00:06:f7:aa:bb:cc") == "00:06:f7:aa:bb:cc"
    assert normalize_btaddr("") == ""
    assert normalize_btaddr("not-an-address") == ""


def test_hidapi_enumeration_dedupes_and_detects_bluetooth(monkeypatch):
    from joust.psmove import backends

    class FakeHid:
        @staticmethod
        def enumerate(vid, pid):
            if pid != PRODUCT_ID_ZCM2:
                return []
            return [
                {"path": b"\\\\?\\hid#col01", "serial_number": "00-06-F7-11-22-33"},
                {"path": b"\\\\?\\hid#col02", "serial_number": "00-06-F7-11-22-33"},
                {"path": b"\\\\?\\hid#usb", "serial_number": ""},
            ]

    monkeypatch.setattr(backends, "_import_hid", lambda: FakeHid)
    found = list(backends.enumerate_hidapi())
    assert len(found) == 2
    assert found[0].bluetooth and found[0].address == "00:06:f7:11:22:33"
    assert not found[1].bluetooth and found[1].transport == "usb"
