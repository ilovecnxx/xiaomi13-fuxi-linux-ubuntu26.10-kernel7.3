"""Sensor health needs measurements and a clean close, not just exit status zero."""

import os
from pathlib import Path
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-health.sh"


class SensorHealthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = SCRIPT.read_text()
        start = script.index("  for sensor in accelerometer ")
        end = script.index("  crashes=", start)
        cls.sensor_loop = script[start:end]

    def probe(self, output, status=0):
        harness = r'''
set -uo pipefail
failures=0
pass() { printf 'PASS %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; failures=$((failures + 1)); }
timeout() { shift 3; "$@"; }
ssccli() {
    if [ "$2" = gyroscope ]; then
        printf '%s\n' "$SSC_OUTPUT"
        return "$SSC_STATUS"
    fi
    printf '%s sensor measurement: 1\n' "${2^}"
}
'''
        return subprocess.run(
            ["bash", "-s"],
            input=harness + self.sensor_loop + '\n[ "$failures" -eq 0 ]\n',
            text=True,
            capture_output=True,
            env={**os.environ, "SSC_OUTPUT": output, "SSC_STATUS": str(status)},
            timeout=5,
        )

    def test_xyz_data_passes_with_explicit_matrix_and_unit_limits(self):
        result = self.probe(
            "WARNING: Mount matrix provided by firmware is all 0, falling back to identity matrix!\n"
            "Gyroscope sensor measurement: X=0.002181 Y=-0.003088 Z=0.000000 m/s"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS gyroscope:", result.stdout)
        self.assertIn("WARN gyroscope 固件安装矩阵全零", result.stdout)
        self.assertIn("WARN gyroscope 单位沿用 CLI 原文", result.stdout)

    def test_discovery_without_measurements_does_not_pass(self):
        result = self.probe("SSC sensor discovered")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL gyroscope", result.stdout)

    def test_nonfinite_axis_does_not_pass(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                result = self.probe(
                    f"Gyroscope sensor measurement: X={value} Y=0.000000 Z=0.000000 m/s"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("PASS gyroscope:", result.stdout)

    def test_close_error_after_data_does_not_pass(self):
        result = self.probe(
            "Gyroscope sensor measurement: X=0.000000 Y=0.000000 Z=0.000000 m/s\n"
            "Unable to close gyroscope sensor: timeout"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL gyroscope", result.stdout)

    def test_failed_command_after_data_does_not_pass(self):
        result = self.probe(
            "Gyroscope sensor measurement: X=0.000000 Y=0.000000 Z=0.000000 m/s", 124
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL gyroscope", result.stdout)

    def test_other_sensor_data_does_not_pass_as_gyroscope(self):
        result = self.probe(
            "Accelerometer sensor measurement: X=0.000000 Y=0.000000 Z=0.000000 m/s"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL gyroscope", result.stdout)


if __name__ == "__main__":
    unittest.main()
