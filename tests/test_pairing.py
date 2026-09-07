from pathlib import Path

from joust.psmove import pairing
from joust.psmove.protocol import PRODUCT_ID_ZCM2, Model


def test_register_with_bluez_writes_files(tmp_path: Path):
    host = "aa:bb:cc:dd:ee:ff"
    (tmp_path / host.upper()).mkdir()
    changed = pairing.register_with_bluez("00:06:f7:11:22:33", host, Model.ZCM2, bluez_dir=tmp_path, restart=False)
    assert changed
    info = (tmp_path / host.upper() / "00:06:F7:11:22:33" / "info").read_text()
    assert "Trusted=true" in info
    assert f"Product={PRODUCT_ID_ZCM2}" in info
    cache = (tmp_path / host.upper() / "cache" / "00:06:F7:11:22:33").read_text()
    assert "[ServiceRecords]" in cache
    # idempotent
    assert not pairing.register_with_bluez("00:06:f7:11:22:33", host, Model.ZCM2, bluez_dir=tmp_path, restart=False)
