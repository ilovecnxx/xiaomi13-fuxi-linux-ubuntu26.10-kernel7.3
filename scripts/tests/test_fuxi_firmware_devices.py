"""Exercise radio isolation and failed firmware recovery without real sysfs."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "packaging/fuxi-device/root/usr/sbin/fuxi-firmware-devices"


class FirmwareDevicesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sys = self.root / "sys"
        self.fw = self.root / "firmware"
        self.env = {**os.environ, "FUXI_SYSFS_ROOT": str(self.sys),
                    "FUXI_FIRMWARE_ROOT": str(self.fw),
                    "FUXI_CONFIG_ROOT": str(self.root / "config")}

    def file(self, path, value=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def run_mode(self, mode):
        return subprocess.run(["sh", str(SCRIPT), mode], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def wifi(self):
        device = self.sys / "bus/pci/devices/0000:01:00.0"
        self.file(device / "vendor", "0x17cb\n")
        self.file(device / "device", "0x1107\n")
        bind = self.file(self.sys / "bus/pci/drivers/ath12k_wifi7_pci/bind")
        self.file(self.fw / "ath12k/WCN7850/hw2.0/amss.bin")
        return device, bind

    def bluetooth(self):
        device = self.sys / "bus/serial/devices/serial0-0"
        self.file(device / "of_node/compatible", "qcom,wcn7850-bt\0")
        bind = self.file(self.sys / "bus/serial/drivers/hci_uart_qca/bind")
        for name in ("hmtbtfw20.tlv", "hmtnv20.bin"):
            self.file(self.fw / "qca" / name)
        return device, bind

    def test_core_starts_adsp_without_touching_radios_or_modem(self):
        adsp = self.file(self.sys / "class/remoteproc/remoteproc0/state", "offline\n")
        self.file(adsp.parent / "name", "adsp\n")
        modem = self.file(self.sys / "devices/4080000.remoteproc/remoteproc/remoteproc1/state", "offline\n")
        self.file(modem.parent / "name", "mpss\n")
        (adsp.parent.parent / "remoteproc1").symlink_to(modem.parent)
        _, wifi_bind = self.wifi()
        _, bt_bind = self.bluetooth()
        result = self.run_mode("core")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(adsp.read_text(), "start\n")
        self.assertEqual(modem.read_text(), "offline\n")
        self.assertEqual(wifi_bind.read_text(), "")
        self.assertEqual(bt_bind.read_text(), "")

    def test_wifi_never_rebinds_a_bound_controller(self):
        device, bind = self.wifi()
        (device / "driver").symlink_to(bind.parent)
        self.assertEqual(self.run_mode("wifi").returncode, 0)
        self.assertEqual(bind.read_text(), "")

    def test_wifi_bind_and_missing_firmware_failure(self):
        _, bind = self.wifi()
        result = self.run_mode("wifi")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(bind.read_text(), "0000:01:00.0\n")
        (self.fw / "ath12k/WCN7850/hw2.0/amss.bin").unlink()
        self.assertEqual(self.run_mode("wifi").returncode, 1)

    def test_kernel_bind_error_fails_service(self):
        _, bind = self.wifi()
        bind.unlink()
        bind.mkdir()
        self.assertEqual(self.run_mode("wifi").returncode, 1)

    def test_bluetooth_does_not_touch_wifi(self):
        _, wifi_bind = self.wifi()
        _, bt_bind = self.bluetooth()
        self.assertEqual(self.run_mode("bluetooth").returncode, 0)
        self.assertEqual(bt_bind.read_text(), "serial0-0\n")
        self.assertEqual(wifi_bind.read_text(), "")

    def test_bluetooth_missing_firmware_preserves_bound_device(self):
        device, bind = self.bluetooth()
        unbind = self.file(bind.parent / "unbind")
        (device / "driver").symlink_to(bind.parent)
        (self.fw / "qca/hmtbtfw20.tlv").unlink()
        self.assertEqual(self.run_mode("bluetooth").returncode, 1)
        self.assertEqual(unbind.read_text(), "")
        self.assertEqual(bind.read_text(), "")

    def test_invalid_mode_fails_without_device_access(self):
        self.assertEqual(self.run_mode("invalid").returncode, 2)


if __name__ == "__main__":
    unittest.main()
