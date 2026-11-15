"""Audit regressions use synthetic records, never proprietary register tables."""

import copy
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-imx800-audit.py"
SPEC = importlib.util.spec_from_file_location("imx800_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def record(address, value):
    return {"address": address, "data": [value], "address_width_encoded": 2,
            "data_width_encoded": 1, "operation_encoded": 0,
            "slave_override_encoded": None, "delay_encoded": 0}


def synthetic_profile():
    values = {0x0111: 3, 0x0112: 10, 0x0113: 10, 0x0114: 2,
              0x0136: 0x13, 0x0137: 0x33, 0x0301: 8, 0x0303: 2,
              0x0305: 3, 0x0306: 1, 0x0307: 44, 0x030B: 2,
              0x030D: 4, 0x030E: 3, 0x030F: 232}
    for address, value in {0x0340: 4000, 0x0342: 5000, 0x0344: 0, 0x0346: 0,
                           0x0348: 8191, 0x034A: 6143,
                           0x034C: 4096, 0x034E: 3072}.items():
        values[address], values[address + 1] = divmod(value, 256)
    return {"tables": {"init": [], "mode1": [record(k, v) for k, v in values.items()],
                       "stream_on": [record(0x0101, 3), record(0x0100, 1)]},
            "mode": {"line_length_encoded": 5000, "frame_length_encoded": 4000,
                     "frame_rate_encoded": 96, "streams": [], "settle_time_ns_encoded": 30}}


class Imx800AuditTests(unittest.TestCase):
    def test_candidate_model_uses_pll_and_frame_timing_as_separate_rates(self):
        report = audit.model(synthetic_profile())
        self.assertEqual(report["link_frequency_hz_candidate"], 1200000000)
        self.assertEqual(report["per_trio_bits_per_second_ceil_candidate"], 5485714286)
        self.assertAlmostEqual(report["pixel_rate_csi_hz_candidate"], 1645714285.7142856)
        self.assertEqual(report["pixel_array_timing_rate_hz_candidate"], 1920000000)
        self.assertEqual(report["frame_rate_hz_candidate"], 96)
        self.assertEqual(report["vt_parallel_factor_assumed"], 16)

    def test_zero_divider_and_inconsistent_metadata_are_rejected(self):
        for change in ("zero-divider", "geometry", "timing", "fps"):
            with self.subTest(change=change):
                profile = synthetic_profile()
                if change == "zero-divider":
                    profile["tables"]["mode1"].append(record(0x030D, 0))
                elif change == "geometry":
                    profile["tables"]["mode1"].append(record(0x034C, 0))
                elif change == "timing":
                    profile["mode"]["line_length_encoded"] = 6000
                else:
                    profile["mode"]["frame_rate_encoded"] = 30
                with self.assertRaises(audit.camera.FormatError):
                    audit.model(profile)

    def test_ordered_comparison_rejects_same_final_value_with_extra_write(self):
        left = {"tables": {"mode1": [record(0x0301, 8)]}, "mode": {},
                "power_encoded": {}, "identity_encoded": {}}
        right = copy.deepcopy(left)
        right["tables"]["mode1"].insert(0, record(0x0301, 4))
        self.assertEqual(audit.byte_map(left["tables"]["mode1"]),
                         audit.byte_map(right["tables"]["mode1"]))
        with self.assertRaisesRegex(audit.camera.FormatError, "differ in tables"):
            audit.compare_profiles(left, right)

    def test_comparison_includes_power_identity_and_auxiliary_stream(self):
        base = {"tables": {}, "mode": {"streams": [43, 48]},
                "power_encoded": {"up": [[0, 19200000, 2]]},
                "identity_encoded": {"value": 8}}
        self.assertEqual(audit.compare_profiles(base, copy.deepcopy(base)), base)
        for key in ("mode", "power_encoded", "identity_encoded"):
            with self.subTest(key=key):
                changed = copy.deepcopy(base)
                changed[key] = {}
                with self.assertRaises(audit.camera.FormatError):
                    audit.compare_profiles(base, changed)

    def test_unknown_operations_widths_arrays_and_delays_are_not_interpreted(self):
        mutations = {"operation_encoded": 4, "data_width_encoded": 2,
                     "address_width_encoded": 1, "slave_override_encoded": 32,
                     "delay_encoded": 100, "data": [1, 2], "address": 0x10000}
        for key, value in mutations.items():
            with self.subTest(key=key):
                changed = record(0x0301, 8)
                changed[key] = value
                with self.assertRaises(audit.camera.FormatError):
                    audit.byte_map([changed])
        with self.assertRaises(audit.camera.FormatError):
            audit.byte_map([record(0x0301, 256)])

    def test_missing_pll_register_is_reported_without_guessing(self):
        with self.assertRaisesRegex(audit.camera.FormatError, "missing required register"):
            audit.model({"tables": {"init": [], "mode1": []}})

    def test_cli_rejects_unknown_source_without_partial_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "unknown.bin"
            source.write_bytes(b"synthetic unsupported module")
            result = subprocess.run([sys.executable, str(SCRIPT), "--semco", str(source),
                                     "--sunny", str(source)], capture_output=True, text=True,
                                    check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("SHA256", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
