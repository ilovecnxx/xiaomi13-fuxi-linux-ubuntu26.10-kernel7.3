import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

DIST = Path(__file__).resolve().parents[2] / 'packaging/fuxi-modem/root/usr/lib/python3/dist-packages'
with patch.object(sys, 'path', [str(DIST), *sys.path]):
    from fuxi_voice_calls import IncompleteVoiceCalls, VoiceIndicationSource
    from fuxi_voice_session import main, select_subscription


def tlv(tag, value):
    return bytes((tag,)) + len(value).to_bytes(2, 'little') + value


def indication(cid, payload, service=9, flags=4, message=0x2e, qmux_flags=0):
    header = bytes((qmux_flags, service, cid, flags, 0, 0)) + message.to_bytes(2, 'little')
    body = header + len(payload).to_bytes(2, 'little') + payload
    return b'\x01' + (len(body) + 2).to_bytes(2, 'little') + body


EMPTY = tlv(1, b'\x00')
CONNECTED = bytes((7, 3, 2, 1, 4, 0, 0))
ENDED = bytes((8, 9, 2, 2, 4, 0, 1))
ONE = tlv(1, b'\x01' + CONNECTED)


class VoiceDiagnosticTest(unittest.TestCase):
    def setUp(self):
        self.indicated = self.enterContext(patch('fuxi_voice_calls.read_indicated_voice_calls'))
        self.query = self.enterContext(patch('fuxi_voice_calls.collect_voice_calls'))
        self.output = self.enterContext(patch('builtins.print'))

    def test_complete_indication_selects_secondary(self):
        self.indicated.return_value = {0: [], 1: [(7, 3, 2, 4, 0)]}
        main()
        self.output.assert_called_once_with('SIM2 VSID=0x11dc5000')
        self.query.assert_not_called()

    def test_partial_indication_replaced_by_whole_query(self):
        self.indicated.side_effect = IncompleteVoiceCalls({0: None, 1: [(7, 3, 2, 4, 0)]})
        self.query.return_value = {0: [], 1: []}
        main()
        self.output.assert_called_once_with('idle')

    def test_complementary_partial_snapshots_still_unknown(self):
        self.indicated.side_effect = IncompleteVoiceCalls({0: None, 1: []})
        self.query.side_effect = IncompleteVoiceCalls({0: [], 1: None})
        with self.assertRaisesRegex(SystemExit, 'Unknown VOICE'):
            main()
        self.output.assert_not_called()

    def test_multiple_conversations_never_replaced_by_fallback(self):
        self.indicated.return_value = {slot: [(7, 3, 2, 4, 0)] for slot in (0, 1)}
        with self.assertRaisesRegex(SystemExit, 'multiple live calls'):
            main()
        self.query.assert_not_called()


class VoiceIndicationTest(unittest.TestCase):
    def setUp(self):
        self.clock = Mock(return_value=10)
        self.source = VoiceIndicationSource(5, clock=self.clock)
        self.device = Mock()
        self.device.connect.side_effect = range(1, 20)
        self.primary = self.bind(0, 7)
        self.secondary = self.bind(1, 8)

    def bind(self, slot, cid):
        client = Mock()
        client.get_device.return_value = self.device
        client.get_service.return_value = 9
        client.get_cid.return_value = cid
        self.source.bind(slot, client)
        signal, callback = self.device.connect.call_args.args
        self.assertEqual(signal, 'indication')
        return callback

    def partial(self):
        with self.assertRaises(IncompleteVoiceCalls) as error:
            self.source.snapshot()
        return error.exception.snapshot

    def test_complete_snapshot_and_session_selection(self):
        self.assertEqual(self.partial(), {0: None, 1: None})
        self.primary(self.device, indication(7, EMPTY, qmux_flags=0x80))
        self.assertEqual(self.partial(), {0: [], 1: None})
        self.secondary(self.device, indication(8, EMPTY))
        self.assertIsNone(select_subscription(self.source.snapshot()))
        records = tlv(1, b'\x02' + ENDED + CONNECTED)
        self.secondary(self.device, indication(8, tlv(0x10, b'private') + records))
        snapshot = self.source.snapshot()
        self.assertEqual(snapshot[1], [(8, 9, 2, 4, 0), (7, 3, 2, 4, 0)])
        snapshot[1].clear()
        self.assertEqual(select_subscription(self.source.snapshot()), 1)

    def test_multiple_live_and_unknown_state(self):
        self.primary(self.device, indication(7, ONE))
        for secondary, reason in ((ONE, 'multiple'), (EMPTY, None)):
            self.secondary(self.device, indication(8, secondary))
            if reason:
                with self.assertRaisesRegex(ValueError, reason):
                    select_subscription(self.source.snapshot())
            else:
                self.assertEqual(select_subscription(self.source.snapshot()), 0)
        for records, reason in ((b'\x02' + CONNECTED + bytes((8,)) + CONNECTED[1:], 'multiple'),
                                (b'\x01\x07\xff' + CONNECTED[2:], 'outside')):
            self.primary(self.device, indication(7, tlv(1, records)))
            with self.assertRaisesRegex(ValueError, reason):
                select_subscription(self.source.snapshot())

    def test_expiry_is_per_slot_and_unrelated_messages_do_not_refresh(self):
        self.primary(self.device, indication(7, EMPTY))
        self.clock.return_value = 14
        self.secondary(self.device, indication(8, EMPTY))
        for kwargs in ({'cid': 8}, {'cid': 255}, {'service': 5}, {'flags': 2}, {'message': 0x2f}):
            self.primary(self.device, indication(payload=ONE, **({'cid': 7} | kwargs)))
        self.primary(Mock(), indication(7, ONE))
        self.assertEqual(self.source.snapshot(), {0: [], 1: []})
        self.clock.return_value = 15
        self.assertEqual(self.partial(), {0: None, 1: []})
        self.primary(self.device, indication(7, ONE))
        self.assertEqual(select_subscription(self.source.snapshot()), 0)
        self.clock.return_value = 19
        self.assertIsNone(self.partial()[1])

    def test_rebind_release_and_cid_reuse(self):
        stale = self.primary
        self.primary(self.device, indication(7, ONE))
        self.secondary(self.device, indication(8, EMPTY))
        self.primary = self.bind(0, 7)
        self.device.disconnect.assert_called_with(1)
        stale(self.device, indication(7, ONE))
        self.assertEqual(self.partial(), {0: None, 1: []})
        moved = self.bind(1, 7)
        self.primary(self.device, indication(7, ONE))
        self.secondary(self.device, indication(8, EMPTY))
        self.assertEqual(self.partial(), {0: None, 1: None})
        moved(self.device, indication(7, EMPTY))
        self.assertEqual(self.partial(), {0: None, 1: []})
        self.source.unbind(1)
        moved(self.device, indication(7, ONE))
        self.assertEqual(self.partial(), {0: None, 1: None})

    def test_malformed_matching_indications_invalidate_only_the_sender(self):
        self.primary(self.device, indication(7, EMPTY))
        payloads = (b'', tlv(0x10, b'\x00'), tlv(1, b''), tlv(1, b'\x01'),
                    tlv(1, b'\x00\x00'), EMPTY + EMPTY, EMPTY + b'\x10', b'\x01\x05\x00\x00')
        frames = [indication(8, payload) for payload in payloads]
        frames.extend((indication(8, EMPTY)[:-1], indication(8, EMPTY)[:11] + b'\x00\x00' + EMPTY))
        for frame in frames:
            with self.subTest(frame=frame):
                self.secondary(self.device, indication(8, EMPTY))
                self.assertIsNone(select_subscription(self.source.snapshot()))
                self.secondary(self.device, frame)
                self.assertEqual(self.partial(), {0: [], 1: None})

    def test_invalid_age_rejected(self):
        for age in (0, -1, float('inf'), float('nan')):
            with self.subTest(age=age), self.assertRaises(ValueError):
                VoiceIndicationSource(age)
