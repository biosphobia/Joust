"""USB pairing helper.

Pairing a Move with a PC has two halves:

1. Tell the controller which Bluetooth host to connect to.  This is done over
   USB with feature report 0x05.
2. Tell the host to accept the controller.  On Linux/BlueZ 5 that means
   dropping an ``info`` file (and a service-record cache) under
   ``/var/lib/bluetooth/<host>/<controller>/`` and restarting bluetoothd,
   exactly like psmoveapi's ``psmove pair`` does.  Root is required.

After that: unplug the USB cable and press the PS button.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from . import protocol as P
from .backends import DeviceInfo, enumerate_devices, open_transport

log = logging.getLogger(__name__)

BLUEZ_DIR = Path("/var/lib/bluetooth")


def bluez_info_entry(pid: int) -> str:
    return (
        "[General]\n"
        "Name=Motion Controller\n"
        "Class=0x002508\n"
        "SupportedTechnologies=BR/EDR\n"
        "Trusted=true\n"
        "Blocked=false\n"
        "Services=00001124-0000-1000-8000-00805f9b34fb;\n"
        "\n"
        "[DeviceID]\n"
        "Source=1\n"
        "Vendor=1356\n"
        f"Product={pid}\n"
        "Version=1\n"
    )


# HID service record for the Move, as captured by the psmoveapi project.
BLUEZ_CACHE_ENTRY = (
    "[General]\n"
    "Name=Motion Controller\n"
    "\n"
    "[ServiceRecords]\n"
    "0x00010000=3601920900000A000100000900013503191124090004350D"
    "35061901000900113503190011090006350909656E09006A09010009000"
    "93508350619112409010009000D350F350D350619010009001335031900"
    "110901002513576972656C65737320436F6E74726F6C6C6572090101251"
    "3576972656C65737320436F6E74726F6C6C6572090102251B536F6E7920"
    "436F6D707574657220456E7465727461696E6D656E74090200090100090"
    "2010901000902020800090203082109020428010902052801090206359A"
    "35980822259405010904A101A102850175089501150026FF00810375019"
    "513150025013500450105091901291381027501950D0600FF8103150026"
    "FF0005010901A10075089504350046FF0009300931093209358102C0050"
    "175089527090181027508953009019102750895300901B102C0A1028502"
    "750895300901B102C0A10285EE750895300901B102C0A10285EF7508953"
    "00901B102C0C00902073508350609040909010009020828000902092801"
    "09020A280109020B09010009020C093E8009020D280009020E2800\n"
)


def host_bluetooth_addresses() -> list[str]:
    """Addresses of local adapters, from sysfs (no bluez dependency)."""
    out = []
    root = Path("/sys/class/bluetooth")
    if root.exists():
        for hci in sorted(root.iterdir()):
            addr = hci / "address"
            try:
                out.append(addr.read_text().strip().lower())
            except OSError:
                continue
    return out


def read_addresses(info: DeviceInfo) -> tuple[str, str]:
    t = open_transport(info)
    try:
        data = t.get_feature_report(P.REPORT_GET_BTADDR, P.BTADDR_GET_SIZE)
        return P.parse_btaddr_report(data)
    finally:
        t.close()


def set_host_address(info: DeviceInfo, host: str) -> None:
    t = open_transport(info)
    try:
        t.send_feature_report(P.build_set_btaddr_report(host))
    finally:
        t.close()


def _write_if_changed(path: Path, content: str) -> bool:
    try:
        if path.read_text() == content:
            return False
    except OSError:
        pass
    path.write_text(content)
    return True


def register_with_bluez(controller: str, host: str, model: P.Model, bluez_dir: Path = BLUEZ_DIR, restart: bool = True) -> bool:
    """Create the BlueZ device records.  Returns True when files were changed."""
    host_dir = bluez_dir / host.upper()
    if not host_dir.is_dir():
        raise FileNotFoundError(f"{host_dir} does not exist; is bluez running and is {host} a local adapter?")
    dev_dir = host_dir / controller.upper()
    cache_dir = host_dir / "cache"
    pid = P.PRODUCT_ID_ZCM2 if model is P.Model.ZCM2 else P.PRODUCT_ID_ZCM1

    changed = False
    if restart:
        _bluetoothd("stop")
    try:
        dev_dir.mkdir(exist_ok=True)
        cache_dir.mkdir(exist_ok=True)
        changed |= _write_if_changed(dev_dir / "info", bluez_info_entry(pid))
        changed |= _write_if_changed(cache_dir / controller.upper(), BLUEZ_CACHE_ENTRY)
    finally:
        if restart:
            _bluetoothd("start")
    return changed


def _bluetoothd(action: str) -> None:
    try:
        subprocess.run(["systemctl", action, "bluetooth.service"], check=False, capture_output=True)
    except FileNotFoundError:
        log.warning("systemctl not found; restart bluetoothd by hand")


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def pair_all(host: str | None = None, register: bool = True) -> list[str]:
    """Pair every USB-connected controller.  Returns their addresses."""
    usb = [d for d in enumerate_devices() if not d.bluetooth]
    if not usb:
        log.error("no PS Move connected over USB")
        return []
    hosts = host_bluetooth_addresses()
    if host is None:
        if not hosts:
            hint = (
                "look it up under Settings > Bluetooth & devices > (adapter) or in Device Manager"
                if sys.platform.startswith("win")
                else "try `bluetoothctl show` or `hciconfig`"
            )
            raise RuntimeError(f"no local bluetooth adapter address found; pass --host aa:bb:cc:dd:ee:ff ({hint})")
        host = hosts[0]
    host = host.lower()
    linux = sys.platform.startswith("linux")
    paired = []
    for info in usb:
        controller, current_host = read_addresses(info)
        log.info("%s (%s): controller %s, current host %s", info.path, info.model.value, controller, current_host)
        if current_host != host:
            set_host_address(info, host)
            log.info("%s: host address set to %s", controller, host)
        else:
            log.info("%s: host address already %s", controller, host)
        if register and linux:
            if not _is_root():
                log.warning("not root: skipping BlueZ registration (run with sudo to do it)")
            else:
                register_with_bluez(controller, host, info.model)
                log.info("%s: registered with BlueZ", controller)
        elif register:
            log.info("%s: host address stored; now pair it from the OS bluetooth settings "
                     "(unplug, press PS, accept 'Motion Controller'; a ZCM1 on Windows needs psmoveapi's psmove pair)", controller)
        paired.append(controller)
    return paired
