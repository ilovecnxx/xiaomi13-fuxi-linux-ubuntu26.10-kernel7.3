"""Exercise bounded sensor lifecycle without contacting SSC hardware."""
import importlib.machinery
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


PATH = Path(__file__).resolve().parents[2] / "packaging/fuxi-device/root/usr/libexec/fuxi/fuxi-gravity-sample"
LOADER = importlib.machinery.SourceFileLoader("fuxi_gravity_sample", str(PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
TOOL = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(TOOL)


class GravitySampleTest(unittest.TestCase):
    def setUp(self):
        self.sensor = Mock()
        self.sensor.init_finish.return_value = True
        self.sensor.open_finish.return_value = True
        self.sensor.close_finish.return_value = True
        self.sensor.get_property.return_value = "gravity"
        self.loop = SimpleNamespace(quit=Mock())
        self.timers = []
        self.cancel = Mock()
        self.session = TOOL.GravitySession(
            self.sensor, self.loop, lambda seconds, callback: self.timers.append((seconds, callback)),
            self.cancel, Mock(), 3)
        self.session.start()

    def initialize(self):
        self.session.initialized(self.sensor, None, None)

    def open(self):
        self.initialize()
        self.session.opened(self.sensor, None, None)

    def finish(self):
        self.session.closed(self.sensor, None, None)

    def test_sample_success_requires_measurements_and_close_ack(self):
        self.open()
        self.session.measured(self.sensor, 0, 0, 9.8)
        self.session.measured(self.sensor, 0, 9.7, 0)
        self.session.stop()
        self.sensor.close.assert_called_once()
        self.finish()
        report = self.session.report
        self.assertEqual(report["status"], "sampled")
        self.assertTrue(report["closed"])
        self.assertEqual(report["samples"], 2)
        self.assertEqual(report["magnitude_min"], 9.7)
        self.assertEqual(report["last_vector"], [0, 9.7, 0])
        self.assertEqual(report["mounting_axes"], "not_verified")

    def test_invalid_measurements_never_count_or_serialize(self):
        self.open()
        for value in (math.nan, math.inf, -math.inf, "bad", 1e7):
            self.session.measured(self.sensor, value, 0, 0)
        self.session.stop()
        self.finish()
        self.assertEqual(self.session.report["status"], "no_measurements")
        self.assertEqual(self.session.report["invalid_samples"], 5)
        self.assertNotIn("last_vector", self.session.report)

    def test_initialization_failure_never_opens_or_closes(self):
        self.sensor.init_finish.side_effect = RuntimeError("private transport log")
        self.initialize()
        self.sensor.open.assert_not_called()
        self.sensor.close.assert_not_called()
        self.assertEqual(self.session.report["status"], "initialization_failed")

    def test_wrong_datatype_never_opens(self):
        self.sensor.get_property.return_value = "accel"
        self.initialize()
        self.sensor.open.assert_not_called()
        self.assertEqual(self.session.report["status"], "initialization_failed")

    def test_init_timeout_cancels_and_late_success_does_not_enable(self):
        self.session.deadline("initializing")
        self.cancel.assert_called_once()
        self.initialize()
        self.sensor.open.assert_not_called()
        self.assertEqual(self.session.report["status"], "timed_out")
        self.loop.quit.assert_called_once()

    def test_stuck_initialization_is_bounded(self):
        self.session.deadline("initializing")
        self.session.cleanup_timeout()
        self.assertEqual(self.session.report["status"], "cleanup_timed_out")
        self.loop.quit.assert_called_once()

    def test_open_timeout_waits_for_enable_before_disable(self):
        self.initialize()
        self.session.deadline("opening")
        self.sensor.close.assert_not_called()
        self.session.opened(self.sensor, None, None)
        self.sensor.close.assert_called_once()
        self.finish()
        self.assertEqual(self.session.report["status"], "timed_out")

    def test_failed_open_still_closes_client(self):
        self.sensor.open_finish.return_value = False
        self.open()
        self.sensor.close.assert_called_once()
        self.finish()
        self.assertEqual(self.session.report["status"], "open_failed")

    def test_signal_during_sample_closes_once_and_ignores_late_samples(self):
        self.open()
        self.session.stop("interrupted")
        self.session.stop("interrupted")
        self.session.measured(self.sensor, 0, 0, 9.8)
        self.sensor.close.assert_called_once()
        self.finish()
        self.assertEqual(self.session.report["status"], "interrupted")
        self.assertEqual(self.session.report["samples"], 0)

    def test_close_failure_cannot_report_success(self):
        self.open()
        self.session.measured(self.sensor, 0, 0, 9.8)
        self.session.stop()
        self.sensor.close_finish.side_effect = RuntimeError("private")
        self.finish()
        self.assertFalse(self.session.report["closed"])
        self.assertEqual(self.session.report["status"], "cleanup_failed")

    def test_close_timeout_cannot_be_overwritten_by_late_ack(self):
        self.open()
        self.session.stop()
        self.session.cleanup_timeout()
        self.finish()
        self.assertEqual(self.session.report["status"], "cleanup_timed_out")
        self.assertFalse(self.session.report["closed"])

    def test_expired_stage_timers_do_not_interrupt_sampling(self):
        self.open()
        self.session.deadline("initializing")
        self.session.deadline("opening")
        self.assertFalse(self.session.stopping)
        self.assertEqual(self.session.stage, "sampling")

    def test_requested_duration_is_limited(self):
        for value in ("1", "3", "10"):
            self.assertEqual(TOOL.duration(value), int(value))
        for value in ("0", "11", "-1"):
            with self.assertRaises(Exception):
                TOOL.duration(value)


if __name__ == "__main__":
    unittest.main()
