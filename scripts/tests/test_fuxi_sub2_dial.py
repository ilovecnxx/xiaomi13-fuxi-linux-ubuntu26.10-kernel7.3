import io
import runpy
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


DIAL = Path(__file__).resolve().parents[2] / 'packaging/fuxi-modem/root/usr/sbin/fuxi-qmi-sub2-dial.py'


def frame(call_id, state):
    record = bytes((1, call_id, state, 2, 1, 4, 0, 0))
    payload = bytes((1, len(record), 0)) + record
    body = bytes((0x80, 9, 11, 4, 0, 0, 0x2e, 0, len(payload), 0)) + payload
    return bytes((1, len(body) + 2, 0)) + body


class DialLifecycleTest(unittest.TestCase):
    def replay(self, *, before_dial=(), during_dial=(), during_hold=()):
        device = Mock()
        voice = Mock()
        voice.get_cid.return_value = 11
        device.allocate_client_finish.return_value = voice
        voice.dial_call_finish.return_value.get_call_id.return_value = 7
        qmi = Mock()
        qmi.Device.new_from_node_finish.return_value = device
        qrtr = Mock()
        context = Mock()
        glib = SimpleNamespace(MainContext=Mock(), Error=RuntimeError)
        glib.MainContext.default.return_value = context
        tick = 0.0
        events = iter(during_hold)

        def emit(event):
            callback = device.connect.call_args.args[1]
            callback(device, frame(*event))

        def iterate(block):
            event = next(events, None)
            if event:
                emit(event)

        def sleep(seconds):
            nonlocal tick
            tick += seconds

        def complete(*args):
            args[-1](None, object())

        def complete_after(events):
            def dispatch(*args):
                for event in events:
                    emit(event)
                complete(*args)
            return dispatch

        for start in (qrtr.Bus.new, qmi.Device.new_from_node, device.open,
                      device.allocate_client, voice.end_call, device.release_client):
            start.side_effect = complete
        voice.indication_register.side_effect = complete_after(before_dial)
        voice.dial_call.side_effect = complete_after(during_dial)
        context.iteration.side_effect = iterate
        modules = {'gi': Mock(), 'gi.repository': SimpleNamespace(Qmi=qmi, Qrtr=qrtr, GLib=glib),
                   'fuxi_qmi_bind': Mock()}
        with patch.dict(sys.modules, modules), patch.object(sys, 'argv', [str(DIAL), '000000000000000', '1']), \
                patch('signal.signal'), patch('time.time', lambda: tick), patch('time.monotonic', lambda: tick), \
                patch('time.sleep', sleep), redirect_stdout(io.StringIO()):
            runpy.run_path(str(DIAL), run_name='__main__')
        device.release_client.assert_called_once()
        if voice.end_call.called:
            voice.end_call.assert_called_once()
            qmi.MessageVoiceEndCallInput.return_value.set_call_id.assert_called_once_with(7)
        return voice.end_call.call_count

    def test_other_call_ending_does_not_suppress_hangup(self):
        self.assertEqual(self.replay(during_hold=[(8, 9), (7, 3)]), 1)

    def test_own_end_during_hold_suppresses_duplicate_hangup(self):
        self.assertEqual(self.replay(during_hold=[(7, 9)]), 0)

    def test_own_end_before_dial_response_is_observed(self):
        self.assertEqual(self.replay(during_dial=[(7, 9)]), 0)

    def test_reused_id_ignores_end_before_dial_request(self):
        self.assertEqual(self.replay(before_dial=[(7, 9)]), 1)
