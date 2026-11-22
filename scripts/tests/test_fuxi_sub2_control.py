import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch


DIST = Path(__file__).resolve().parents[2] / 'packaging/fuxi-modem/root/usr/lib/python3/dist-packages'
with patch.object(sys, 'path', [str(DIST), *sys.path]):
    import fuxi_sub2_control as control

try:
    import gi
    gi.require_version('Qmi', '1.0')
    gi.require_version('Qrtr', '1.0')
    from gi.repository import GLib, Qmi, Qrtr
except (ImportError, ValueError):
    Qmi = None


def invoke(action, argument=None):
    with redirect_stdout(io.StringIO()) as output:
        code = control.run(action, argument)
    line, = output.getvalue().splitlines()
    return code, json.loads(line)


class ArgumentTest(unittest.TestCase):
    def test_invalid_never_opens_transport_or_echoes_argument(self):
        with patch.object(control, '_execute') as execute:
            for action, value in [('private', None), ('calls', ''), ('dial', None), ('dial', 'private'),
                                  ('dial', '０００'), ('dial', '0' * 16), ('answer', '256'), ('end', '-1'),
                                  ('end', True), ('end', '7\n'), ('wait-calls', '0'),
                                  ('wait-calls', '61'), ('wait-calls', ''), ('wait-calls', 5)]:
                with self.subTest(action=action, value=value):
                    code, reply = invoke(action, value)
                    self.assertEqual((code, reply['result']), (2, 'invalid_argument'))
                    self.assertNotIn('private', str(reply))
            execute.assert_not_called()


def record(call_id, state, direction):
    return bytes((call_id, state, 2, direction, 4, 0, 0))


def indication(payload, cid=11, service=9, flags=4, message=0x2e):
    header = bytes((0x80, service, cid, flags, 0, 0)) + message.to_bytes(2, 'little')
    body = header + len(payload).to_bytes(2, 'little') + payload
    return b'\x01' + (len(body) + 2).to_bytes(2, 'little') + body


def tlv(message, tag, value):
    offset = Qmi.message_tlv_write_init(message, tag)
    for byte in value:
        Qmi.message_tlv_write_guint8(message, byte)
    Qmi.message_tlv_write_complete(message, offset)


@unittest.skipUnless(Qmi, 'requires Qmi and Qrtr typelibs')
class ControlTest(unittest.TestCase):
    def setUp(self):
        self.device = Mock()
        self.voice = Mock()
        self.voice.get_device.return_value = self.device
        self.voice.get_cid.return_value = 11
        self.voice.get_next_transaction_id.side_effect = iter(range(1, 250))
        self.voice.get_service.return_value = Qmi.Service.VOICE
        self.device.allocate_client_finish.return_value = self.voice
        self.events = []
        self.records = b'\x02' + record(7, 2, 2) + record(8, 3, 1)
        self.fail = None
        self.private = b'private-identity'
        self.number = '+' + '0' * 15
        self.bind_frames = []
        self.query_frames = []
        self.register_frames = []
        self.later_frames = []

        def emit(frames):
            for frame in frames:
                self.callback(self.device, frame)
            return GLib.SOURCE_REMOVE

        def connect(signal, callback):
            self.assertEqual(signal, 'indication')
            self.events.append('connect')
            self.callback = callback
            return 43

        def register(request, timeout, cancel, done):
            self.assertTrue(request.get_call_notification_events())
            self.events.append('register')
            emit(self.register_frames)
            if self.later_frames:
                source = GLib.idle_source_new()
                source.set_callback(lambda *_: emit(self.later_frames))
                source.attach(GLib.MainContext.get_thread_default())
            complete(done)

        self.device.connect.side_effect = connect
        self.device.disconnect.side_effect = lambda handler: self.events.append('disconnect')
        self.voice.indication_register.side_effect = register

        def complete(*args):
            args[-1](None, object())

        def command(request, context, timeout, cancel, done):
            self.assertEqual(Qmi.message_get_service(request), Qmi.Service.VOICE)
            self.assertEqual(Qmi.message_get_client_id(request), 11)
            message_id = Qmi.message_get_message_id(request)
            event = {0x44: 'bind', 0x2f: 'query'}[message_id]
            self.events.append(event)
            if event == 'bind':
                offset, length = Qmi.message_tlv_read_init(request, 1)
                self.assertEqual((length, Qmi.message_tlv_read_guint8(request, offset, 0)[2]), (1, 1))
                emit(self.bind_frames)
            elif self.fail == 'header':
                request = Qmi.message_new(Qmi.Service.VOICE, 12, 2, message_id)
            error = Qmi.ProtocolError.INTERNAL if self.fail == event else Qmi.ProtocolError.NONE
            response = Qmi.message_response_new(request, error)
            if event == 'query':
                emit(self.query_frames)
                if self.records is not None:
                    tlv(response, 0x10, self.records)
                tlv(response, 0x11, self.private)
            done(None, response)

        def release(client, flags, timeout, cancel, done):
            self.assertIs(client, self.voice)
            self.assertEqual(flags, Qmi.DeviceReleaseClientFlags.RELEASE_CID)
            self.events.append('release')
            complete(done)

        def close(timeout, cancel, done):
            self.events.append('close')
            complete(done)

        def mutate(action):
            def dispatch(request, timeout, cancel, done):
                self.events.append(action)
                if action == 'dial':
                    self.assertEqual(request.get_calling_number(), self.number)
                else:
                    self.assertEqual(request.get_call_id(), self.selected)
                complete(done)
            return dispatch

        self.device.open.side_effect = complete
        self.device.allocate_client.side_effect = complete
        self.device.command_full.side_effect = command
        self.device.command_full_finish.side_effect = lambda response: response
        self.device.release_client.side_effect = release
        self.device.close_async.side_effect = close
        self.device.release_client_finish.side_effect = lambda _: self.fail != 'release'
        self.device.close_finish.side_effect = lambda _: self.fail != 'close'
        parsers = {'dial': (0x20, Qmi.message_voice_dial_call_response_parse),
                   'answer': (0x22, Qmi.message_voice_answer_call_response_parse),
                   'end': (0x21, Qmi.message_voice_end_call_response_parse)}

        def finish(action):
            if self.fail == 'timeout':
                raise TimeoutError(self.number)
            message_id, parse = parsers[action]
            error = Qmi.ProtocolError.INTERNAL if self.fail == 'rejected' else Qmi.ProtocolError.NONE
            request = Qmi.message_new(Qmi.Service.VOICE, 11, 3, message_id)
            response = Qmi.message_response_new(request, error)
            if self.fail != 'no_id':
                tlv(response, 0x10, bytes((self.selected,)))
            return parse(response)

        self.selected = 7
        for action in parsers:
            getattr(self.voice, action + '_call').side_effect = mutate(action)
            getattr(self.voice, action + '_call_finish').side_effect = lambda _, action=action: finish(action)
        self.starting_context = GLib.MainContext.get_thread_default()
        for owner, name, replacement in [(Qrtr.Bus, 'new', complete), (Qrtr.Bus, 'new_finish', Mock()),
                                         (Qmi.Device, 'new_from_node', complete),
                                         (Qmi.Device, 'new_from_node_finish', Mock(return_value=self.device))]:
            self.enterContext(patch.object(owner, name, replacement))

    def check(self, action, argument, code, result, events):
        self.events.clear()
        actual_code, reply = invoke(action, argument)
        self.assertEqual((actual_code, reply['result']), (code, result), reply)
        self.assertEqual((reply['version'], reply['slot'], reply['action']), (1, 2, action))
        self.assertNotIn(self.private.decode(), str(reply))
        self.assertNotIn(self.number, str(reply))
        self.assertEqual(self.events, events + ['release', 'close'])
        self.assertEqual(GLib.MainContext.get_thread_default(), self.starting_context)
        return reply

    def test_list_only_public_fields_and_explicit_empty(self):
        reply = self.check('calls', None, 0, 'ok', ['bind', 'query'])
        self.assertEqual(reply['calls'], [{'id': 7, 'state': 'incoming', 'direction': 'incoming'},
                                          {'id': 8, 'state': 'conversation', 'direction': 'outgoing'}])
        self.records = b'\x00'
        self.assertEqual(self.check('calls', None, 0, 'ok', ['bind', 'query'])['calls'], [])

    def test_discovery_fills_missing_query_from_listener_only(self):
        cached = [{'id': 8, 'state': 'waiting', 'direction': 'incoming'}]
        cache = Mock()
        cache.read_snapshot.return_value = dict(calls=cached, observed_boottime_ns=200)
        with patch.dict(sys.modules, fuxi_sub2_call_cache=cache):
            self.records = None
            reply = self.check('calls', None, 0, 'ok', ['bind', 'query'])
            self.assertEqual((reply['calls'], reply['source'], reply['observed_boottime_ns']),
                             (cached, 'indication', 200))
            self.records = b'\x00'
            self.assertEqual(self.check('calls', None, 0, 'ok', ['bind', 'query'])['calls'], [])
            cache.read_snapshot.assert_called_once_with()

    def test_accepted_mutations_only_dispatch_selected_operation(self):
        for state in (2, 7):
            self.records = b'\x02' + record(8, 3, 1) + record(7, state, 2)
            self.check('answer', '7', 0, 'accepted', ['bind', 'connect', 'query', 'disconnect', 'answer'])
        self.selected = 8
        self.check('end', '8', 0, 'accepted', ['bind', 'connect', 'query', 'disconnect', 'end'])
        self.assertEqual(self.check('dial', self.number, 0, 'accepted', ['bind', 'dial'])['call_id'], 8)

    def test_snapshot_unknown_and_invalid_responses_block_dispatch(self):
        for records in (None, b'', b'\x01', b'\x00\x00', b'\x02' + record(7, 2, 2) * 2):
            self.records = records
            for action in ('calls', 'answer', 'end'):
                if action == 'calls':
                    self.check(action, None, 1, 'calls_unknown', ['bind', 'query'])
                else:
                    registration = ['register'] if records is None else []
                    self.check(action, '7', 1, 'calls_unknown', ['bind', 'connect', 'query'] + registration + ['disconnect'])
        for fail in ('query', 'header'):
            self.fail = fail
            self.records = b'\x01' + record(7, 3, 1)
            self.check('end', '7', 1, 'calls_unknown', ['bind', 'connect', 'query', 'disconnect'])

    def test_selected_id_state_and_direction_gate_mutations(self):
        for action, call_id, state, direction in [('answer', 8, 2, 2), ('answer', 7, 2, 1),
                ('answer', 7, 3, 2), ('end', 7, 8, 2), ('end', 7, 9, 1), ('end', 7, 255, 2),
                ('end', 7, 3, 255), ('end', 8, 3, 1)]:
            with self.subTest(action=action, call_id=call_id, state=state, direction=direction):
                self.records = b'\x01' + record(call_id, state, direction)
                self.check(action, '7', 1, 'blocked', ['bind', 'connect', 'query', 'disconnect'])

    def test_uncertain_and_rejected_requests_never_replay(self):
        for action in ('dial', 'answer', 'end'):
            for failure, code, result in [('timeout', 3, 'result_unknown'), ('rejected', 1, 'rejected')]:
                self.fail = failure
                argument = self.number if action == 'dial' else '7'
                snapshot = [] if action == 'dial' else ['connect', 'query', 'disconnect']
                self.check(action, argument, code, result, ['bind'] + snapshot + [action])

    def call_frame(self, *records, **header):
        value = bytes((len(records),)) + b''.join(records)
        return indication(bytes((1, len(value), 0)) + value, **header)

    def test_missing_call_tlv_uses_indication_during_query_or_after_registration(self):
        self.records = None
        self.query_frames = [self.call_frame(record(8, 2, 2), record(7, 7, 2))]
        self.check('answer', '7', 0, 'accepted', ['bind', 'connect', 'query', 'disconnect', 'answer'])
        self.query_frames = [self.call_frame(record(7, 9, 2), cid=12)]
        self.later_frames = [self.call_frame(record(7, 3, 1))]
        self.fail = 'timeout'
        self.check('end', '7', 3, 'result_unknown', ['bind', 'connect', 'query', 'register', 'disconnect', 'end'])

    def test_latest_matching_indication_gates_selected_id_and_state(self):
        self.records = None
        eligible = self.call_frame(record(7, 2, 2))
        for action, last, result in [('answer', self.call_frame(record(8, 2, 2)), 'blocked'),
                ('answer', self.call_frame(record(7, 3, 2)), 'blocked'),
                ('answer', self.call_frame(record(7, 2, 1)), 'blocked'),
                ('end', self.call_frame(record(7, 9, 1)), 'blocked'),
                ('end', self.call_frame(record(7, 3, 0)), 'blocked'),
                ('end', indication(b''), 'calls_unknown')]:
            with self.subTest(action=action, last=last):
                self.register_frames = [eligible, last]
                self.check(action, '7', 1, result, ['bind', 'connect', 'query', 'register', 'disconnect'])

    def test_expired_observation_and_failed_registration_block_submission(self):
        self.records = None
        self.query_frames = [self.call_frame(record(7, 2, 2))]
        self.register_frames = [self.call_frame(record(7, 2, 2))]
        self.voice.indication_register_finish.return_value.get_result.side_effect = RuntimeError()
        with patch.object(control, 'monotonic', side_effect=[0, control.FALLBACK_SECONDS, 3]):
            self.check('answer', '7', 1, 'calls_unknown', ['bind', 'connect', 'query', 'register', 'disconnect'])

    def test_query_with_explicit_empty_or_invalid_metadata_keeps_precedence(self):
        self.query_frames = [self.call_frame(record(7, 2, 2))]
        for records, result in [(b'\x00', 'blocked'), (b'\x01', 'calls_unknown')]:
            self.records = records
            self.check('answer', '7', 1, result, ['bind', 'connect', 'query', 'disconnect'])

    def test_missing_dial_id_keeps_confirmed_acceptance(self):
        self.fail = 'no_id'
        self.assertIsNone(self.check('dial', self.number, 0, 'accepted', ['bind', 'dial'])['call_id'])

    def test_bind_failure_prevents_dispatch_and_cleanup_failure_preserves_outcome(self):
        self.fail = 'bind'
        self.check('dial', self.number, 1, 'unavailable', ['bind'])
        for failure in ('release', 'close'):
            self.fail = failure
            self.assertTrue(self.check('dial', self.number, 0, 'accepted', ['bind', 'dial'])['cleanup_failed'])

    def test_allocation_failure_closes_device_and_restores_context(self):
        self.device.allocate_client_finish.side_effect = RuntimeError(self.private)
        self.assertEqual(invoke('dial', self.number)[1]['result'], 'unavailable')
        self.assertEqual(self.events, ['close'])
        self.assertEqual(GLib.MainContext.get_thread_default(), self.starting_context)

    def check_wait(self, result, code, seconds=None):
        reply = self.check('wait-calls', seconds, code, result, ['connect', 'bind', 'register', 'disconnect'])
        self.device.disconnect.assert_called_with(43)
        return reply

    def test_first_matching_event_survives_bind_and_register(self):
        private_tlv = bytes((0x10, len(self.private), 0)) + self.private
        first = indication(private_tlv + bytes((1, len(self.records), 0)) + self.records)
        self.bind_frames = [first]
        self.register_frames = [indication(b'\x01\x01\x00\x00')]
        self.assertEqual(self.check_wait('ok', 0)['calls'], [
            {'id': 7, 'state': 'incoming', 'direction': 'incoming'},
            {'id': 8, 'state': 'conversation', 'direction': 'outgoing'}])

    def test_failed_bind_or_register_discards_buffered_event(self):
        self.bind_frames = [indication(b'\x01\x01\x00\x00')]
        self.fail = 'bind'
        reply = self.check('wait-calls', None, 1, 'unavailable', ['connect', 'bind', 'disconnect'])
        self.assertNotIn('calls', reply)
        self.fail = None
        self.voice.indication_register_finish.return_value.get_result.side_effect = RuntimeError(self.private)
        self.assertNotIn('calls', self.check_wait('unavailable', 1))

    def test_wrong_headers_ignored_then_valid_event_on_private_context(self):
        for kwargs in ({'cid': 12}, {'cid': 255}, {'service': 5}, {'flags': 2}, {'message': 0x2f}):
            self.register_frames.append(indication(b'', **kwargs))
        self.later_frames = [indication(b'\x01\x01\x00\x00')]
        self.assertEqual(self.check_wait('ok', 0, '60')['calls'], [])

    def test_invalid_client_identity_skips_observation(self):
        for cid in (0, 255):
            self.voice.get_cid.return_value = cid
            self.check('wait-calls', None, 1, 'unavailable', [])
        self.voice.get_cid.return_value = 11
        self.voice.get_device.return_value = Mock()
        self.check('wait-calls', None, 1, 'unavailable', [])

    def test_missing_or_malformed_metadata_finishes_unknown(self):
        empty = b'\x01\x01\x00\x00'
        for frame in (indication(b''), indication(b'\x10\x01\x00x'), indication(empty * 2),
                      indication(empty)[:-1], indication(empty + b'\x10'), indication(b'\x01\x03\x00x'),
                      indication(b'\x01\x01\x00\x01')):
            with self.subTest(frame=frame):
                self.register_frames = [frame]
                self.assertNotIn('calls', self.check_wait('calls_unknown', 1))

    def test_quiet_interval_expires_on_private_context(self):
        self.assertNotIn('calls', self.check_wait('timeout', 0, '1'))
