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


if __name__ == "__main__":
    unittest.main()
