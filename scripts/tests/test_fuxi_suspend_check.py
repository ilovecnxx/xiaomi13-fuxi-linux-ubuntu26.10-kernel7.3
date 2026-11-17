"""Guard the RTC until logind reports an actual completed sleep cycle."""

import importlib.util
import errno
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "fuxi_suspend_check", Path(__file__).resolve().parents[1] / "fuxi-suspend-check.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class SuspendCheckTest(unittest.TestCase):
    def test_platform_stats_skip_active_ddr_interface_and_record_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stats = root / "stats"
            stats.mkdir()
            (stats / "aosd").write_text("Count: 0\n")
            (stats / "cxsd").write_text("Count: 3\n")
            original_read = Path.read_text

            def read_stat(path, *args, **kwargs):
                self.assertNotEqual(path.name, "ddr_stats")
                if path.name == "modem":
                    raise PermissionError(errno.EACCES, "unavailable")
                return original_read(path, *args, **kwargs)

            with mock.patch.object(CHECK, "PLATFORM_STATS", stats), \
                    mock.patch.object(Path, "read_text", read_stat):
                CHECK.snapshot_platform_stats(root, "before")
            report = json.loads((root / "before-platform-sleep.json").read_text())
            self.assertEqual(report["stats"], {"aosd": "Count: 0\n", "cxsd": "Count: 3\n"})
            self.assertEqual(report["errors"]["modem"], {"errno": errno.EACCES})
            self.assertEqual(report["errors"]["adsp"], {"errno": errno.ENOENT})
            self.assertGreaterEqual(report["boottime_end"], report["boottime_begin"])

    def test_enqueue_is_not_resume(self):
        cycle = CHECK.SleepCycle()
        self.assertTrue(cycle.may_clear_alarm)
        cycle.requested = True
        self.assertFalse(cycle.may_clear_alarm)
        cycle.observe(True)
        self.assertFalse(cycle.may_clear_alarm)
        cycle.observe(False)
        self.assertTrue(cycle.may_clear_alarm)

    def test_stray_false_signal_does_not_clear_alarm(self):
        cycle = CHECK.SleepCycle()
        cycle.requested = True
        cycle.observe(False)
        self.assertFalse(cycle.resumed)
        self.assertFalse(cycle.may_clear_alarm)

    def test_queued_complete_cycle_is_not_missed(self):
        cycle = CHECK.SleepCycle()
        cycle.requested = True

        def dispatch():
            cycle.observe(True)
            cycle.observe(False)

        CHECK.wait_for_resume(cycle, dispatch, 10, clock=lambda: 0,
                              pause=lambda _: self.fail("Complete cycle should return"))
        self.assertTrue(cycle.may_clear_alarm)

    def test_timeout_during_prepare_keeps_alarm(self):
        cycle = CHECK.SleepCycle()
        cycle.requested = True
        now = [0]

        def pause(_):
            now[0] += 1

        with self.assertRaises(TimeoutError):
            CHECK.wait_for_resume(cycle, lambda: cycle.observe(True), 2,
                                  clock=lambda: now[0], pause=pause)
        self.assertFalse(cycle.may_clear_alarm)

    def test_boottime_deadline_accounts_for_sleep(self):
        cycle = CHECK.SleepCycle()
        cycle.requested = True
        now = [0]

        def dispatch():
            now[0] = 80
            cycle.observe(True)

        with self.assertRaises(TimeoutError):
            CHECK.wait_for_resume(cycle, dispatch, 65, clock=lambda: now[0])
        self.assertFalse(cycle.may_clear_alarm)

    def test_trace_keeps_unanticipated_irqs_and_exports_loss_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out"
            output.mkdir()
            trace = CHECK.WakeTrace(output)
            trace.path = root / "instance"

            def fake_write(name, value):
                target = trace.path / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value + "\n")

            with mock.patch.object(trace, "write", side_effect=fake_write), \
                    mock.patch.object(Path, "rmdir", side_effect=lambda: shutil.rmtree(trace.path)):
                with self.assertRaisesRegex(RuntimeError, "experiment failed"):
                    with trace:
                        (trace.path / "trace").write_text(
                            "irq_handler_entry: irq=8 name=arch_timer\n"
                            "irq_handler_entry: irq=999 name=unanticipated\n")
                        stats = trace.path / "per_cpu/cpu0/stats"
                        stats.parent.mkdir(parents=True)
                        stats.write_text("overrun: 42\n")
                        self.assertFalse((trace.path / "events/irq/irq_handler_entry/filter").exists())
                        self.assertEqual((trace.path / "events/irq/irq_handler_exit/enable").read_text(), "1\n")
                        raise RuntimeError("experiment failed")
            self.assertIn("irq=999", (output / "trace.txt").read_text())
            self.assertEqual((output / "trace-cpu0-stats.txt").read_text(), "overrun: 42\n")
            config = json.loads((output / "trace-config.json").read_text())
            self.assertIn("qcom_smp2p/smp2p_notify_in", config["missing_platform_events"])
            self.assertFalse(trace.path.exists())

    def test_main_cleanup_after_rtc_timeout_request_timeout_and_resume(self):
        for failure in ("rtcwake", "Suspend", None):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                alarm = root / "wakealarm"
                alarm.write_text("")
                signals = []

                def call(_name, _path, _iface, method, *_args):
                    if method == "Get":
                        return SimpleNamespace(unpack=lambda: (False,))
                    if failure == "Suspend":
                        raise TimeoutError("queued request timed out")
                    for sleeping in (True, False):
                        signals[0](None, None, None, None, None,
                                   SimpleNamespace(unpack=lambda: (sleeping,)))

                def subscribe(*args):
                    signals.append(args[-1])
                    return 1

                def rtcwake(*_args):
                    alarm.write_text("123\n")
                    if failure == "rtcwake":
                        raise TimeoutError("armed RTC before timeout")
                    return "armed"

                bus = SimpleNamespace(call_sync=call, signal_subscribe=subscribe,
                                      signal_unsubscribe=mock.Mock(), flush_sync=mock.Mock())
                gio = SimpleNamespace(BusType=SimpleNamespace(SYSTEM=0),
                                      DBusCallFlags=SimpleNamespace(NONE=0),
                                      DBusSignalFlags=SimpleNamespace(NONE=0),
                                      bus_get_sync=lambda *_args: bus)
                glib = SimpleNamespace(Variant=lambda *_args: None,
                                       MainContext=SimpleNamespace(default=lambda: None))
                trace = mock.MagicMock()
                descriptor = os.open(root / "lock", os.O_CREAT | os.O_RDWR, 0o600)
                with mock.patch.dict("sys.modules", {
                    "gi": SimpleNamespace(repository=SimpleNamespace(Gio=gio, GLib=glib)),
                    "gi.repository": SimpleNamespace(Gio=gio, GLib=glib),
                }), mock.patch("sys.argv", ["check", "--output", str(root / "out")]), \
                        mock.patch.object(CHECK, "RTC", root), \
                        mock.patch.object(CHECK, "check_idle"), \
                        mock.patch.object(CHECK, "snapshot"), \
                        mock.patch.object(CHECK, "WakeTrace", return_value=trace), \
                        mock.patch.object(CHECK, "run", side_effect=rtcwake), \
                        mock.patch.object(CHECK.os, "geteuid", return_value=0), \
                        mock.patch.object(CHECK.os, "open", return_value=descriptor), \
                        mock.patch.object(CHECK.os, "umask"), mock.patch("builtins.print"):
                    if failure:
                        with self.assertRaises(TimeoutError):
                            CHECK.main()
                    else:
                        CHECK.main()
                self.assertEqual(alarm.read_text(), "123\n" if failure == "Suspend" else "0\n")


if __name__ == "__main__":
    unittest.main()
