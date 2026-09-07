"""Raw HID transports for the PS Move.

Two backends are provided:

* ``hidraw`` - Linux only, no third-party dependency.  Talks straight to
  ``/dev/hidrawN`` and enumerates through sysfs.
* ``hidapi`` - cross platform, uses the ``hid`` PyPI package (``pip install
  hidapi``).

Both expose the same tiny interface: enumerate(), read(), write(),
get_feature_report(), send_feature_report(), close().
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

from .protocol import PRODUCT_IDS, VENDOR_ID, Model


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    vendor_id: int
    product_id: int
    bluetooth: bool  # True when connected over bluetooth, False for USB
    address: str  # controller bluetooth address if known, else ""
    backend: str

    @property
    def model(self) -> Model:
        return Model.from_pid(self.product_id)

    @property
    def transport(self) -> str:
        return "bluetooth" if self.bluetooth else "usb"

    @property
    def label(self) -> str:
        return self.address or self.path


class Transport(Protocol):
    info: DeviceInfo

    def read(self, size: int, timeout_ms: int) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def get_feature_report(self, report_id: int, size: int) -> bytes: ...
    def send_feature_report(self, data: bytes) -> int: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Linux hidraw
# ---------------------------------------------------------------------------

_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction: int, type_: str, nr: int, size: int) -> int:
    return (direction << _IOC_DIRSHIFT) | (ord(type_) << _IOC_TYPESHIFT) | (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT)


def HIDIOCSFEATURE(size: int) -> int:
    return _ioc(_IOC_WRITE | _IOC_READ, "H", 0x06, size)


def HIDIOCGFEATURE(size: int) -> int:
    return _ioc(_IOC_WRITE | _IOC_READ, "H", 0x07, size)


SYSFS_HIDRAW = Path("/sys/class/hidraw")
BUS_USB = 0x03
BUS_BLUETOOTH = 0x05


def _parse_uevent(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def enumerate_hidraw(sysfs: Path = SYSFS_HIDRAW) -> Iterator[DeviceInfo]:
    if not sysfs.exists():
        return
    for node in sorted(sysfs.iterdir()):
        uevent = node / "device" / "uevent"
        try:
            fields = _parse_uevent(uevent.read_text())
        except OSError:
            continue
        hid_id = fields.get("HID_ID", "")
        try:
            bus_s, vid_s, pid_s = hid_id.split(":")
            bus, vid, pid = int(bus_s, 16), int(vid_s, 16), int(pid_s, 16)
        except ValueError:
            continue
        if vid != VENDOR_ID or pid not in PRODUCT_IDS:
            continue
        yield DeviceInfo(
            path=f"/dev/{node.name}",
            vendor_id=vid,
            product_id=pid,
            bluetooth=(bus == BUS_BLUETOOTH),
            address=fields.get("HID_UNIQ", "").lower() if bus == BUS_BLUETOOTH else "",
            backend="hidraw",
        )


class HidrawTransport:
    def __init__(self, info: DeviceInfo):
        if not sys.platform.startswith("linux"):
            raise RuntimeError("the hidraw backend only exists on Linux; use the hidapi backend")
        self.info = info
        self.fd = os.open(info.path, os.O_RDWR | os.O_NONBLOCK)

    def read(self, size: int, timeout_ms: int) -> bytes:
        import select

        r, _, _ = select.select([self.fd], [], [], timeout_ms / 1000.0)
        if not r:
            return b""
        try:
            return os.read(self.fd, size)
        except BlockingIOError:
            return b""

    def write(self, data: bytes) -> int:
        return os.write(self.fd, data)

    def get_feature_report(self, report_id: int, size: int) -> bytes:
        import fcntl

        buf = bytearray(size)
        buf[0] = report_id
        n = fcntl.ioctl(self.fd, HIDIOCGFEATURE(size), buf, True)
        return bytes(buf[:n]) if isinstance(n, int) and n > 0 else bytes(buf)

    def send_feature_report(self, data: bytes) -> int:
        import fcntl

        buf = bytearray(data)
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(len(buf)), buf, True)
        return len(buf)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# hidapi (cross platform)
# ---------------------------------------------------------------------------


def _import_hid():
    try:
        import hid  # type: ignore

        return hid
    except ImportError:
        return None


_BTADDR_RE = re.compile(r"^([0-9a-f]{2})[:\-]?([0-9a-f]{2})[:\-]?([0-9a-f]{2})[:\-]?([0-9a-f]{2})[:\-]?([0-9a-f]{2})[:\-]?([0-9a-f]{2})$")


def normalize_btaddr(text: str) -> str:
    """'00-06-F7-AA-BB-CC' / '0006f7aabbcc' -> '00:06:f7:aa:bb:cc', or '' if not an address."""
    m = _BTADDR_RE.match((text or "").strip().lower())
    return ":".join(m.groups()) if m else ""


def enumerate_hidapi() -> Iterator[DeviceInfo]:
    hid = _import_hid()
    if hid is None:
        return
    seen: set[str] = set()
    for pid in PRODUCT_IDS:
        for d in hid.enumerate(VENDOR_ID, pid):
            path = d["path"]
            if isinstance(path, bytes):
                path = path.decode(errors="replace")
            # psmoveapi convention (all platforms): a controller connected over
            # bluetooth reports its own address as the serial number; over USB
            # the serial is empty.  Windows lists several HID collections per
            # device, so de-duplicate by address.
            address = normalize_btaddr(d.get("serial_number") or "")
            key = address or path
            if key in seen:
                continue
            seen.add(key)
            yield DeviceInfo(
                path=path,
                vendor_id=VENDOR_ID,
                product_id=pid,
                bluetooth=bool(address),
                address=address,
                backend="hidapi",
            )


class HidapiTransport:
    def __init__(self, info: DeviceInfo):
        hid = _import_hid()
        if hid is None:
            raise RuntimeError("hidapi backend requested but the 'hid' package is not installed")
        self.info = info
        path = info.path.encode() if isinstance(info.path, str) else info.path
        if hasattr(hid, "device"):  # 'hidapi' package (cython-hidapi)
            self._dev = hid.device()
            self._dev.open_path(path)
        elif hasattr(hid, "Device"):  # 'hid' package (pyhidapi)
            self._dev = hid.Device(path=path)
        else:
            raise RuntimeError("unrecognised 'hid' module; install the 'hidapi' package")

    def read(self, size: int, timeout_ms: int) -> bytes:
        data = self._dev.read(size, timeout_ms)
        return bytes(data) if data else b""

    def write(self, data: bytes) -> int:
        return self._dev.write(bytes(data))

    def get_feature_report(self, report_id: int, size: int) -> bytes:
        return bytes(self._dev.get_feature_report(report_id, size))

    def send_feature_report(self, data: bytes) -> int:
        return self._dev.send_feature_report(bytes(data))

    def close(self) -> None:
        try:
            self._dev.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def available_backends() -> list[str]:
    names = []
    if sys.platform.startswith("linux"):
        names.append("hidraw")
    if _import_hid() is not None:
        names.append("hidapi")
    return names


def enumerate_devices(backend: str | None = None) -> list[DeviceInfo]:
    """List connected PS Move controllers.

    With ``backend=None`` the first backend that finds anything wins; hidraw
    is preferred on Linux because it needs no extra packages.
    """
    order = [backend] if backend else available_backends()
    for name in order:
        if name == "hidraw":
            found = list(enumerate_hidraw())
        elif name == "hidapi":
            found = list(enumerate_hidapi())
        else:
            raise ValueError(f"unknown backend {name!r}")
        if found or backend:
            return found
    return []


def open_transport(info: DeviceInfo) -> Transport:
    if info.backend == "hidraw":
        return HidrawTransport(info)
    if info.backend == "hidapi":
        return HidapiTransport(info)
    raise ValueError(f"unknown backend {info.backend!r}")
