"""Check GNSS evidence boundaries and keep diagnostics free of position data."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[2] / "packaging/fuxi-device/root/usr/libexec/fuxi/fuxi-gnss-audit"
LOADER = importlib.machinery.SourceFileLoader("fuxi_gnss_audit", str(PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
AUDIT = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(AUDIT)


class GnssAuditTest(unittest.TestCase):
    def collect(self, mm=None, loc="16 2 0 0 131 Location service", qmi_status="ok", mm_status="ok"):
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[0] == "qrtr-lookup":
                return {"status": "ok"}, loc if argv[1] == "16" else "Service Version Instance Node Port"
            if argv[0] == "mmcli":
                return {"status": mm_status}, mm or json.dumps({"modem": {"location": {
                    "capabilities": ["3gpp-lac-ci"], "enabled": ["3gpp-lac-ci"]}}})
            return {"status": qmi_status}, "private or arbitrary output must not be stored"

        with tempfile.TemporaryDirectory() as directory:
            report = AUDIT.collect(run, Path(directory))
        return report, calls

    def test_loc_readability_does_not_claim_satellite_fix(self):
        report, calls = self.collect()
        self.assertTrue(report["qmi_loc_readable"])
        self.assertFalse(report["modemmanager_gps_capability"])
        self.assertEqual(report["satellite_fix"], "not_tested")
        self.assertIn("loc_readable_but_modemmanager_gps_not_exposed", report["findings"])
        self.assertNotIn("private", json.dumps(report))
        qmi = [call for call in calls if call[0] == "qmicli"]
        self.assertEqual([call[-1] for call in qmi], ["--loc-get-engine-lock", "--loc-get-operation-mode"])
        self.assertTrue(all(call[1:4] == ["-d", "qrtr://0", "--device-open-proxy"] for call in qmi))

    def test_missing_or_ambiguous_service_never_probes_arbitrary_node(self):
        for rows in ("Service Version Instance Node Port", "16 2 0 0 131\n16 2 0 1 132"):
            report, calls = self.collect(loc=rows)
            self.assertIsNone(report["qmi_loc_readable"])
            self.assertFalse(any(call[0] == "qmicli" for call in calls))
            self.assertTrue(report["findings"])

    def test_mm_failure_or_malformed_data_is_unknown_not_unsupported(self):
        for kwargs in ({"mm_status": "timed_out"}, {"mm": "invalid json"},
                       {"mm": "[]"}, {"mm": '{"modem":{"location":{"capabilities":"gps-raw"}}}'}):
            report, _ = self.collect(**kwargs)
            self.assertIsNone(report["modemmanager_gps_capability"])
            self.assertIn("modemmanager_capability_unknown", report["findings"])
            self.assertNotIn("loc_readable_but_modemmanager_gps_not_exposed", report["findings"])

    def test_gps_capability_does_not_imply_fix(self):
        report, _ = self.collect(mm=json.dumps({"modem": {"location": {
            "capabilities": ["3gpp-lac-ci", "gps-nmea"], "enabled": ["3gpp-lac-ci"]}}}))
        self.assertTrue(report["modemmanager_gps_capability"])
        self.assertEqual(report["satellite_fix"], "not_tested")
        self.assertEqual(report["findings"], [])

    def test_missing_discovery_does_not_mean_hardware_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            report = AUDIT.collect(lambda argv: ({"status": "unavailable"}, ""), Path(directory))
        self.assertIsNone(report["probes"]["qrtr_loc"]["nodes"])
        self.assertIsNone(report["qmi_loc_readable"])
        self.assertIn("qrtr_loc_discovery_unknown", report["findings"])
        self.assertNotIn("qrtr_loc_not_advertised", report["findings"])

    def test_qmi_timeout_is_recorded_as_read_failure(self):
        report, _ = self.collect(qmi_status="timed_out")
        self.assertFalse(report["qmi_loc_readable"])
        self.assertIn("loc_read_failed", report["findings"])

    def test_qrtr_parser_ignores_headers_other_services_and_duplicates(self):
        self.assertEqual(AUDIT.qrtr_nodes(
            "Service Version Instance Node Port\n16 2 0 0 131 Location\n16 2 1 0 132\n"
            "6 1 0 1 100\n16 invalid 0 2 100\n16 2 0 -1 10", 16), [0])

    def test_command_failure_and_time_limit_are_bounded(self):
        for error, expected in ((FileNotFoundError(), "unavailable"),
                                (subprocess.TimeoutExpired("qmicli", 8), "timed_out"),
                                (PermissionError(), "failed")):
            with patch.object(AUDIT.subprocess, "run", side_effect=error) as run:
                result, output = AUDIT.command(["qmicli"])
                self.assertEqual(result["status"], expected)
                self.assertEqual(output, "")
                self.assertEqual(run.call_args.kwargs["timeout"], 8)


if __name__ == "__main__":
    unittest.main()
