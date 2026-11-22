from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


DIST = Path(__file__).resolve().parents[2] / 'packaging/fuxi-modem/root/usr/lib/python3/dist-packages'
with patch.object(sys, 'path', [str(DIST), *sys.path]):
    import fuxi_sub2_call_cache as cache
    import fuxi_sub2_control


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.clock = self.enterContext(patch.object(cache, '_now', return_value=2 * cache.MAX_AGE_NS))
        self.generation = 'a' * 32
        self.observer = cache.CallCache(self.generation)
        self.ring = {'id': 12, 'state': 'incoming', 'direction': 'incoming'}

    def observe(self):
        self.observer.observe({12: self.ring})
        return self.observer.snapshot()

    def test_full_snapshot_replacement_invalidation_and_generation(self):
        self.assertIsNone(self.observer.snapshot())
        self.assertEqual(self.observe()['calls'], [self.ring])
        self.ring['state'] = 'end'
        self.assertEqual(self.observer.snapshot()['calls'][0]['state'], 'incoming')
        self.assertEqual(self.observe()['calls'][0]['state'], 'end')
        self.observer.observe({})
        self.assertEqual(self.observer.snapshot()['calls'], [])
        self.ring['number'] = 'private'
        self.assertIsNone(self.observe())
        self.observer.observe({})
        self.observer.observe(None)
        self.assertIsNone(self.observer.snapshot())
        self.assertIsNone(cache.CallCache('b' * 32).snapshot())
        for invalid in (None, '', 'a' * 33):
            with self.assertRaises(ValueError):
                cache.CallCache(invalid)

    def test_reads_do_not_refresh_observation(self):
        stamp = self.observe()['observed_boottime_ns']
        self.clock.return_value = stamp + cache.MAX_AGE_NS - 1
        self.assertEqual(self.observer.snapshot()['observed_boottime_ns'], stamp)
        self.clock.return_value += 1
        self.assertIsNone(self.observer.snapshot())

    def reader(self, packet, generation=None, flags=0, uid=0):
        connection = MagicMock()
        connection.getsockopt.return_value = struct.pack('3i', 120, uid, 0)
        connection.recvmsg.return_value = (packet, [], flags, None)
        with patch.object(socket, 'socket') as create:
            create.return_value.__enter__.return_value = connection
            return cache.read_snapshot(generation)

    def test_reader_checks_age_generation_peer_and_packet(self):
        record = self.observe()
        packet = json.dumps(record).encode()
        self.assertEqual(self.reader(packet, self.generation), record)
        self.assertIsNone(self.reader(packet, 'b' * 32))
        self.assertIsNone(self.reader(packet, uid=1000))
        self.assertIsNone(self.reader(packet, flags=socket.MSG_TRUNC))
        for malformed in (b'', b'{', b'null', b'[]', b'\xff'):
            self.assertIsNone(self.reader(malformed))
        for field, invalid in (
                ('slot', 2.0), ('slot', 1), ('version', True), ('generation', 'a'),
                ('observed_boottime_ns', -1), ('observed_boottime_ns', self.clock.return_value + 1),
                ('observed_boottime_ns', self.clock.return_value - cache.MAX_AGE_NS),
                ('calls', [self.ring, self.ring]), ('calls', [dict(self.ring, number='private')]),
                ('calls', [dict(self.ring, id=True)]), ('calls', [dict(self.ring, direction='private')]),
                ('calls', [dict(self.ring, state=None)]), ('unexpected', 1)):
            with self.subTest(field=field, value=invalid):
                self.assertIsNone(self.reader(json.dumps({**record, field: invalid}).encode()))

    def test_real_socket_dispatch_cleanup_and_replacement(self):
        try:
            import gi
            gi.require_version('GLibUnix', '2.0')
            from gi.repository import GLib, GLibUnix
        except (ImportError, ValueError):
            self.skipTest('GLibUnix typelib unavailable')
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'calls.sock')
            with patch.object(cache, 'SOCKET_PATH', path):
                self.assertIsNone(cache.read_snapshot())
                for generation in (self.generation, 'b' * 32):
                    with ExitStack() as resources:
                        observer = cache.CallCache(generation)
                        cache.serve(resources, GLibUnix, GLib, observer, path)
                        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                        for calls in (None, {12: self.ring}, {}):
                            observer.observe(calls)
                            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
                                client.settimeout(1)
                                client.connect(path)
                                GLib.MainContext.default().iteration(False)
                                self.assertEqual(json.loads(client.recv(cache.MAX_PACKET)), observer.snapshot())
                        if os.getuid() != 0:
                            self.assertIsNone(cache.read_snapshot())
                    self.assertFalse(os.path.exists(path))
                    self.assertIsNone(cache.read_snapshot())


if __name__ == '__main__':
    unittest.main()
