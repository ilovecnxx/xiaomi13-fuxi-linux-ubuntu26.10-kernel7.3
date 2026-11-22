import contextlib
import io
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch

DIST = Path(__file__).resolve().parents[2] / 'packaging/fuxi-modem/root/usr/lib/python3/dist-packages'
with patch.object(sys, 'path', [str(DIST), *sys.path]):
    import fuxi_call_owner as coordinator
    import fuxi_voice_calls as collector


CONNECTED = (71, 3, 2, 4, 0)
ENDED = (71, 9, 2, 4, 0)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.endpoint = Path(directory, 'initial_voice_v1')
        self.debt = Path(directory, 'cleanup-required')
        self.stop = Event()
        self.owner = coordinator.Owner(self.stop, self.debt)
        self.enterContext(patch.object(coordinator.audio, 'ENDPOINT', self.endpoint))
        self.enterContext(patch.object(coordinator.audio, 'LOCK', str(Path(directory, 'lock'))))
        self.enterContext(patch.object(coordinator, 'DEBT', self.debt))
        self.calls = {0: [], 1: []}
        self.commands = []
        self.route('ready')
        self.read = self.enterContext(patch.object(coordinator, 'read_indicated_voice_calls',
                                                    side_effect=self.snapshot))
        self.write = self.enterContext(patch.object(coordinator.audio, 'submit', side_effect=self.submit))

    def route(self, state):
        self.endpoint.write_text(state + '\n', encoding='ascii')

    def snapshot(self):
        if None in self.calls.values():
            raise collector.IncompleteVoiceCalls(self.calls)
        return self.calls

    def submit(self, command):
        self.commands.append(command)
        self.route('awaiting-peers' if command == 'stop' else command.replace('start-', 'active-'))

    def start(self, slot=0):
        self.calls[slot] = [CONNECTED]
        self.owner.step()
        self.assertEqual(self.owner.call, (slot, CONNECTED[0]))

    def test_call_appears_after_idle_or_missing_peers(self):
        self.owner.step()
        self.calls[0] = [CONNECTED]
        for route in ('awaiting-peers', 'unavailable', 'unexpected-state'):
            self.route(route)
            for _ in range(4):
                self.assertIn('calls=not-polled', self.owner.step())
                self.assertEqual(self.owner.delay, 2)
        self.assertEqual(self.read.call_count, 1)
        self.write.assert_not_called()
        self.route('ready')
        self.owner.step()
        self.owner.step()
        self.assertEqual(self.commands, ['start-sim1'])

    def test_unknown_both_and_single_confirmed(self):
        for calls, label in [({0: None, 1: None}, 'unknown-both'),
                             ({0: [CONNECTED], 1: None}, 'connected-sim1-other-unknown'),
                             ({0: None, 1: [CONNECTED]}, 'connected-sim2-other-unknown'),
                             ({0: None, 1: []}, 'unknown-sim1')]:
            self.calls = calls
            self.assertIn(label, self.owner.step())
        self.assertEqual(self.commands, [])

    def test_end_or_disappearance_releases(self):
        for records in ([ENDED], []):
            self.route('ready')
            self.start(1)
            self.calls[1] = records
            self.owner.step()
            self.owner.step()
            self.assertFalse(self.debt.exists())
        self.assertEqual(self.commands, ['start-sim2', 'stop'] * 2)

    def test_switch_requires_fresh_checkpoint_and_observation(self):
        self.start()
        self.calls = {0: [ENDED], 1: [CONNECTED]}
        self.owner.step()
        self.owner.step()
        self.route('ready')
        self.owner.step()
        self.calls[1] = [(72, *CONNECTED[1:])]
        self.owner.step()
        self.route('ready')
        self.owner.step()
        self.assertEqual(self.commands, ['start-sim1', 'stop', 'start-sim2', 'stop', 'start-sim2'])
        self.assertEqual(self.owner.call, (1, 72))

    def test_ambiguity_and_loss_release(self):
        for calls in ({0: None, 1: None}, {0: [CONNECTED], 1: None},
                      {0: [CONNECTED], 1: [CONNECTED]}, {0: [], 1: [(71, 2, 2, 4, 0)]},
                      {0: [(71, 3, 2, 4, 1)], 1: []}):
            self.calls = {0: [], 1: []}
            self.route('ready')
            self.start()
            self.calls = calls
            self.owner.step()
            self.assertEqual(self.commands[-1], 'stop')
            self.assertIsNone(self.owner.call)

    def test_post_start_rechecks_identity(self):
        self.read.side_effect = [{0: [CONNECTED], 1: []}, {0: [], 1: [CONNECTED]}]
        self.owner.step()
        self.assertEqual(self.commands, ['start-sim1', 'stop'])
        self.assertIsNone(self.owner.call)

    def test_signal_during_start_releases_without_poll(self):
        def interrupt(command):
            self.submit(command)
            self.stop.set()
        self.write.side_effect = interrupt
        self.calls[1] = [CONNECTED]
        self.owner.step()
        self.assertEqual(self.read.call_count, 1)
        self.assertEqual(self.commands, ['start-sim2', 'stop'])

    def test_shutdown_during_collection_blocks_start(self):
        def interrupt():
            self.stop.set()
            return {0: [CONNECTED], 1: []}
        self.read.side_effect = interrupt
        self.owner.step()
        self.assertEqual(self.commands, [])

    def test_wrong_start_readback(self):
        self.calls[0] = [CONNECTED]
        self.write.side_effect = lambda command: self.route('active-sim2' if command != 'stop' else 'ready')
        self.assertIn('start-unconfirmed', self.owner.step())
        self.assertIsNone(self.owner.call)
        self.assertEqual(self.write.call_args.args, ('stop',))

    def test_rejected_start_backoff_in_daemon_loop(self):
        def reject(command):
            self.commands.append(command)
            if command != 'stop':
                raise OSError('rejected')
            self.route('ready')

        def advance(delay):
            delays.append(delay)
            if len(delays) == 6:
                self.calls[0] = [(72, *CONNECTED[1:])]
            if len(delays) == 7:
                self.stop.set()

        delays = []
        self.calls[0] = [CONNECTED]
        self.write.side_effect = reject
        with patch.object(self.stop, 'wait', side_effect=advance), patch.object(coordinator, 'notify'):
            coordinator.run(self.stop, self.debt)
        self.assertEqual(delays, [2, 4, 8, 16, 30, 30, 2])
        self.assertEqual(self.commands, ['start-sim1', 'stop'] * 7)

    def test_unconfirmed_start_backoff_resets_on_success(self):
        self.calls[1] = [CONNECTED]
        self.write.side_effect = lambda command: self.route('ready')
        for delay in (2, 4, 8):
            self.assertIn('start-unconfirmed', self.owner.step())
            self.assertEqual(self.owner.delay, delay)
        self.write.side_effect = self.submit
        self.start(1)
        self.assertEqual(self.owner.delay, 0.5)

    def test_unconfirmed_stop_backoff_precedes_idle_gate(self):
        self.start(1)
        reads = self.read.call_count
        self.route('unavailable')
        self.write.side_effect = lambda command: None
        for delay in (2, 4, 8, 16, 30, 30):
            self.assertIn('cleanup-pending', self.owner.step())
            self.assertEqual(self.owner.delay, delay)
            self.assertTrue(self.debt.exists())
        self.assertEqual(self.read.call_count, reads)
        self.write.side_effect = self.submit
        self.assertEqual(self.owner.step(), 'released')
        self.assertFalse(self.debt.exists())
        self.assertEqual(self.owner.delay, 0.5)

    def test_unresolved_start_and_stop_survive_process_restart(self):
        self.calls[1] = [CONNECTED]
        self.write.side_effect = OSError('unresolved command')
        self.assertIn('cleanup-pending', self.owner.step())
        self.assertTrue(self.debt.exists())
        restarted = coordinator.Owner(self.stop, self.debt)
        self.route('unavailable')
        self.write.side_effect = lambda _: None
        self.assertEqual(restarted.step(), 'cleanup-pending')
        self.write.side_effect = self.submit
        self.assertEqual(restarted.step(), 'released')
        self.assertFalse(self.debt.exists())
        self.assertEqual(self.read.call_count, 1)

    def test_restart_with_residual_route_even_without_marker(self):
        for route in ('active-sim1', 'active-sim2', 'cleanup'):
            self.route(route)
            self.assertEqual(self.owner.step(), 'released')
        self.assertEqual(self.commands, ['stop'] * 3)
        self.read.assert_not_called()

    def test_active_route_fault(self):
        self.start()
        self.route('cleanup')
        self.owner.step()
        self.assertEqual(self.commands, ['start-sim1', 'stop'])

    def test_cli_status_observes_without_writing(self):
        output = io.StringIO()
        self.calls[1] = [CONNECTED]
        with patch.object(sys, 'argv', ['owner', 'status']), contextlib.redirect_stdout(output):
            coordinator.main()
        self.assertEqual(output.getvalue(), 'calls=eligible-sim2 route=ready start_candidate=1\n')
        self.assertFalse(self.debt.exists())
        self.write.assert_not_called()

    def test_stop_post_obeys_debt_and_shared_lock(self):
        with patch.object(sys, 'argv', ['owner', 'cleanup']):
            coordinator.main()
            self.debt.touch()
            with coordinator.exclusive(), self.assertRaises(BlockingIOError):
                coordinator.main()
            self.write.assert_not_called()
            coordinator.main()
        self.assertEqual(self.commands, ['stop'])

    def test_run_notifies_and_releases_under_shared_lock(self):
        self.calls[0] = [CONNECTED]
        address = str(self.debt.with_name('notify.sock'))
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
            listener.bind(address)
            listener.settimeout(3)
            with patch.dict(os.environ, NOTIFY_SOCKET=address):
                worker = Thread(target=coordinator.run, args=(self.stop, self.debt))
                worker.start()
                try:
                    self.assertIn(b'READY=1\n', listener.recv(512))
                    self.assertEqual(listener.recv(512), b'STATUS=calls=eligible-sim1 route=active-sim1')
                    with self.assertRaises(BlockingIOError), coordinator.exclusive():
                        pass
                finally:
                    self.stop.set()
                    worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.commands, ['start-sim1', 'stop'])

    def test_packet_age_and_transport_failure(self):
        packet = json.dumps({'version': 1, 'slots': {'0': [20, [CONNECTED]], '1': [15, []]}})
        with patch.object(collector, '_boottime', return_value=20):
            self.calls = collector._decode_observations(packet)
        self.assertIn('connected-sim1-other-unknown', self.owner.step())
        with patch.object(collector, 'COLLECTOR_SOCKET', str(self.debt.with_name('absent'))), \
                patch.object(coordinator, 'read_indicated_voice_calls', collector.read_indicated_voice_calls):
            self.assertIn('unknown-both', self.owner.step())
        self.write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
