from contextlib import ExitStack, contextmanager
from configparser import ConfigParser
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

DIST = Path(__file__).resolve().parents[2] / 'packaging/fuxi-modem/root/usr/lib/python3/dist-packages'
UNITS = DIST.parents[1] / 'systemd/system'
with patch.object(sys, 'path', [str(DIST), *sys.path]):
    import fuxi_voice_calls as voice

try:
    import gi
    gi.require_version('Qmi', '1.0')
    gi.require_version('GLibUnix', '2.0')
    from gi.repository import Gio, GLib, GLibUnix, Qmi
except (ImportError, ValueError):
    Qmi = None


def indication(cid):
    payload = b'\x01\x08\x00\x01\x07\x03\x02\x01\x04\x00\x00\x10\x07\x00private'
    frame = bytes((0x80, 9, cid, 4, 0, 0, 0x2e, 0)) + len(payload).to_bytes(2, 'little') + payload
    return b'\x01' + (len(frame) + 2).to_bytes(2, 'little') + frame


class CollectorUnitTest(unittest.TestCase):
    unit = UNITS / 'fuxi-voice-collector.service'

    def test_enable_pulls_collector_with_resident_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            units = Path(directory) / 'usr/lib/systemd/system'
            units.mkdir(parents=True)
            for name in (self.unit.name, 'fuxi-qmi-proxy.service'):
                shutil.copyfile(UNITS / name, units / name)
            command = ['systemctl', '--root', directory]
            subprocess.run([*command, 'enable', self.unit.name], check=True, capture_output=True)
            link = Path(directory) / 'etc/systemd/system/fuxi-qmi-proxy.service.wants' / self.unit.name
            self.assertEqual(os.readlink(link), '/usr/lib/systemd/system/' + self.unit.name)
            subprocess.run([*command, 'disable', self.unit.name], check=True, capture_output=True)
            self.assertFalse(link.is_symlink())

    def test_main_process_owns_readiness_and_startup_retry(self):
        unit = ConfigParser(interpolation=None)
        unit.read(self.unit)
        service = unit['Service']
        self.assertEqual(service['Type'], 'notify')
        self.assertEqual(service['NotifyAccess'], 'main')
        self.assertNotIn('ExecStartPost', service)
        self.assertEqual(service['TimeoutStartSec'], '100')
        self.assertEqual(service['Restart'], 'on-failure')
        self.assertEqual(service['RestartSec'], '10')

    def test_notify_ready_delivers_to_path_and_abstract_sockets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'notify.sock')
            for address, destination in [(path, path), ('@' + path, '\0' + path)]:
                with self.subTest(address=address), socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
                    receiver.bind(destination)
                    receiver.settimeout(1)
                    with patch.dict(os.environ, NOTIFY_SOCKET=address):
                        voice._notify_ready()
                    self.assertEqual(receiver.recv(64), b'READY=1')


@unittest.skipUnless(Qmi, 'requires host QMI and GLibUnix typelibs')
class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.device = Mock()
        self.clients = [Mock(), Mock()]
        self.handlers = {}
        self.commands = []
        self.released = []
        self.failure = None
        self.emit_on = 'query'

        def connect(name, handler):
            key = (name, len(self.handlers))
            self.handlers[key] = handler
            return key

        self.device.connect.side_effect = connect
        self.device.disconnect.side_effect = self.handlers.pop
        self.device.allocate_client_finish.side_effect = self.clients
        self.device.command_full_finish.side_effect = lambda result: result
        self.device.release_client_finish.return_value = True
        for slot, client in enumerate(self.clients):
            client.get_cid.return_value = 7 + slot
            client.get_next_transaction_id.side_effect = iter(range(1, 255))
            client.get_service.return_value = Qmi.Service.VOICE
            client.get_device.return_value = self.device
            client.indication_register_finish.return_value.get_result.return_value = None
        self.source = voice.VoiceIndicationSource(5)

    def emit(self, cid):
        for (name, _), callback in list(self.handlers.items()):
            if name == 'indication':
                callback(self.device, indication(cid))

    def wait(self, start, *args):
        if start == self.device.allocate_client:
            self.assertEqual(args[0], Qmi.Service.VOICE)
        elif start == self.device.release_client:
            self.assertEqual(args[1], Qmi.DeviceReleaseClientFlags.RELEASE_CID)
            self.released.append(args[0].get_cid())
        elif start in [client.indication_register for client in self.clients]:
            self.assertTrue(args[0].get_call_notification_events())
            if start == self.clients[1].indication_register and self.emit_on == 'register':
                self.emit(8)
        elif start == self.device.command_full:
            request = args[0]
            cid = Qmi.message_get_client_id(request)
            message = Qmi.message_get_message_id(request)
            self.commands.append((cid, message))
            self.assertEqual(Qmi.message_get_service(request), Qmi.Service.VOICE)
            self.assertIn(message, (0x44, 0x2f))
            if message == 0x44:
                offset, length = Qmi.message_tlv_read_init(request, 1)
                self.assertEqual(length, 1)
                self.assertEqual(Qmi.message_tlv_read_guint8(request, offset, 0)[2], cid - 7)
            if self.failure == (cid, message, 'header'):
                request = Qmi.message_new(Qmi.Service.VOICE, cid, 3, message)
            error = Qmi.ProtocolError.INTERNAL if self.failure == (cid, message, 'result') else Qmi.ProtocolError.NONE
            result = Qmi.message_response_new(request, error)
            if message == 0x2f:
                if cid == 8 and self.emit_on == 'query':
                    self.emit(cid)
                if cid == 7 or self.emit_on == 'query':
                    offset = Qmi.message_tlv_write_init(result, 0x10)
                    Qmi.message_tlv_write_guint8(result, 0)
                    Qmi.message_tlv_write_complete(result, offset)
            return self.device, result
        else:
            self.fail('Unexpected QMI operation')
        return self.device, object()

    def setup_collector(self, resources):
        return voice._observe_dual(resources, Qmi, GLib, self.device, self.wait, self.source)

    def test_readback_both_slots_and_indication_overlapping_query(self):
        with ExitStack() as resources:
            self.setup_collector(resources)
            self.assertEqual(self.commands, [(7, 0x44), (7, 0x2f), (8, 0x44), (8, 0x2f)])
            self.assertEqual(self.source.snapshot(), {0: [], 1: [(7, 3, 2, 4, 0)]})
            stale_handler = list(self.handlers.values())[-1]
        self.assertEqual(self.released, [8, 7])
        self.assertFalse(self.handlers)
        stale_handler(self.device, indication(8))
        self.assertEqual(self.source.observations(), {0: None, 1: None})

    def test_registration_ring_survives_missing_call_tlv(self):
        self.emit_on = 'register'
        with ExitStack() as resources:
            self.setup_collector(resources)
            self.assertEqual(self.source.snapshot(), {0: [], 1: [(7, 3, 2, 4, 0)]})

    def test_registration_error_unwinds_both_before_second_readback(self):
        result = self.clients[1].indication_register_finish.return_value
        result.get_result.side_effect = GLib.Error('registration rejected')
        with self.assertRaisesRegex(GLib.Error, 'registration rejected'), ExitStack() as resources:
            self.setup_collector(resources)
        self.assertEqual(self.commands, [(7, 0x44), (7, 0x2f), (8, 0x44)])
        self.assertEqual(self.released, [8, 7])
        self.assertFalse(self.handlers)
        self.assertEqual(self.source.observations(), {0: None, 1: None})

    def test_quiet_slot_with_missing_call_tlv_remains_unknown(self):
        self.emit_on = None
        with ExitStack() as resources, self.assertRaises(voice.IncompleteVoiceCalls) as error:
            self.setup_collector(resources)
            self.source.snapshot()
        self.assertEqual(error.exception.snapshot, {0: [], 1: None})

    def test_wrong_bind_header_rejected_and_releases_clients(self):
        self.failure = (8, 0x44, 'header')
        with self.assertRaisesRegex(ValueError, 'header'), ExitStack() as resources:
            self.setup_collector(resources)
        self.assertEqual(self.released, [8, 7])
        self.assertFalse(self.handlers)
        self.assertNotIn((8, 0x2f), self.commands)

    def test_rejected_query_unwinds_both(self):
        self.failure = (8, 0x2f, 'result')
        with self.assertRaises(RuntimeError), ExitStack() as resources:
            self.setup_collector(resources)
        self.assertEqual(self.released, [8, 7])
        self.assertFalse(self.handlers)

    def test_collector_entry_serves_sanitized_packet_and_unwinds_on_sigterm(self):
        @contextmanager
        def transport(context):
            yield Qmi, GLib, self.device, self.wait

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'calls.sock')
            client = self.enterContext(socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET))
            client.settimeout(1)
            errors = []

            def ready():
                for subscription in self.clients:
                    subscription.indication_register_finish.return_value.get_result.assert_called_once()
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                client.connect(path)

            def probe():
                try:
                    GLib.MainContext.default().iteration(False)
                    packet = client.recv(voice.MAX_PACKET)
                    self.assertEqual(voice._decode_observations(packet), {0: [], 1: [(7, 3, 2, 4, 0)]})
                    self.assertNotIn(b'private', packet)
                    self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    os.kill(os.getpid(), signal.SIGTERM)
                return GLib.SOURCE_REMOVE

            idle = GLib.idle_source_new()
            idle.set_callback(lambda *_: probe())
            idle.attach(GLib.MainContext.default())
            try:
                with patch.object(voice, '_transport', transport), patch.object(voice, 'COLLECTOR_SOCKET', path), \
                        patch.object(voice, '_notify_ready', side_effect=ready) as notify:
                    self.assertEqual(voice.run_collector(), 0)
                    notify.assert_called_once_with()
            finally:
                idle.destroy()
            if errors:
                raise errors[0]
            self.assertFalse(os.path.exists(path))
        self.assertEqual(self.released, [8, 7])
        self.assertFalse(self.handlers)

    def test_startup_failure_exits_before_ready(self):
        @contextmanager
        def transport(context):
            yield Qmi, GLib, self.device, self.wait

        with tempfile.TemporaryDirectory() as directory, patch.object(voice, '_transport', transport), \
                patch.object(voice, 'COLLECTOR_SOCKET', directory), patch.object(voice, '_notify_ready') as notify:
            result = self.clients[1].indication_register_finish.return_value
            result.get_result.side_effect = GLib.Error('registration rejected')
            with self.assertRaisesRegex(GLib.Error, 'registration rejected'):
                voice.run_collector()
            notify.assert_not_called()
            self.device.allocate_client_finish.side_effect = self.clients
            result.get_result.side_effect = None
            with self.assertRaises(OSError):
                voice.run_collector()
            notify.assert_not_called()
        self.assertEqual(self.released, [8, 7, 8, 7])
        self.assertFalse(self.handlers)

    def demand_fixture(self):
        self.clock = self.enterContext(patch.object(voice, '_boottime', return_value=100))
        self.source = voice.VoiceIndicationSource(5, clock=self.clock)
        clients = self.setup_collector(self.enterContext(ExitStack()))
        self.requests = {}

        def command(request, context, timeout, cancel, callback):
            self.assertEqual(timeout, voice.REFRESH_SECONDS)
            self.assertEqual(Qmi.message_get_message_id(request), 0x2f)
            self.assertEqual(Qmi.message_get_transaction_id(request), 3)
            self.requests[Qmi.message_get_client_id(request)] = (request, callback, cancel)

        self.device.command_full.side_effect = command
        glib = Mock(wraps=GLib)
        glib.SOURCE_REMOVE = GLib.SOURCE_REMOVE
        glib.Error = GLib.Error
        glib.timeout_add.return_value = 123
        glib.source_remove.return_value = True
        refresh = voice._DemandRefresh(self.source, lambda slot, cancel, done: voice._read_async(
            Qmi, GLib, self.device, clients[slot], cancel, done), glib)
        self.addCleanup(refresh.close)
        return refresh, glib

    def reader(self, refresh):
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        server.setblocking(False)
        client.settimeout(0.2)
        refresh.accept(server)
        return client

    def complete(self, cid, data=None, malformed_header=False):
        request, callback, _ = self.requests[cid]
        if malformed_header:
            request = Qmi.message_new(Qmi.Service.VOICE, cid, 27, 0x2f)
        response = Qmi.message_response_new(request, Qmi.ProtocolError.NONE)
        if data is not None:
            offset = Qmi.message_tlv_write_init(response, 0x10)
            for value in data:
                Qmi.message_tlv_write_guint8(response, value)
            Qmi.message_tlv_write_complete(response, offset)
        offset = Qmi.message_tlv_write_init(response, 0x11)
        for value in b'private':
            Qmi.message_tlv_write_guint8(response, value)
        Qmi.message_tlv_write_complete(response, offset)
        callback(self.device, response)

    def test_quiet_long_call_refreshed_only_on_demand_and_shared(self):
        refresh, glib = self.demand_fixture()
        self.reader(refresh).recv(voice.MAX_PACKET)
        glib.timeout_add.assert_not_called()
        self.clock.return_value += 3600
        self.assertFalse(self.requests)
        clients = [self.reader(refresh) for _ in range(voice.MAX_WAITERS)]
        self.assertEqual(self.reader(refresh).recv(1), b'')
        self.assertEqual(self.device.command_full.call_count, 2)
        self.assertEqual(set(self.requests), {7, 8})
        self.complete(8, b'\x01\x07\x03\x02\x01\x04\x00\x00')
        self.complete(7, b'\x00')
        for client in clients:
            packet = client.recv(voice.MAX_PACKET)
            self.assertNotIn(b'private', packet)
            self.assertEqual(voice._decode_observations(packet), {0: [], 1: [(7, 3, 2, 4, 0)]})
        self.reader(refresh).recv(voice.MAX_PACKET)
        self.assertEqual(self.device.command_full.call_count, 2)
        glib.source_remove.assert_called_once_with(123)

    def test_new_indication_wins_and_missing_tlv_stays_unknown(self):
        refresh, _ = self.demand_fixture()
        self.clock.return_value += 5
        client = self.reader(refresh)
        self.emit(8)
        self.complete(8, b'\x00')
        self.complete(7)
        self.assertEqual(voice._decode_observations(client.recv(voice.MAX_PACKET)),
                         {0: None, 1: [(7, 3, 2, 4, 0)]})
        self.reader(refresh).recv(voice.MAX_PACKET)
        self.assertEqual(self.device.command_full.call_count, 2)

    def test_refresh_only_expired_slot_and_reject_mismatched_reply(self):
        refresh, _ = self.demand_fixture()
        self.clock.return_value += 5
        self.emit(8)
        client = self.reader(refresh)
        self.assertEqual(set(self.requests), {7})
        self.complete(7, b'\x00', malformed_header=True)
        self.assertEqual(voice._decode_observations(client.recv(voice.MAX_PACKET))[0], None)

    def test_refresh_timeout_keeps_concurrent_indication_and_ignores_late_replies(self):
        refresh, glib = self.demand_fixture()
        self.clock.return_value += 5
        client = self.reader(refresh)
        self.emit(8)
        self.clock.return_value += voice.REFRESH_SECONDS
        self.assertEqual(glib.timeout_add.call_args.args[1](), GLib.SOURCE_REMOVE)
        self.assertTrue(self.requests[7][2].is_cancelled())
        expected = {0: None, 1: [(7, 3, 2, 4, 0)]}
        self.assertEqual(voice._decode_observations(client.recv(voice.MAX_PACKET)), expected)
        self.complete(7, b'\x00')
        self.complete(8, b'\x00')
        with self.assertRaises(voice.IncompleteVoiceCalls) as error:
            self.source.snapshot()
        self.assertEqual(error.exception.snapshot, expected)

    def test_suspend_past_deadline_and_disconnect_drop_pending_work(self):
        refresh, _ = self.demand_fixture()
        self.clock.return_value += 5
        client = self.reader(refresh)
        self.clock.return_value += 100
        self.complete(7, b'\x00')
        self.assertEqual(voice._decode_observations(client.recv(voice.MAX_PACKET)), {0: None, 1: None})
        self.assertTrue(self.requests[8][2].is_cancelled())
        self.device.command_full.side_effect = lambda *args: None
        client = self.reader(refresh)
        cancel = refresh.cancel
        refresh.close()
        self.assertTrue(cancel.is_cancelled())
        self.assertEqual(client.recv(1), b'')
        self.complete(8, b'\x00')
        self.assertEqual(self.source.observations(), {0: None, 1: None})

    def test_real_gi_async_on_unopened_device_runs_on_calling_context(self):
        context = GLib.MainContext.new()
        context.push_thread_default()
        try:
            results = []
            voice._read_async(Qmi, GLib, Qmi.Device(), self.clients[0], Gio.Cancellable.new(),
                              lambda calls: results.append((context.is_owner(), calls)))
            self.assertEqual(results, [])
            self.assertTrue(context.iteration(False))
            self.assertEqual(results, [(True, None)])
        finally:
            context.pop_thread_default()

    def test_removal_with_socket_refresh_in_flight_on_real_loop(self):
        @contextmanager
        def transport(context):
            yield Qmi, GLib, self.device, self.wait

        self.emit_on = None
        context = GLib.MainContext.default()
        requests = []
        owners = []

        def command(request, _, timeout, cancel, callback):
            owners.append(context.is_owner())
            requests.append((request, cancel, callback))
            GLib.idle_add(remove)

        def remove():
            self.handlers[('device-removed', 0)](self.device)
            return GLib.SOURCE_REMOVE

        self.device.command_full.side_effect = command
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'calls.sock')
            client = self.enterContext(socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET))
            client.settimeout(1)

            def connect():
                client.connect(path)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(connect)
            guard = GLib.timeout_source_new(1500)
            guard.set_callback(lambda *_: remove())
            guard.attach(context)
            try:
                with patch.object(voice, '_transport', transport), patch.object(voice, 'COLLECTOR_SOCKET', path):
                    self.assertEqual(voice.run_collector(), 1)
            finally:
                guard.destroy()
            self.assertEqual(owners, [True])
            request, cancel, callback = requests[0]
            self.assertEqual(Qmi.message_get_client_id(request), 8)
            self.assertTrue(cancel.is_cancelled())
            self.assertEqual(client.recv(1), b'')
            callback(self.device, Qmi.message_response_new(request, Qmi.ProtocolError.NONE))
        self.assertEqual(self.released, [8, 7])
        self.assertFalse(self.handlers)


class ReaderTest(unittest.TestCase):
    def read(self, slots, uid=0, flags=0):
        connection = MagicMock()
        connection.getsockopt.return_value = struct.pack('3i', 7, uid, 0)
        connection.recvmsg.return_value = (json.dumps({'version': 1, 'slots': slots}).encode(), [], flags, None)
        with patch.object(voice, '_boottime', return_value=15), patch.object(socket, 'socket') as create:
            create.return_value.__enter__.return_value = connection
            return voice.read_indicated_voice_calls()

    def test_empty_expired_future_and_private_data(self):
        slots = {'0': [11, []], '1': [12, [[7, 3, 2, 4, 0]]]}
        self.assertEqual(self.read(slots), {0: [], 1: [(7, 3, 2, 4, 0)]})
        for invalid in ([10, []], [16, []], None):
            with self.subTest(invalid=invalid), self.assertRaises(voice.IncompleteVoiceCalls) as error:
                self.read({**slots, '1': invalid})
            self.assertEqual(error.exception.snapshot, {0: [], 1: None})
        for options in ({'uid': 1000}, {'flags': socket.MSG_TRUNC}):
            with self.assertRaises(voice.IncompleteVoiceCalls) as error:
                self.read(slots, **options)
            self.assertEqual(error.exception.snapshot, {0: None, 1: None})
        for records in ([[7, 3, 2, 4, 'private']], [[7, 3, 2, 4, 0]] * 2):
            with self.assertRaises(voice.IncompleteVoiceCalls) as error:
                self.read({**slots, '1': [12, records]})
            self.assertEqual(error.exception.snapshot, {0: None, 1: None})
