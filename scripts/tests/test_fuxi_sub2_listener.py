import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
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

    def test_older_glib_uses_legacy_signal_registration(self):
        glib, unix = self.backend()
        glib.unix_signal_add.side_effect = unix.signal_add.side_effect
        glib.MainLoop.return_value.run.side_effect = lambda: self.fire(self.signals, signal.SIGTERM)
        self.assertEqual(listener.listen(Mock(), 0, glib, SimpleNamespace()), 0)
        self.assertEqual(glib.unix_signal_add.call_count, 2)
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
from types import SimpleNamespace
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
unix = GLibUnix if sys.argv[2] == 'native' else SimpleNamespace()
sys.exit(module.listen(lambda: None, 0, GLib, unix))
"""
        for backend in ("native", "legacy"):
            with self.subTest(backend=backend):
                result = subprocess.run([sys.executable, "-c", program, str(SOURCE), backend],
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
            syncs = []
            real_fsync = os.fsync

            def sync(fd):
                self.assertEqual(json.loads(path.read_text())["pdu"], "aabb")
                real_fsync(fd)
                syncs.append(fd)

            def ack(*args):
                self.assertEqual(len(syncs), 2)
                self.assertEqual(json.loads(path.read_text())["pdu"], "aabb")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

            client.send_ack.side_effect = ack
            with patch.object(listener, "SMS_FILE", str(path)), \
                    patch.object(listener.os, "fsync", side_effect=sync), \
                    patch.object(listener, "Qmi", qmi, create=True), \
                    patch.object(listener, "WMS_CLIENT", client), patch.object(listener, "log"):
                listener.handle_transfer_route(payload)
            client.send_ack.assert_called_once()

    def test_save_failure_never_acks(self):
        client = Mock()
        payload = bytes([0]) + (42).to_bytes(4, "little") + bytes([6, 2, 0, 0xAA, 0xBB])
        with patch.object(listener.os, "open", side_effect=OSError("disk full")) as open_file, \
                patch.object(listener, "WMS_CLIENT", client), patch.object(listener, "log"):
            listener.handle_transfer_route(payload)
        open_file.assert_called_once()
        client.send_ack.assert_not_called()


    def test_file_or_directory_sync_failure_never_acks(self):
        payload = bytes([0]) + (42).to_bytes(4, "little") + bytes([6, 2, 0, 0xAA, 0xBB])
        for failure_index in (0, 1):
            with self.subTest(failure_index=failure_index), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "sms.jsonl"
                client = Mock()
                effects = [None] * failure_index + [OSError("sync failed")]
                with patch.object(listener, "SMS_FILE", str(path)), \
                        patch.object(listener.os, "fsync", side_effect=effects), \
                        patch.object(listener, "WMS_CLIENT", client), patch.object(listener, "log"):
                    listener.handle_transfer_route(payload)
                client.send_ack.assert_not_called()
                self.assertEqual(json.loads(path.read_text())["pdu"], "aabb")

    def test_invalid_payload_never_opens_storage_or_acks(self):
        valid = bytes([0]) + (42).to_bytes(4, "little") + bytes([6, 2, 0, 0xAA, 0xBB])
        for payload in ([valid[:size] for size in range(len(valid))] +
                        [valid + b"\x00", bytes([2]) + valid[1:]]):
            with self.subTest(size=len(payload)), patch.object(listener.os, "open") as open_file, \
                    patch.object(listener, "WMS_CLIENT", Mock()) as client, patch.object(listener, "log"):
                listener.handle_transfer_route(payload)
                open_file.assert_not_called()
                client.send_ack.assert_not_called()

    def test_no_ack_requested_still_saves_and_syncs(self):
        payload = bytes([1]) + (42).to_bytes(4, "little") + bytes([6, 2, 0, 0xAA, 0xBB])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sms.jsonl"
            client = Mock()
            with patch.object(listener, "SMS_FILE", str(path)), \
                    patch.object(listener.os, "fsync", wraps=os.fsync) as sync, \
                    patch.object(listener, "WMS_CLIENT", client), patch.object(listener, "log"):
                listener.handle_transfer_route(payload)
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(json.loads(path.read_text())["pdu"], "aabb")
            client.send_ack.assert_not_called()

    def test_existing_log_is_appended_without_losing_prior_message(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sms.jsonl"
            with patch.object(listener, "SMS_FILE", str(path)), patch.object(listener, "log"):
                for txn, pdu in ((42, b"\xaa\xbb"), (43, b"\xcc\xdd")):
                    listener.handle_transfer_route(bytes([1]) + txn.to_bytes(4, "little") +
                                                   bytes([6, len(pdu), 0]) + pdu)
            messages = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([m["txn"] for m in messages], [42, 43])
            self.assertEqual([m["pdu"] for m in messages], ["aabb", "ccdd"])

    def test_symlink_log_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("unchanged")
            path = Path(directory) / "sms.jsonl"
            path.symlink_to(target)
            payload = bytes([0]) + (42).to_bytes(4, "little") + bytes([6, 2, 0, 0xAA, 0xBB])
            client = Mock()
            with patch.object(listener, "SMS_FILE", str(path)), \
                    patch.object(listener, "WMS_CLIENT", client), patch.object(listener, "log"):
                listener.handle_transfer_route(payload)
            self.assertEqual(target.read_text(), "unchanged")
            client.send_ack.assert_not_called()


class IndicationFrameTest(unittest.TestCase):
    @staticmethod
    def frame(body, service=5, message=1):
        header = b"\x80" + bytes([service, 7, 4, 0, 0]) + message.to_bytes(2, "little")
        content = header + len(body).to_bytes(2, "little") + body
        return b"\x01" + (len(content) + 2).to_bytes(2, "little") + content

    def test_complete_frame_preserves_services_and_unknown_tlvs(self):
        for service in (5, 9):
            with self.subTest(service=service):
                frame = self.frame(b"\x10\x02\x00\xaa\xbb\x77\x00\x00", service)
                self.assertEqual(listener.parse_frame(frame),
                                 (service, 7, 0, 1, {0x10: b"\xaa\xbb", 0x77: b""}))

    def test_truncated_or_extra_frame_bytes_are_rejected(self):
        frame = self.frame(b"\x10\x02\x00\xaa\xbb")
        for value in [frame[:size] for size in range(len(frame))] + [frame + b"\x00"]:
            with self.subTest(size=len(value)):
                self.assertIsNone(listener.parse_frame(value))

    def test_invalid_tlv_boundaries_or_duplicates_are_rejected(self):
        for body in (b"\x10", b"\x10\x00", b"\x10\x02\x00\xaa",
                     b"\x10\x00\x00\x10\x00\x00"):
            with self.subTest(body=body):
                self.assertIsNone(listener.parse_frame(self.frame(body)))

    def test_wrong_qmi_length_is_rejected_even_with_valid_qmux_length(self):
        frame = bytearray(self.frame(b"\x10\x00\x00"))
        frame[11:13] = bytes([4, 0])
        self.assertIsNone(listener.parse_frame(frame))

    def test_empty_voice_and_storage_fields_do_not_raise(self):
        self.assertEqual(listener.describe(9, 0x2E, {1: b""}), "")
        self.assertEqual(listener.describe(5, 1, {0x10: b""}), "")

    def test_malformed_indication_never_reaches_sms_handler(self):
        frame = self.frame(b"\x11\x0a\x00\x00")
        with patch.object(listener, "handle_transfer_route") as handler, \
                patch.object(listener, "log"):
            listener.on_indication(None, frame)
        handler.assert_not_called()

    def test_complete_indication_passes_exact_payload_to_sms_handler(self):
        payload = bytes([0]) + (42).to_bytes(4, "little") + bytes([6, 2, 0, 0xAA, 0xBB])
        frame = self.frame(b"\x11" + len(payload).to_bytes(2, "little") + payload)
        with patch.object(listener, "handle_transfer_route") as handler, \
                patch.object(listener, "log") as log:
            listener.on_indication(None, frame)
        handler.assert_called_once_with(payload)
        self.assertNotIn("aabb", str(log.call_args_list))


if __name__ == "__main__":
    unittest.main()
