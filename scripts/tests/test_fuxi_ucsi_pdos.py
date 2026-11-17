import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock


SPEC = importlib.util.spec_from_file_location(
    'fuxi_ucsi_pdos', Path(__file__).resolve().parents[1] / 'fuxi-ucsi-pdos.py')
pd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pd)

EVENT = ' task-123 [000] 1.0: ucsi_run_command: GET_PDOS -> OK (err=0)\n'
STATS = 'overrun: 0\ncommit overrun: 0\ndropped events: 0\n'


def traces(length):
    return {'all': EVENT, **{str(n): EVENT if n == length else ''
                            for n in pd.LENGTHS}}


class UcsiPdosTests(unittest.TestCase):
    def test_length_requires_one_unfiltered_and_one_filtered_completion(self):
        for length in pd.LENGTHS:
            self.assertEqual(pd.verified_length(traces(length), [STATS]), length)
        for broken in (traces(20), {**traces(4), 'all': ''},
                       {**traces(4), 'all': EVENT * 2},
                       {**traces(4), '8': EVENT}, {**traces(4), '4': EVENT * 2}):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                pd.verified_length(broken, [STATS])

    def test_loss_or_missing_counters_refuse_decode(self):
        for stats in ([], ['overrun: 0\n'],
                      [STATS.replace('overrun: 0', 'overrun: 1', 1)],
                      [STATS, STATS.replace('dropped events: 0', 'dropped events: 1')]):
            with self.subTest(stats=stats), self.assertRaises(ValueError):
                pd.verified_length(traces(16), stats)

    def test_response_word_order_and_size(self):
        self.assertEqual(pd.response_bytes('0x' + '0' * 40 + '0a01912c')[:4],
                         bytes.fromhex('2c91010a'))
        for value in ('0x2d12c', '0x' + 'g' * 48, '0x' + '0' * 50):
            with self.assertRaises(ValueError):
                pd.response_bytes(value)

    def test_commands_only_encode_partner_source_get(self):
        self.assertEqual(pd.source_command(1, 0, 4), 0x700810010)
        self.assertEqual(pd.source_command(1, 4, 3), 0x604810010)
        for args in ((0, 0, 4), (128, 0, 4), (1, 8, 3), (1, 0, 5)):
            with self.assertRaises(ValueError):
                pd.source_command(*args)

    def test_fixed_pps_and_other_pdos_are_capabilities_only(self):
        fixed = pd.decode_pdo(0x2d12c)
        self.assertEqual((fixed['voltage_mv'], fixed['max_current_ma']), (9000, 3000))
        pps = pd.decode_pdo(3 << 30 | 110 << 17 | 33 << 8 | 60)
        self.assertEqual((pps['kind'], pps['min_voltage_mv'], pps['max_voltage_mv'],
                          pps['max_current_ma']), ('pps', 3300, 11000, 3000))
        self.assertEqual(pd.decode_pdo(1 << 30)['kind'], 'battery')
        self.assertEqual(pd.decode_pdo(2 << 30)['kind'], 'variable')
        self.assertEqual(pd.decode_pdo(0xd0000000)['kind'], 'unsupported_apdo')
        with self.assertRaises(ValueError):
            pd.decode_pdo(0)

    def test_short_first_page_does_not_read_stale_tail(self):
        reader = Mock()
        capability = bytes.fromhex('46400000011000') + bytes(9)
        reader.query.side_effect = [capability, (0x1912c).to_bytes(4, 'little')]
        result = pd.read_source(reader, 1)
        self.assertEqual(reader.query.call_count, 2)
        self.assertEqual(len(result['pdos']), 1)
        self.assertTrue(result['pdo_details_advertised'])
        self.assertFalse(result['active_contract_verified'])
        self.assertFalse(result['fast_charge_verified'])

    def test_full_page_then_empty_tail_is_exactly_four_pdos(self):
        reader = Mock()
        reader.query.side_effect = [bytes(4) + b'\x01' + bytes(11),
                                    (0x1912c).to_bytes(4, 'little') * 4, b'']
        result = pd.read_source(reader, 1)
        self.assertEqual(reader.query.call_count, 3)
        self.assertEqual(reader.query.call_args.args, (0x604810010, 12))
        self.assertEqual(len(result['pdos']), 4)
        self.assertFalse(result['pdo_details_advertised'])

    def test_capability_length_and_connector_must_match(self):
        for capability in (bytes(12), bytes(16)):
            reader = Mock()
            reader.query.return_value = capability
            with self.assertRaises(ValueError):
                pd.read_source(reader, 1)
            self.assertEqual(reader.query.call_count, 1)

    def test_instance_is_removed_when_query_raises(self):
        path = MagicMock()
        with self.assertRaisesRegex(RuntimeError, 'query failed'):
            with pd.trace_instance(path):
                raise RuntimeError('query failed')
        path.mkdir.assert_called_once()
        path.rmdir.assert_called_once()


if __name__ == '__main__':
    unittest.main()
