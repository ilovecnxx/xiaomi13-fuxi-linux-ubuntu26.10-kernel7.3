"""Check interval units, counter resets and disappearing sensor processes."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest


PATH = Path(__file__).resolve().parents[2] / "packaging/fuxi-device/root/usr/libexec/fuxi/fuxi-driver-audit"
LOADER = importlib.machinery.SourceFileLoader("fuxi_driver_audit", str(PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
AUDIT = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(AUDIT)


class DriverAuditTest(unittest.TestCase):
    def sample(self, monotonic, ticks, idle, sensors):
        return {"monotonic": monotonic, "cpu_ticks": ticks,
                "idle": {"cpu0/cpuidle/state1": {"name": "sleep", "disabled": 0, "time_us": idle}},
                "sensors": sensors}

    def test_interval_percentages_use_correct_units(self):
        before = self.sample(10, [10, 0, 10, 70, 10, 0, 0, 0], 1000000,
                             {"5": {"name": "iio-sensor-proxy", "ticks": 2}})
        after = self.sample(12, [20, 0, 20, 140, 20, 0, 0, 0], 2500000,
                            {"5": {"name": "iio-sensor-proxy", "ticks": 3}})
        report = AUDIT.interval(before, after, 100)
        self.assertEqual(report["cpu_busy_percent_all_cores"], 20)
        self.assertEqual(report["idle"]["cpu0/cpuidle/state1"]["residency_percent"], 75)
        self.assertEqual(report["sensor_processes"][0]["cpu_percent_one_core"], 0.5)

    def test_reset_idle_and_exited_process_are_omitted(self):
        before = self.sample(1, [0] * 8, 500, {"5": {"name": "iio-sensor-proxy", "ticks": 2}})
        after = self.sample(2, [0] * 8, 100, {})
        report = AUDIT.interval(before, after, 100)
        self.assertEqual(report["idle"], {})
        self.assertEqual(report["sensor_processes"], [])
        self.assertIsNone(report["cpu_busy_percent_all_cores"])

    def test_truncated_proc_comm_is_recognized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            process = proc / "5"
            process.mkdir(parents=True)
            (proc / "stat").write_text("cpu 1 0 1 8 0 0 0 0 9 0\n")
            (process / "comm").write_text("iio-sensor-prox\n")
            fields = ["S"] + ["0"] * 20
            fields[11], fields[12], fields[19] = "7", "3", "42"
            (process / "stat").write_text("5 (iio-sensor-prox) " + " ".join(fields))
            result = AUDIT.snapshot(root / "sys", proc)
            self.assertEqual(result["sensors"]["5"],
                             {"name": "iio-sensor-proxy", "ticks": 10, "started": 42})
            self.assertEqual(sum(result["cpu_ticks"]), 10)

    def test_reused_pid_is_not_reported_as_the_same_process(self):
        before = self.sample(1, [0] * 8, 0,
                             {"5": {"name": "iio-sensor-proxy", "ticks": 2, "started": 1}})
        after = self.sample(2, [0] * 8, 0,
                            {"5": {"name": "iio-sensor-proxy", "ticks": 5, "started": 2}})
        self.assertEqual(AUDIT.interval(before, after, 100)["sensor_processes"], [])

    def test_service_uses_cgroup_identity_without_reading_command_line(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            process = proc / "5"
            process.mkdir(parents=True)
            (proc / "stat").write_text("cpu 1 0 1 8 0 0 0 0\n")
            (process / "comm").write_text("python3\n")
            (process / "cmdline").write_text("private application data")
            (process / "cgroup").write_text("0::/system.slice/fuxi-sub2-msg.service\n")
            fields = ["S"] + ["0"] * 20
            fields[11], fields[12], fields[19] = "7", "3", "42"
            (process / "stat").write_text("5 (python3) " + " ".join(fields))
            (process / "status").write_text("voluntary_ctxt_switches:\t100\nnonvoluntary_ctxt_switches:\t2\n")
            result = AUDIT.snapshot(root / "sys", proc)
            self.assertEqual(result["services"]["5"], {
                "name": "fuxi-sub2-msg.service", "ticks": 10, "started": 42,
                "voluntary_ctxt_switches": 100, "nonvoluntary_ctxt_switches": 2})
            self.assertNotIn("private", str(result))
            (process / "cgroup").write_text("0::/system.slice/other-fuxi-sub2-msg.service\n")
            self.assertEqual(AUDIT.snapshot(root / "sys", proc)["services"], {})

    def service_sample(self, when, started=42, ticks=10, voluntary=10, involuntary=2):
        sample = self.sample(when, [0] * 8, 0, {})
        sample["services"] = {"5": {
            "name": "fuxi-sub2-msg.service", "started": started, "ticks": ticks,
            "voluntary_ctxt_switches": voluntary, "nonvoluntary_ctxt_switches": involuntary}}
        return sample

    def test_service_interval_units_and_stable_identity(self):
        before = self.service_sample(1)
        after = self.service_sample(3, ticks=11, voluntary=14, involuntary=3)
        report = AUDIT.interval(before, after, 100)
        self.assertTrue(report["service_process_set_unchanged"])
        self.assertEqual(report["service_processes"], [{
            "service": "fuxi-sub2-msg.service", "pid": 5, "cpu_percent_one_core": .5,
            "main_task_voluntary_switches": 4, "main_task_voluntary_switches_per_second": 2,
            "main_task_involuntary_switches": 1, "main_task_involuntary_switches_per_second": .5}])

    def test_service_restarts_are_flagged_and_not_compared(self):
        before = self.service_sample(1)
        for services in ({}, self.service_sample(2, started=43)["services"]):
            after = self.service_sample(2)
            after["services"] = services
            report = AUDIT.interval(before, after, 100)
            self.assertFalse(report["service_process_set_unchanged"])
            self.assertEqual(report["service_processes"], [])

    def test_missing_or_reset_context_counters_are_unknown(self):
        before = self.service_sample(1)
        after = self.service_sample(2, voluntary=None, involuntary=1)
        measured = AUDIT.interval(before, after, 100)["service_processes"][0]
        self.assertIsNone(measured["main_task_voluntary_switches"])
        self.assertIsNone(measured["main_task_involuntary_switches_per_second"])

    def test_invalid_interval_is_rejected(self):
        before = self.service_sample(1)
        with self.assertRaises(ValueError):
            AUDIT.interval(before, before, 100)
        with self.assertRaises(ValueError):
            AUDIT.interval(before, self.service_sample(2), 0)


if __name__ == "__main__":
    unittest.main()
