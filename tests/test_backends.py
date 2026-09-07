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


def _fake_hid(entries):
    class FakeHid:
        @staticmethod
        def enumerate(vid, pid):
            return [dict(e, product_id=pid) for e in entries if e.get("pid", pid) == pid]

    return FakeHid


def test_hidapi_windows_picks_col01_for_data_and_col02_for_address(monkeypatch):
    from joust.psmove import backends

    bt = "00-06-F7-11-22-33"
    entries = [  # Windows lists col02 first here on purpose
        {"path": b"\\\\?\\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&0002054c_pid&0c5e&col02#9&1234&0&0001#{...}", "serial_number": bt, "pid": PRODUCT_ID_ZCM2},
        {"path": b"\\\\?\\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&0002054c_pid&0c5e&col01#9&1234&0&0000#{...}", "serial_number": bt, "pid": PRODUCT_ID_ZCM2},
        {"path": b"\\\\?\\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&0002054c_pid&0c5e&col03#9&1234&0&0002#{...}", "serial_number": bt, "pid": PRODUCT_ID_ZCM2},
        {"path": b"\\\\?\\hid#vid_054c&pid_0c5e&col01#7&abcd&0&0000#{...}", "serial_number": "0", "pid": PRODUCT_ID_ZCM2},
        {"path": b"\\\\?\\hid#vid_054c&pid_0c5e&col02#7&abcd&0&0001#{...}", "serial_number": "0", "pid": PRODUCT_ID_ZCM2},
        {"path": b"\\\\?\\hid#vid_054c&pid_0c5e&col03#7&abcd&0&0002#{...}", "serial_number": "0", "pid": PRODUCT_ID_ZCM2},
    ]
    monkeypatch.setattr(backends, "_import_hid", lambda: _fake_hid(entries))
    found = list(backends.enumerate_hidapi())
    assert len(found) == 2
    bt_dev, usb_dev = found
    assert bt_dev.bluetooth and bt_dev.address == "00:06:f7:11:22:33" and bt_dev.model is Model.ZCM2
    assert "&col01#" in bt_dev.path and "&col02#" in bt_dev.addr_path
    assert not usb_dev.bluetooth and usb_dev.transport == "usb"
    assert "&col01#" in usb_dev.path and "&col02#" in usb_dev.addr_path


def test_hidapi_single_entry_platforms(monkeypatch):
    from joust.psmove import backends

    entries = [
        {"path": b"/dev/hidraw3", "serial_number": "00:06:f7:aa:bb:cc", "pid": PRODUCT_ID_ZCM1},
        {"path": b"/dev/hidraw4", "serial_number": "", "pid": PRODUCT_ID_ZCM2},
    ]
    monkeypatch.setattr(backends, "_import_hid", lambda: _fake_hid(entries))
    found = list(backends.enumerate_hidapi())
    assert [(d.path, d.bluetooth, d.addr_path) for d in found] == [("/dev/hidraw3", True, ""), ("/dev/hidraw4", False, "")]


def test_hidapi_transport_routes_address_reports(monkeypatch):
    from joust.psmove import backends

    opened = []

    class FakeDev:
        def __init__(self):
            self.path = None
            self.feature_calls = []
            self.closed = False

        def open_path(self, path):
            self.path = path
            opened.append(path)

        def read(self, size, timeout):
            return [1, 2, 3] if b"col01" in self.path else []

        def write(self, data):
            return len(data)

        def get_feature_report(self, rid, size):
            self.feature_calls.append(("get", rid, size))
            return bytes([rid]) + bytes(size - 1)

        def send_feature_report(self, data):
            self.feature_calls.append(("send", data[0], len(data)))
            return len(data)

        def close(self):
            self.closed = True

    class FakeHid:
        device = FakeDev

    monkeypatch.setattr(backends, "_import_hid", lambda: FakeHid)
    info = backends.DeviceInfo("x&col01#", VENDOR_ID, PRODUCT_ID_ZCM2, True, "00:06:f7:11:22:33", "hidapi", addr_path="x&col02#")
    t = backends.HidapiTransport(info)
    assert opened == [b"x&col01#", b"x&col02#"]
    assert t.read(64, 10) == b"\x01\x02\x03"
    t.get_feature_report(0x04, 16)
    t.send_feature_report(bytes([0x05]) + bytes(22))
    t.get_feature_report(0x10, 49)
    assert [c[:2] for c in t._addr_dev.feature_calls] == [("get", 0x04), ("send", 0x05)]
    assert [c[:2] for c in t._dev.feature_calls] == [("get", 0x10)]
    t.close()
    assert t._dev.closed and t._addr_dev.closed
