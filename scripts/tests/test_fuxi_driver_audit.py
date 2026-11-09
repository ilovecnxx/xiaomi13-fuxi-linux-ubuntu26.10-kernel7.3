"""Check interval units, counter resets and disappearing sensor processes."""
import importlib.machinery
import importlib.util
import copy
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

    def test_wakeup_source_table_is_parsed_by_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "kernel/debug/wakeup_sources"
            path.parent.mkdir(parents=True)
            path.write_text(
                "name active_count event_count wakeup_count expire_count active_since "
                "total_time max_time last_change prevent_suspend_time\n"
                "rpmh 0 12 4 1 0 250 100 230 50\n"
                "qcom usb-pm 1 8 3 0 10 90 25 80 20\n")
            self.assertEqual(AUDIT.wakeup_sources(root), {
                "rpmh": {"active_count": 0, "event_count": 12, "wakeup_count": 4,
                         "expire_count": 1, "active_since": 0, "total_time": 250,
                         "max_time": 100, "last_change": 230, "prevent_suspend_time": 50},
                "qcom usb-pm": {"active_count": 1, "event_count": 8, "wakeup_count": 3,
                                "expire_count": 0, "active_since": 10, "total_time": 90,
                                "max_time": 25, "last_change": 80, "prevent_suspend_time": 20}})

    def wakeup_sample(self, when, sources):
        sample = self.sample(when, [0] * 8, 0, {})
        sample["wakeup_sources"] = sources
        return sample

    def test_wakeup_interval_reports_counter_deltas(self):
        before = self.wakeup_sample(1, {"rpmh": {
            "active_count": 0, "event_count": 12, "wakeup_count": 4,
            "total_time": 250, "prevent_suspend_time": 50}})
        after = self.wakeup_sample(3, {"rpmh": {
            "active_count": 1, "event_count": 18, "wakeup_count": 6,
            "total_time": 550, "prevent_suspend_time": 90}})
        report = AUDIT.interval(before, after, 100)
        self.assertTrue(report["wakeup_source_set_unchanged"])
        self.assertEqual(report["wakeup_sources"], [{
            "name": "rpmh", "active_count": 1, "event_count_delta": 6,
            "wakeup_count_delta": 2, "total_time_ms_delta": 300,
            "prevent_suspend_time_ms_delta": 40}])

    def test_wakeup_counter_resets_are_unknown_and_source_changes_flagged(self):
        before = self.wakeup_sample(1, {
            "stays": {"active_count": 0, "event_count": 8, "wakeup_count": 4,
                      "total_time": 50, "prevent_suspend_time": 20},
            "removed": {"active_count": 0, "event_count": 3, "wakeup_count": 2,
                        "total_time": 10, "prevent_suspend_time": 5}})
        after = self.wakeup_sample(2, {
            "stays": {"active_count": 0, "event_count": 1, "wakeup_count": 5,
                      "total_time": 60, "prevent_suspend_time": 4},
            "added": {"active_count": 1, "event_count": 2, "wakeup_count": 1,
                      "total_time": 4, "prevent_suspend_time": 3}})
        report = AUDIT.interval(before, after, 100)
        self.assertFalse(report["wakeup_source_set_unchanged"])
        self.assertEqual(report["wakeup_sources"], [{
            "name": "stays", "active_count": 0, "event_count_delta": None,
            "wakeup_count_delta": 1, "total_time_ms_delta": 10,
            "prevent_suspend_time_ms_delta": None}])

    def test_unreadable_or_malformed_wakeup_source_data_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(AUDIT.wakeup_sources(root))
            path = root / "kernel/debug/wakeup_sources"
            path.parent.mkdir(parents=True)
            path.write_text("name event_count\nrpmh not-a-number\n")
            self.assertIsNone(AUDIT.wakeup_sources(root))
            path.write_text(
                "name active_count event_count wakeup_count expire_count active_since "
                "total_time max_time last_change prevent_suspend_time\n"
                "rpmh 0 invalid 0 0 0 0 0 0 0\n")
            self.assertIsNone(AUDIT.wakeup_sources(root))

        before = self.wakeup_sample(1, None)
        after = self.wakeup_sample(2, {"rpmh": {"event_count": 1}})
        report = AUDIT.interval(before, after, 100)
        self.assertIsNone(report["wakeup_source_set_unchanged"])
        self.assertEqual(report["wakeup_sources"], [])

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

    def power_conditions(self):
        return {"backlights": {"panel": {"bl_power": 4, "brightness": 164,
                                         "actual_brightness": 0, "max_brightness": 4095}},
                "power_supplies": {"bat": {"type": "Battery", "status": "Charging", "online": None,
                                            "capacity_percent": 75, "current_now_ua": -437000},
                                   "usb": {"type": "USB", "status": None, "online": 1}}}

    def test_power_endpoints_record_raw_units_and_optional_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            battery = root / "class/power_supply/bat"
            battery.mkdir(parents=True)
            for name, value in {"type": "Battery", "status": "Charging", "capacity": "75",
                                "current_now": "-437000", "voltage_now": "3999000",
                                "charge_counter": "2496000", "temp": "217"}.items():
                (battery / name).write_text(value)
            panel = root / "class/backlight/panel"
            panel.mkdir(parents=True)
            (panel / "bl_power").write_text("4")
            report = AUDIT.power_conditions(root)
            supply = report["power_supplies"]["bat"]
            self.assertEqual(supply["current_now_ua"], -437000)
            self.assertEqual(supply["voltage_now_uv"], 3999000)
            self.assertEqual(supply["charge_counter_uah"], 2496000)
            self.assertEqual(supply["temp_tenths_celsius"], 217)
            self.assertIsNone(supply["energy_now_uwh"])
            self.assertEqual(report["backlights"]["panel"]["bl_power"], 4)
            self.assertIsNone(AUDIT.matching_power_endpoints(report, report))

    def test_power_endpoints_allow_charge_and_current_to_change(self):
        before = self.service_sample(1)
        after = self.service_sample(2)
        before["power_conditions"] = self.power_conditions()
        after["power_conditions"] = self.power_conditions()
        after["power_conditions"]["power_supplies"]["bat"].update(
            capacity_percent=76, current_now_ua=-500000)
        report = AUDIT.interval(before, after, 100)
        self.assertTrue(report["power_condition_endpoints_match"])
        self.assertEqual(report["power_conditions"]["before"], before["power_conditions"])
        self.assertEqual(report["power_conditions"]["after"], after["power_conditions"])

    def test_screen_or_supply_changes_are_flagged(self):
        before = self.power_conditions()
        for category, name, field, value in (
                ("backlights", "panel", "bl_power", 0),
                ("backlights", "panel", "brightness", 1000),
                ("backlights", "panel", "max_brightness", 2047),
                ("power_supplies", "usb", "online", 0),
                ("power_supplies", "bat", "status", "Discharging")):
            with self.subTest(field=field):
                after = copy.deepcopy(before)
                after[category][name][field] = value
                self.assertFalse(AUDIT.matching_power_endpoints(before, after))
        after = copy.deepcopy(before)
        del after["power_supplies"]["usb"]
        self.assertFalse(AUDIT.matching_power_endpoints(before, after))

    def test_unknown_power_conditions_never_report_matching_endpoints(self):
        self.assertIsNone(AUDIT.matching_power_endpoints(None, None))
        self.assertIsNone(AUDIT.matching_power_endpoints(
            {"backlights": {}, "power_supplies": {}}, self.power_conditions()))
        for category, name, field in (("backlights", "panel", "brightness"),
                                      ("power_supplies", "bat", "type"),
                                      ("power_supplies", "bat", "status"),
                                      ("power_supplies", "usb", "online")):
            with self.subTest(field=field):
                after = self.power_conditions()
                after[category][name][field] = None
                self.assertIsNone(AUDIT.matching_power_endpoints(self.power_conditions(), after))


if __name__ == "__main__":
    unittest.main()
