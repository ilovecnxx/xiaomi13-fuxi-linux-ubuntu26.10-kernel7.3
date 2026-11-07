import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


SOURCE = (Path(__file__).resolve().parents[2] /
          "packaging/fuxi-modem/root/usr/sbin/fuxi-qmi-sub2-msg.py")
spec = importlib.util.spec_from_file_location("sub2_listener", SOURCE)
listener = importlib.util.module_from_spec(spec)
spec.loader.exec_module(listener)


class ListenerTest(unittest.TestCase):
    def backend(self):
        glib, unix = Mock(), Mock()
        glib.PRIORITY_DEFAULT = 0
        glib.MainLoop.return_value = Mock()
        self.callbacks = {}
        self.signals = {}
        self.active = set()

        def register(table, key, callback):
            source = len(self.active) + 1
            self.active.add(source)
            table[key] = (source, callback)
            return source

        glib.timeout_add_seconds.side_effect = lambda seconds, cb: register(self.callbacks, seconds, cb)
        unix.signal_add.side_effect = lambda priority, sig, cb: register(self.signals, sig, cb)
        glib.MainContext.default.return_value.find_source_by_id.side_effect = (
            lambda source: source if source in self.active else None)
        glib.source_remove.side_effect = self.active.remove
        return glib, unix

    def fire(self, table, key):
        source, callback = table[key]
        if not callback():
            self.active.remove(source)

    def test_watchdog_repeats_then_failure_exits_for_restart(self):
        glib, unix = self.backend()
        watchdog = Mock(side_effect=[None, RuntimeError("disconnected")])

        def events():
            self.fire(self.callbacks, 30)
            glib.MainLoop.return_value.quit.assert_not_called()
            self.fire(self.callbacks, 30)

        glib.MainLoop.return_value.run.side_effect = events
        with patch.object(listener, "log"):
            self.assertEqual(listener.listen(watchdog, 0, glib, unix), 1)
        self.assertEqual(watchdog.call_count, 2)
        glib.MainLoop.return_value.quit.assert_called_once()
        self.assertFalse(self.active)

    def test_signals_stop_cleanly_and_remove_remaining_sources(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig=sig):
                glib, unix = self.backend()
                glib.MainLoop.return_value.run.side_effect = lambda: self.fire(self.signals, sig)
                watchdog = Mock()
                self.assertEqual(listener.listen(watchdog, 0, glib, unix), 0)
                watchdog.assert_not_called()
                glib.MainLoop.return_value.quit.assert_called_once()
                self.assertFalse(self.active)

    def test_duration_stops_without_removing_expired_source_twice(self):
        glib, unix = self.backend()
        glib.MainLoop.return_value.run.side_effect = lambda: self.fire(self.callbacks, 2)
        self.assertEqual(listener.listen(Mock(), 2, glib, unix), 0)
        self.assertFalse(self.active)

    def test_exception_removes_sources(self):
        glib, unix = self.backend()
        glib.MainLoop.return_value.run.side_effect = RuntimeError("dispatch failed")
        with self.assertRaisesRegex(RuntimeError, "dispatch failed"):
            listener.listen(Mock(), 0, glib, unix)
        self.assertFalse(self.active)

    def test_negative_duration_fails_before_loading_device_libraries(self):
        with patch.object(sys, "argv", [str(SOURCE), "-1"]):
            with self.assertRaisesRegex(SystemExit, "nonnegative"):
                listener.main()

    def test_real_glib_dispatch_and_sigterm_without_qmi(self):
        try:
            import gi
            gi.require_version("GLibUnix", "2.0")
            from gi.repository import GLibUnix  # noqa: F401
        except (ImportError, ValueError):
            self.skipTest("GLibUnix typelib unavailable on host")
        program = """
import importlib.util, os, signal, sys
import gi
gi.require_version('GLibUnix', '2.0')
from gi.repository import GLib, GLibUnix
spec = importlib.util.spec_from_file_location('listener', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def event():
    print('event dispatched', flush=True)
    os.kill(os.getpid(), signal.SIGTERM)
    return False
GLib.idle_add(event)
sys.exit(module.listen(lambda: None, 0, GLib, GLibUnix))
"""
        result = subprocess.run([sys.executable, "-c", program, str(SOURCE)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("event dispatched", result.stdout)
        self.assertNotIn("CRITICAL", result.stderr)


class SmsPersistenceTest(unittest.TestCase):
    def test_pdu_is_saved_before_ack(self):
        qmi, client = Mock(), Mock()
        payload = bytes([0]) + (42).to_bytes(4, "little") + bytes([6, 2, 0, 0xAA, 0xBB])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sms.jsonl"

            def ack(*args):
                self.assertEqual(json.loads(path.read_text())["pdu"], "aabb")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

            client.send_ack.side_effect = ack
            with patch.object(listener, "SMS_FILE", str(path)), \
                    patch.object(listener, "Qmi", qmi, create=True), \
                    patch.object(listener, "WMS_CLIENT", client), patch.object(listener, "log"):
                listener.handle_transfer_route(payload)
            client.send_ack.assert_called_once()

    def test_save_failure_never_acks(self):
        client = Mock()
        payload = bytes([0]) + (42).to_bytes(4, "little") + bytes([6, 0, 0])
        with patch.object(listener.os, "open", side_effect=OSError("disk full")), \
                patch.object(listener, "WMS_CLIENT", client), patch.object(listener, "log"):
            listener.handle_transfer_route(payload)
        client.send_ack.assert_not_called()


if __name__ == "__main__":
    unittest.main()
