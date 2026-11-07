import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-camera-identify.py"
SPEC = importlib.util.spec_from_file_location("camera_identify", SCRIPT)
camera = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(camera)


class CameraIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.devices = Path(self.temp.name)
        self.power = self.devices / "camera-power-cam2" / "power_on"
        self.power.parent.mkdir()
        self.power.write_text("0\n")

    @patch.object(camera.time, "sleep")
    @patch.object(camera, "find_bus", return_value="5")
    @patch.object(camera.subprocess, "run")
    def test_exact_sensor_identity_and_power_off(self, run, bus, sleep):
        run.return_value.stdout = "0x56 0x0d 0x42\n"
        result = camera.identify("cam2", self.devices)
        self.assertTrue(result["matched"])
        self.assertEqual(result["identity"], "0x560d42")
        self.assertEqual(self.power.read_text(), "0\n")
        self.assertEqual(run.call_args.args[0],
                         ["i2ctransfer", "-y", "5", "w2@0x36", "0x30", "0x0a", "r3"])

    @patch.object(camera.time, "sleep")
    @patch.object(camera, "find_bus", return_value="5")
    @patch.object(camera.subprocess, "run", side_effect=subprocess.TimeoutExpired("i2c", 5))
    def test_timeout_releases_power(self, run, bus, sleep):
        with self.assertRaises(subprocess.TimeoutExpired):
            camera.identify("cam2", self.devices)
        self.assertEqual(self.power.read_text(), "0\n")

    @patch.object(camera, "find_bus", return_value="5")
    @patch.object(camera.subprocess, "run")
    def test_busy_camera_is_untouched(self, run, bus):
        self.power.write_text("1\n")
        with self.assertRaisesRegex(RuntimeError, "already powered"):
            camera.identify("cam2", self.devices)
        run.assert_not_called()
        self.assertEqual(self.power.read_text(), "1\n")

    def test_missing_bus_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "expected one CCI bus"):
            camera.find_bus("ac15000", "0", self.devices)


if __name__ == "__main__":
    unittest.main()
