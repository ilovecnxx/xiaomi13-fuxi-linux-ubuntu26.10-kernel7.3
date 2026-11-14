"""Keep baseline inspection read-only and distinguish missing evidence from drift."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("baseline", Path(__file__).resolve().parents[1] / "fuxi-device-baseline.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


class DeviceBaselineTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in {"proc/sys/kernel/osrelease": "test-deb9", "boot/fuxi/default": "deb9",
                            "boot/fuxi/fallback": "deb8", baseline.INSTALLER: "installer fixture"}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        self.digest = hashlib.sha256(b"installer fixture").hexdigest()
        self.calls = []

    def runner(self, argv):
        self.calls.append(argv)
        if argv[0] == "systemctl":
            self.assertEqual(argv[1], "show")
            return {"returncode": 0, "stdout": "LoadState=loaded\nActiveState=active\nSubState=exited\nExecStart=fixture", "stderr": ""}
        self.assertEqual(argv[:2], ["dpkg-query", "-W"])
        return {"returncode": 0, "stdout": "fixture package inventory", "stderr": ""}

    def test_matching_inventory_does_not_run_boot_tools(self):
        report = baseline.collect(self.digest, self.root, self.runner)
        self.assertEqual(report["status"], "match")
        self.assertEqual(report["boot"]["default"]["value"], "deb9")
        self.assertEqual(len(self.calls), 4)

    def test_old_installer_is_drift(self):
        report = baseline.collect("0" * 64, self.root, self.runner)
        self.assertEqual(report["status"], "drift")
        self.assertFalse(report["installer"]["matches_repository"])

    def test_missing_split_services_are_not_waived_by_combined_service(self):
        def runner(argv):
            if argv[0] == "systemctl" and argv[2] != baseline.UNITS[0]:
                return {"returncode": 0, "stdout": "LoadState=not-found\nActiveState=inactive", "stderr": ""}
            return self.runner(argv)
        report = baseline.collect(self.digest, self.root, runner)
        self.assertEqual(report["status"], "drift")
        self.assertEqual(len(report["differences"]), 2)

    def test_unreadable_installer_is_unknown(self):
        (self.root / baseline.INSTALLER).unlink()
        report = baseline.collect(self.digest, self.root, self.runner)
        self.assertEqual(report["status"], "unknown")

    def test_unavailable_systemd_is_unknown(self):
        def runner(argv):
            if argv[0] == "systemctl":
                return {"returncode": 1, "stdout": "", "stderr": "no bus"}
            return self.runner(argv)
        self.assertEqual(baseline.collect(self.digest, self.root, runner)["status"], "unknown")

    def test_missing_boot_selection_is_unknown(self):
        (self.root / "boot/fuxi/default").unlink()
        self.assertEqual(baseline.collect(self.digest, self.root, self.runner)["status"], "unknown")

    def test_inactive_radio_is_drift(self):
        def runner(argv):
            result = self.runner(argv)
            if argv[0] == "systemctl":
                result["stdout"] = result["stdout"].replace("ActiveState=active", "ActiveState=inactive")
            return result
        self.assertEqual(baseline.collect(self.digest, self.root, runner)["status"], "drift")
