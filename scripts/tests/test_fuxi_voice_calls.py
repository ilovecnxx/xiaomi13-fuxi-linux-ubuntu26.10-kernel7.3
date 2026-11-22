import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

DIST = Path(__file__).resolve().parents[2] / 'packaging/fuxi-modem/root/usr/lib/python3/dist-packages'
with patch.object(sys, 'path', [str(DIST), *sys.path]):
    import fuxi_voice_calls as voice

try:
    import gi
    gi.require_version('Qmi', '1.0')
    gi.require_version('Qrtr', '1.0')
    from gi.repository import GLib, Qmi, Qrtr
except (ImportError, ValueError):
    Qmi = None


def add_tlv(message, tlv, values):
    offset = Qmi.message_tlv_write_init(message, tlv)
    for value in values:
        Qmi.message_tlv_write_guint8(message, value)
    Qmi.message_tlv_write_complete(message, offset)


RECORDS = (2, 7, 2, 2, 1, 4, 1, 0, 8, 3, 0, 2, 3, 0, 1)


@unittest.skipUnless(Qmi, 'requires Qmi/Qrtr GI typelibs')
class VoiceCallsTest(unittest.TestCase):
    def response(self, records=None):
        request = Qmi.message_new(Qmi.Service.VOICE, 7, 1, 0x2f)
        response = Qmi.message_response_new(request, Qmi.ProtocolError.NONE)
        if records is not None:
            add_tlv(response, 0x10, records)
        return response

    def test_wire_fields_match_real_gir_array(self):
        response = self.response(RECORDS)
        getter = getattr(Qmi.MessageVoiceGetAllCallInfoOutput, 'get_call_information', None)
        if getter is None or not getter.get_symbol().endswith('_gir'):
            self.skipTest('requires pointer-array GIR accessor for independent comparison')
        output = Qmi.message_voice_get_all_call_info_response_parse(response)
        expected = [(int(call.id), int(call.state), int(call.type), int(call.mode),
                     int(call.multipart_indicator)) for call in output.get_call_information()]
        api = Mock(wraps=Qmi)
        api.Endian = Qmi.Endian
        actual = voice._call_metadata(api, response, {})
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [(7, 2, 2, 4, 1), (8, 3, 0, 3, 0)])
        self.assertTrue(all(type(value) is int for call in actual for value in call))
        self.assertEqual([args.args[1] for args in api.message_tlv_read_init.call_args_list],
                         [0x02, 0x10])

    def test_only_explicit_empty_array_means_no_calls(self):
        self.assertEqual(voice._call_metadata(Qmi, self.response((0,)), {}), [])
        for invalid in (None, (1,), (0, 1), RECORDS[:-1]):
            with self.subTest(invalid=invalid), self.assertRaises((GLib.Error, ValueError)):
                voice._call_metadata(Qmi, self.response(invalid), {})

    def test_libqmi_wire_id_and_absent_vs_explicit_empty(self):
        request = Qmi.message_new(Qmi.Service.VOICE, 7, 1, 0x2f)
        self.assertEqual(int(Qmi.message_get_service(request)), 9)
        self.assertIn('"Get All Call Info" (0x002F)', Qmi.message_get_printable_full(
            request, Qmi.MessageContext.new(), ''))
        output = Qmi.message_voice_get_all_call_info_response_parse(self.response())
        output.get_result()
        with self.assertRaises(GLib.Error) as missing:
            output.get_call_information()
        self.assertTrue(missing.exception.matches(Qmi.core_error_quark(), Qmi.CoreError.TLVNOTFOUND))
        empty = Qmi.message_voice_get_all_call_info_response_parse(self.response((0,)))
        self.assertEqual(list(empty.get_call_information()), [])

    def collect(self, failing=None, records=((0,), RECORDS), diagnostic=False):
        device = Mock()
        self.events = []
        clients = [Mock(), Mock()]
        for slot, client in enumerate(clients):
            client.get_service.return_value = Qmi.Service.VOICE
            client.get_cid.return_value = slot + 7
            client.get_next_transaction_id.side_effect = (1, 2)

        def complete(*args):
            args[-1](None, object())

        def command(request, context, timeout, cancellable, callback):
            cid = Qmi.message_get_client_id(request)
            slot = cid - 7
            msg = Qmi.message_get_message_id(request)
            self.assertEqual(Qmi.message_get_service(request), Qmi.Service.VOICE)
            if msg == 0x44:
                offset, length = Qmi.message_tlv_read_init(request, 1)
                self.assertEqual(length, 1)
                self.assertEqual(Qmi.message_tlv_read_guint8(request, offset, 0)[2], slot)
                self.events.append(('bind', slot))
            else:
                self.assertEqual(msg, 0x2f)
                self.events.append(('query', slot))
            failed = self.events[-1] == failing
            error = Qmi.ProtocolError.INTERNAL if failed else Qmi.ProtocolError.NONE
            response = Qmi.message_response_new(request, error)
            if (failing == ('header', slot) and msg == 0x2f or
                    failing == ('bind-header', slot) and msg == 0x44):
                response = Qmi.message_response_new(Qmi.message_new(Qmi.Service.VOICE, cid, 3, msg), error)
            if msg == 0x2f and not failed and records[slot] is not None:
                add_tlv(response, 0x10, records[slot])
            callback(None, response)

        def release(client, flags, timeout, cancel, done):
            self.events.append(('release', clients.index(client)))
            self.assertEqual(flags, Qmi.DeviceReleaseClientFlags.RELEASE_CID)
            complete(done)

        def close_finish(result):
            self.events.append(('close',))
            return failing != ('close',)

        device.command_full.side_effect = command
        device.command_full_finish.side_effect = lambda result: result
        device.allocate_client_finish.side_effect = clients
        device.release_client.side_effect = release
        device.release_client_finish.side_effect = lambda result: self.events[-1] != failing
        device.close_async.side_effect = complete
        device.close_finish.side_effect = close_finish
        device.open.side_effect = complete
        device.open_finish.return_value = True
        device.allocate_client.side_effect = complete
        with patch.object(Qrtr.Bus, 'new', complete), patch.object(Qrtr.Bus, 'new_finish'), \
             patch.object(Qmi.Device, 'new_from_node', complete), \
             patch.object(Qmi.Device, 'new_from_node_finish', return_value=device):
            if diagnostic:
                self.evidence = {}
                return voice._collect_voice_calls(self.evidence)
            return voice.collect_voice_calls()

    def test_both_confirmed_before_their_queries(self):
        self.assertEqual(self.collect(), {0: [], 1: [(7, 2, 2, 4, 1), (8, 3, 0, 3, 0)]})
        self.assertEqual(self.events, [('bind', 0), ('query', 0), ('release', 0),
                                       ('bind', 1), ('query', 1), ('release', 1), ('close',)])

    def test_failures_release_and_close(self):
        for failure in [('bind', 0), ('bind', 1), ('bind-header', 1), ('query', 1), ('release', 0),
                        ('header', 0), ('close',)]:
            with self.subTest(failure=failure):
                with self.assertRaises(RuntimeError):
                    self.collect(failure)
                self.assertEqual(self.events[-1], ('close',))
                self.assertIn(('release', 0), self.events)
                self.assertIn(('release', 1), self.events)
                if failure[0] in ('bind', 'bind-header'):
                    self.assertNotIn(('query', failure[1]), self.events)

    def test_unknown_preserved_independently_for_each_slot(self):
        for first, second in ((None, RECORDS), ((0,), None), (None, None)):
            with self.subTest(first=first, second=second):
                with self.assertRaises(voice.IncompleteVoiceCalls) as incomplete:
                    self.collect(records=(first, second), diagnostic=True)
                for slot, records in enumerate((first, second)):
                    self.assertIn(('query', slot), self.events)
                    entry = self.evidence[slot]
                    self.assertEqual(entry['response_service_cid_txn_id'], [9, 7 + slot, 2, 47])
                    self.assertEqual(entry['result'], [0, 0])
                    calls = incomplete.exception.snapshot[slot]
                    if records is None:
                        self.assertIsNone(calls)
                        self.assertEqual(entry['tlv_0x10'], 'absent')
                        self.assertEqual(entry['state'], 'unknown')
                        self.assertNotIn('count', entry)
                    else:
                        self.assertEqual(len(calls), records[0])
                        self.assertEqual(entry['count'], records[0])
                        self.assertEqual(entry['state'], 'known')
