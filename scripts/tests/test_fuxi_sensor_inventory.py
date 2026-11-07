"""Check bounded metadata-only discovery and conservative failure reporting."""
import contextlib
import errno
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[2] / "packaging/fuxi-device/root/usr/libexec/fuxi/fuxi-sensor-inventory"
LOADER = importlib.machinery.SourceFileLoader("fuxi_sensor_inventory", str(PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
AUDIT = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(AUDIT)


class SensorInventoryTest(unittest.TestCase):
    def worker(self, properties=None, failure=None, deadline=False):
        loop = SimpleNamespace(quit=lambda: None)
        sensor = SimpleNamespace(get_property=lambda key: properties[key])
        observed = []
        callbacks = {}

        class QueryError(Exception):
            domain = "ssc-sensor"
            code = 2

        def new(data_type, cancellable, ready, data):
            observed.append((data_type, cancellable))
            callbacks["ready"] = lambda: ready(None, None, data)

        def finish(_result):
            if failure == "type":
                raise TypeError("constructor returned NULL")
            if failure == "query":
                raise QueryError("private error text")
            return None if failure == "null" else sensor

        def timeout(_seconds, callback):
            callbacks["deadline"] = callback

        loop.run = lambda: callbacks["deadline" if deadline else "ready"]()
        cancelled = []
        repository = ModuleType("gi.repository")
        repository.Gio = SimpleNamespace(Cancellable=lambda: SimpleNamespace(cancel=lambda: cancelled.append(True)))
        repository.GLib = SimpleNamespace(MainLoop=lambda: loop, timeout_add_seconds=timeout, Error=QueryError)
        repository.SSC = SimpleNamespace(Sensor=SimpleNamespace(new=new, new_finish=finish))
        gi = ModuleType("gi")
        gi.require_version = lambda *_: None
        output = io.StringIO()
        with patch.dict(AUDIT.sys.modules, {"gi": gi, "gi.repository": repository}), \
                patch.object(AUDIT.sys, "argv", ["-c", "accel"]), contextlib.redirect_stdout(output):
            exec(AUDIT.PROBE_CODE, {})
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0], "accel")
        return json.loads(output.getvalue()), cancelled

    def test_worker_only_queries_string_metadata_without_opening_sensor(self):
        properties = {"name": "bmi26x", "vendor": "BOSCH", "data-type": "accel"}
        report, cancelled = self.worker(properties)
        self.assertEqual(report, {"status": "attributes_reported", "attributes": properties})
        self.assertFalse(cancelled)

    def test_worker_rejects_mismatched_or_incomplete_attributes(self):
        for properties in ({"name": "bmi26x", "vendor": "BOSCH", "data-type": "gyro"},
                           {"name": None, "vendor": "BOSCH", "data-type": "accel"},
                           {"name": "bmi26x", "vendor": "x" * 257, "data-type": "accel"}):
            with self.subTest(properties=properties):
                report, _ = self.worker(properties)
                self.assertEqual(report, {"status": "incomplete_attributes"})

    def test_worker_null_and_library_errors_are_distinct(self):
        for failure, status in (("null", "no_object_returned"), ("type", "constructor_type_error"),
                                ("query", "query_error")):
            report, _ = self.worker(failure=failure)
            self.assertEqual(report["status"], status)
            self.assertNotIn("private", json.dumps(report))

    def test_worker_deadline_cancels_query(self):
        report, cancelled = self.worker(deadline=True)
        self.assertEqual(report, {"status": "timed_out"})
        self.assertEqual(cancelled, [True])

    def query(self, stdout, returncode=0):
        with patch.object(AUDIT.subprocess, "run", return_value=SimpleNamespace(
                stdout=stdout, stderr="private raw transport log", returncode=returncode)) as run:
            report = AUDIT.query("accel")
        self.assertNotIn("private", json.dumps(report))
        return report, run.call_args

    def test_worker_is_bounded_and_uses_fixed_arguments(self):
        report, call = self.query('{"status":"attributes_reported","attributes":{"name":"bmi26x"}}')
        self.assertEqual(report["status"], "attributes_reported")
        self.assertEqual(call.args[0], [AUDIT.sys.executable, "-c", AUDIT.PROBE_CODE, "accel"])
        self.assertEqual(call.kwargs["timeout"], 8)
        self.assertEqual(call.kwargs["env"]["G_MESSAGES_DEBUG"], "")

    def test_timeout_does_not_imply_missing_hardware(self):
        with patch.object(AUDIT.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                "worker", 8, output="private raw transport log")):
            self.assertEqual(AUDIT.query("gyro"), {"status": "timed_out"})

    def test_launch_failure_reports_errno_without_raw_text(self):
        with patch.object(AUDIT.subprocess, "run", side_effect=OSError(errno.EACCES, "private")):
            self.assertEqual(AUDIT.query("gyro"), {"status": "launch_failed", "errno": errno.EACCES})

    def test_crashed_worker_does_not_pass_from_partial_output(self):
        report, _ = self.query('{"status":"attributes_reported"}', -11)
        self.assertEqual(report, {"status": "worker_failed", "returncode": -11})

    def test_invalid_json_shape_status_and_size_are_rejected(self):
        for text in ("", "not json", "null", "[]", "{}", '{"status":"fully_supported"}', " " * 4097):
            with self.subTest(text=text[:40]):
                report, _ = self.query(text)
                self.assertEqual(report, {"status": "invalid_response"})

    def test_null_constructor_remains_inconclusive(self):
        for status in ("constructor_type_error", "no_object_returned", "incomplete_attributes", "query_error"):
            with self.subTest(status=status):
                report, _ = self.query(json.dumps({"status": status}))
                self.assertEqual(report, {"status": status})

    def test_missing_dependency_stops_remaining_probes(self):
        with patch.object(AUDIT, "query", return_value={"status": "dependency_unavailable"}) as query:
            report = AUDIT.collect()
        self.assertEqual(query.call_count, 1)
        self.assertEqual(list(report["results"]), [AUDIT.DATA_TYPES[0]])
        self.assertEqual(report["requested_data_types"], list(AUDIT.DATA_TYPES))

    def test_metadata_never_becomes_measurement_or_full_inventory_validation(self):
        with patch.object(AUDIT, "query", return_value={"status": "attributes_reported"}) as query:
            report = AUDIT.collect()
        self.assertEqual(query.call_count, len(AUDIT.DATA_TYPES))
        self.assertFalse(report["measurement_streams_requested"])
        self.assertFalse(report["complete_hardware_inventory"])
        self.assertEqual(report["measurement_validation"], "not_tested")
        self.assertEqual(report["physical_units"], "not_verified")
        self.assertEqual(report["mounting_axes"], "not_verified")


if __name__ == "__main__":
    unittest.main()
