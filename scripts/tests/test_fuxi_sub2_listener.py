import importlib.util
import json
import os
from contextlib import ExitStack
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
    def test_ack_reports_modem_result(self):
        class AckError(Exception):
            message = "rejected"

        client = Mock()
        client.send_ack_finish.return_value.get_result.side_effect = AckError()
        with patch.object(listener, "GLib", SimpleNamespace(Error=AckError), create=True), \
                patch.object(listener, "log") as log:
            listener.ack_cb(client, object())
        log.assert_called_once_with("WMS ack failed: rejected")

    def test_binding_must_identify_exact_secondary(self):
        for value in (None, b"", b"\x00", b"\x01\x00", b"\x01"):
            with self.subTest(value=value), patch.object(listener, "raw"), \
                    patch.object(listener, "tlv_bytes", return_value=value):
                if value == b"\x01":
                    listener.check_wms_binding(object(), object())
                else:
                    with self.assertRaisesRegex(RuntimeError, "binding"):
                        listener.check_wms_binding(object(), object())

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
        client = Mock()
        client.get_cid.return_value = 7
        with patch.object(listener, "WMS_CLIENT", client), \
                patch.object(listener, "handle_transfer_route") as handler, \
                patch.object(listener, "log") as log:
            listener.on_indication(None, frame)
            handler.assert_called_once_with(payload)
            handler.reset_mock()
            client.get_cid.return_value = 8
            listener.on_indication(None, frame)
            handler.assert_not_called()
        self.assertNotIn("aabb", str(log.call_args_list))


class VoiceRegistrationTest(unittest.TestCase):
    def setUp(self):
        dist = SOURCE.parents[1] / 'lib/python3/dist-packages'
        self.enterContext(patch.object(sys, 'path', [str(dist), *sys.path]))
        self.enterContext(patch.dict(os.environ, RUNTIME_DIRECTORY='/run/fuxi-sub2-msg',
                                    INVOCATION_ID='a' * 32))
        self.qmi = SimpleNamespace(Service=SimpleNamespace(VOICE=9))
        self.enterContext(patch.object(listener, 'Qmi', self.qmi, create=True))
        self.enterContext(patch.object(listener, 'GLib', Mock(), create=True))
        self.log = self.enterContext(patch.object(listener, 'log'))
        self.bind = self.enterContext(patch('fuxi_qmi_bind.bind_subscription'))
        self.register = self.enterContext(patch('fuxi_sub2_control._register_calls'))
        self.serve = self.enterContext(patch('fuxi_sub2_call_cache.serve'))
        self.device, self.voice = Mock(), Mock()
        self.voice.get_service.return_value = 9
        self.voice.get_device.return_value = self.device
        self.voice.get_cid.return_value = 7
        self.handlers = {}

        def connect(name, handler):
            self.handlers[name] = handler
            return name

        self.device.connect.side_effect = connect
        self.device.disconnect.side_effect = self.handlers.pop
        self.body = b'\x01\x08\x00\x01\x06\x02\x01\x02\x04\x00\x00\x10\x07\x00private'
        self.ring = IndicationFrameTest.frame(self.body, 9, 0x2e)

    def start(self, resources):
        listener.register_voice_listener(resources, self.device, self.voice, Mock())

    def test_discovery_observes_registration_ring_and_discards_unrelated_frames(self):
        def during_registration(*_):
            self.bind.assert_called_once_with(self.qmi, listener.wait, self.device, self.voice, 0x44, 1)
            self.serve.assert_not_called()
            self.handlers['indication'](self.device, self.ring)

        self.register.side_effect = during_registration
        with ExitStack() as resources:
            self.start(resources)
            cache = self.serve.call_args.args[-1]
            on_frame = self.handlers['indication']
            snapshot = cache.snapshot()
            self.assertEqual(snapshot['calls'], [{'id': 6, 'state': 'incoming', 'direction': 'incoming'}])
            on_frame(object(), self.ring)
            for offset, value in ((4, 5), (5, 8), (6, 2), (9, 0x2f)):
                foreign = bytearray(self.ring)
                foreign[offset] = value
                on_frame(self.device, foreign)
            self.assertIs(cache.snapshot(), snapshot)
            self.assertEqual(self.log.call_count, 1)
            self.assertNotIn('private', str(self.log.call_args_list))
            on_frame(self.device, self.ring[:-1])
            self.assertIsNone(cache.snapshot())
            on_frame(self.device, IndicationFrameTest.frame(b'\x01\x01\x00\x00', 9, 0x2e))
            self.assertEqual(cache.snapshot()['calls'], [])
            self.handlers['device-removed'](self.device)
            on_frame(self.device, self.ring)
            self.assertIsNone(cache.snapshot())
        self.assertFalse(self.handlers)

    def test_bind_and_registration_failures_do_not_publish(self):
        for step in (self.bind, self.register):
            with self.subTest(step=step), self.assertRaises(RuntimeError), ExitStack() as resources:
                step.side_effect = RuntimeError('rejected')
                self.start(resources)
            step.side_effect = None
            self.assertFalse(self.handlers)
            self.serve.assert_not_called()

    def test_manual_observer_logs_only_bound_metadata_without_service_endpoint(self):
        with patch.dict(os.environ, RUNTIME_DIRECTORY=''), ExitStack() as resources:
            self.start(resources)
            listener.on_indication(self.device, self.ring)
            self.log.assert_not_called()
            self.handlers['indication'](self.device, self.ring)
            self.log.assert_called_once_with('VOICE calls', [{'id': 6, 'state': 'incoming', 'direction': 'incoming'}])
            self.serve.assert_not_called()


class StoredMtTest(unittest.TestCase):
    tpdu = bytes.fromhex("04 03 91 21 f3 00 08 62 11 22 01 00 00 00 02 4e 2d")

    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / 'sms.jsonl'
        self.enterContext(patch.object(listener, 'SMS_FILE', str(self.path)))
        self.client = Mock(spec=['get_cid', 'raw_read', 'raw_read_finish'])
        self.client.get_cid.return_value = 7
        self.enterContext(patch.object(listener, 'WMS_CLIENT', self.client))
        self.enterContext(patch.object(listener, 'STORED_READS', {}))
        self.log = self.enterContext(patch.object(listener, 'log'))
        self.qmi = SimpleNamespace(MessageWmsRawReadInput=Mock(), WmsStorageType=int,
                                   WmsMessageMode=SimpleNamespace(GSM_WCDMA=1),
                                   WmsMessageTagType=SimpleNamespace(MT_READ=0, MT_NOT_READ=1),
                                   WmsMessageFormat=SimpleNamespace(GSM_WCDMA_POINT_TO_POINT=6))
        self.enterContext(patch.object(listener, 'Qmi', self.qmi, create=True))
        self.enterContext(patch.object(listener, 'GLib', SimpleNamespace(Error=RuntimeError), create=True))

    def indicate(self, storage=1, index=0x12345678, mode=b'', stored=None):
        value = bytes([storage]) + index.to_bytes(4, 'little') if stored is None else stored
        body = b'\x10' + len(value).to_bytes(2, 'little') + value + mode
        listener.on_indication(None, IndicationFrameTest.frame(body))

    def finish(self, data=None, tag=1, fmt=6):
        output = self.client.raw_read_finish.return_value
        output.get_result.return_value = None
        output.get_raw_message_data.return_value = (tag, fmt, list(b'\0' + self.tpdu if data is None else data))
        self.client.raw_read.call_args.args[-1](self.client, object())

    def rows(self):
        return [json.loads(line) for line in self.path.read_bytes().splitlines()]

    def test_stored_framing_and_existing_inbox_reader(self):
        reader = SOURCE.parents[1] / 'lib/python3/dist-packages/fuxi_sub2_inbox.py'
        spec = importlib.util.spec_from_file_location('inbox', reader)
        inbox = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inbox)
        self.indicate(mode=b'\x12\x01\x00\x01')
        request = self.qmi.MessageWmsRawReadInput.return_value
        request.set_message_memory_storage_id.assert_called_once_with(1, 0x12345678)
        request.set_message_mode.assert_called_once_with(1)
        self.finish(b'\x03\x91\x21\xf3' + self.tpdu)
        row, = self.rows()
        self.assertEqual((row['txn'], row['storage'], row['format'], row['pdu']),
                         (0x12345678, 1, 6, self.tpdu.hex()))
        item = inbox.record(0, self.path.read_bytes())
        self.assertEqual((item['sender'], item['text']), ('+123', '中'))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        for private in ('+123', '中', self.tpdu.hex()):
            self.assertNotIn(private, str(self.log.call_args_list))

    def test_duplicate_in_flight_then_restart_and_reuse(self):
        self.indicate()
        self.indicate()
        self.indicate()
        self.client.raw_read.assert_called_once()
        self.finish()
        self.assertEqual(self.client.raw_read.call_count, 2)
        self.finish(tag=0)
        with patch.object(listener, 'STORED_READS', {}):
            self.indicate()
            self.finish()
        self.assertEqual(len(self.rows()), 1)
        other = self.tpdu[:-2] + bytes.fromhex('4e 00')
        self.indicate()
        self.finish(b'\0' + other)
        self.indicate()
        self.finish()
        self.indicate(storage=0)
        self.finish()
        self.assertEqual([row['pdu'] for row in self.rows()], [self.tpdu.hex(), other.hex()] + [self.tpdu.hex()] * 2)

    def test_unsupported_and_foreign_indications_do_not_read(self):
        self.indicate(storage=255)
        self.indicate(stored=b'\0' * 4)
        self.indicate(stored=b'\0' * 6)
        self.indicate(mode=b'\x12\x01\x00\x00')
        self.client.get_cid.return_value = 9
        self.indicate()
        self.client.raw_read.assert_not_called()

    def test_transfer_route_takes_precedence(self):
        with patch.object(listener, 'handle_transfer_route') as transfer:
            self.indicate(mode=b'\x11\x08\x00' + b'\0' * 8)
            transfer.assert_called_once()
        self.client.raw_read.assert_not_called()

    def test_failure_unsupported_format_tag_and_invalid_framing(self):
        for args in (dict(tag=2), dict(fmt=7), dict(fmt=0), dict(data=b''), dict(data=b'\x03\x91'),
                     dict(data=b'\0' + self.tpdu[:-1]), dict(data=b'\0\x02' + self.tpdu[1:])):
            self.indicate()
            self.finish(**args)
        self.indicate()
        self.client.raw_read_finish.side_effect = RuntimeError('private error')
        self.finish()
        self.assertFalse(listener.STORED_READS)
        self.assertFalse(self.path.exists())
        self.assertNotIn('private error', str(self.log.call_args_list))

    def test_replay_syncs_existing_row_after_directory_sync_failure(self):
        self.indicate()
        with patch.object(listener.os, 'fsync', side_effect=[None, OSError('private error')]):
            self.finish()
        self.indicate()
        with patch.object(listener.os, 'fsync', wraps=os.fsync) as sync:
            self.finish()
            self.assertEqual(sync.call_count, 2)
        self.assertEqual(len(self.rows()), 1)
        self.assertNotIn('private error', str(self.log.call_args_list))


if __name__ == "__main__":
    unittest.main()
