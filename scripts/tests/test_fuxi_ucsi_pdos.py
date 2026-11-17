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


def status(rdo=0x130320c8, flags=11):
    return bytes(2) + flags.to_bytes(2, 'little') + rdo.to_bytes(4, 'little') + b'\x01'


def contract_reader(before=None, after=None, pdo=0x1912c):
    reader = Mock()
    reader.query.side_effect = [status() if before is None else before,
                               bytes(4) + b'\x01' + bytes(11),
                               pdo.to_bytes(4, 'little'),
                               status() if after is None else after]
    return reader


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

    def test_commands_encode_only_supported_get_targets(self):
        self.assertEqual(pd.pdo_command(1, 0, 4), 0x700810010)
        self.assertEqual(pd.pdo_command(1, 4, 3), 0x604810010)
        self.assertEqual(pd.pdo_command(1, 0, 4, 'local-sink'), 0x300010010)
        self.assertEqual(pd.pdo_command(1, 4, 3, 'local-sink'), 0x204010010)
        for args in ((0, 0, 4), (128, 0, 4), (1, 8, 3), (1, 0, 5)):
            with self.assertRaises(ValueError):
                pd.pdo_command(*args)
        with self.assertRaises(ValueError):
            pd.pdo_command(1, 0, 4, 'set-contract')

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
        result = pd.read_pdos(reader, 1)
        self.assertEqual(reader.query.call_count, 2)
        self.assertEqual(len(result['pdos']), 1)
        self.assertTrue(result['pdo_details_advertised'])
        self.assertFalse(result['active_contract_verified'])
        self.assertFalse(result['fast_charge_verified'])

    def test_full_page_then_empty_tail_is_exactly_four_pdos(self):
        reader = Mock()
        reader.query.side_effect = [bytes(4) + b'\x01' + bytes(11),
                                    (0x1912c).to_bytes(4, 'little') * 4, b'']
        result = pd.read_pdos(reader, 1)
        self.assertEqual(reader.query.call_count, 3)
        self.assertEqual(reader.query.call_args.args, (0x604810010, 12))
        self.assertEqual(len(result['pdos']), 4)
        self.assertFalse(result['pdo_details_advertised'])

    def test_capability_length_and_connector_must_match(self):
        for capability in (bytes(12), bytes(16)):
            reader = Mock()
            reader.query.return_value = capability
            with self.assertRaises(ValueError):
                pd.read_pdos(reader, 1)
            self.assertEqual(reader.query.call_count, 1)

    def test_local_sink_pagination_and_operational_fields(self):
        reader = Mock()
        reader.query.side_effect = [bytes(4) + b'\x01' + bytes(11),
                                    (0x1912c).to_bytes(4, 'little') * 4,
                                    (0x2d12c).to_bytes(4, 'little')]
        result = pd.read_pdos(reader, 1, 'local-sink')
        self.assertEqual([call.args for call in reader.query.call_args_list],
                         [(6, 16), (0x300010010, 16), (0x204010010, 12)])
        self.assertEqual(result['target'], 'local-sink')
        self.assertEqual(result['pdos'][-1]['voltage_mv'], 9000)
        self.assertEqual(result['pdos'][-1]['operational_current_ma'], 3000)
        self.assertNotIn('max_current_ma', result['pdos'][-1])
        self.assertFalse(result['active_contract_verified'])

    def test_empty_local_reply_does_not_imply_five_volt_limit(self):
        reader = Mock()
        reader.query.side_effect = [bytes(4) + b'\x01' + bytes(11), b'']
        result = pd.read_pdos(reader, 1, 'local-sink')
        self.assertEqual(result['pdos'], [])
        self.assertEqual(reader.query.call_count, 2)

    def test_sink_battery_and_pps_fields(self):
        battery = pd.decode_pdo(1 << 30 | 20, source=False)
        self.assertEqual(battery['operational_power_mw'], 5000)
        pps = pd.decode_pdo(3 << 30 | 110 << 17 | 33 << 8 | 60, source=False)
        self.assertEqual(pps['max_current_ma'], 3000)
        self.assertNotIn('operational_current_ma', pps)

    def test_instance_is_removed_when_query_raises(self):
        path = MagicMock()
        with self.assertRaisesRegex(RuntimeError, 'query failed'):
            with pd.trace_instance(path):
                raise RuntimeError('query failed')
        path.mkdir.assert_called_once()
        path.rmdir.assert_called_once()

    def test_nine_byte_status_length_is_opt_in_and_not_a_pdo_length(self):
        nine = {**traces(9), '9': EVENT}
        self.assertEqual(pd.verified_length(nine, [STATS], (*pd.LENGTHS, 9)), 9)
        with self.assertRaises(ValueError):
            pd.verified_length(nine, [STATS])
        for pages in ([bytes(9)], [(0x1912c).to_bytes(4, 'little') * 4, bytes(9)]):
            reader = Mock()
            reader.query.side_effect = [bytes(4) + b'\x01' + bytes(11), *pages]
            with self.assertRaisesRegex(ValueError, 'Invalid PDO'):
                pd.read_pdos(reader, 1)

    def test_reported_fixed_request_is_not_measured_power(self):
        reader = contract_reader()
        report = pd.read_contract(reader, 1)
        self.assertEqual([call.args for call in reader.query.call_args_list],
                         [(0x10012, 16), (6, 16), (0x700810010, 16), (0x10012, 16)])
        self.assertEqual(report['result'], 'firmware_contract_read')
        request = report['reported_request']
        self.assertEqual((request['voltage_mv'], request['operating_current_ma'],
                          request['limit_current_ma']), (5000, 2000, 2000))
        self.assertEqual(request['limit_kind'], 'maximum')
        self.assertFalse(request['capability_mismatch'])
        self.assertFalse(report['active_contract_verified'])
        self.assertFalse(report['fast_charge_verified'])

    def test_changed_disconnected_source_or_non_pd_status_is_unresolved(self):
        for after in (status(rdo=0x13025896), status(flags=3), status(flags=27),
                      status(flags=9)):
            for before in (status(), after):
                with self.subTest(before=before, after=after):
                    report = pd.read_contract(contract_reader(before, after), 1)
                    if before == after == status(rdo=0x13025896):
                        self.assertEqual(report['result'], 'firmware_contract_read')
                    else:
                        self.assertEqual(report['result'], 'contract_unresolved')

    def test_truncated_status_or_invalid_connector_stops_before_pdo_queries(self):
        reader = contract_reader(before=bytes(8))
        with self.assertRaisesRegex(ValueError, '9-byte'):
            pd.read_contract(reader, 1)
        self.assertEqual(reader.query.call_count, 1)
        for connector in (0, 128):
            with self.assertRaises(ValueError):
                pd.read_contract(reader, connector)
        self.assertEqual(reader.query.call_count, 1)

    def test_unmapped_or_inconsistent_rdo_does_not_establish_contract(self):
        for rdo, pdo in ((0, 0x1912c), (0x230320c8, 0x1912c), (0x930320c8, 0x1912c),
                         (0x130640c8, 0x1912c), (0x13032064, 0x1912c),
                         (0x130320c8, 2 << 30 | 300)):
            report = pd.read_contract(contract_reader(status(rdo), status(rdo), pdo), 1)
            self.assertEqual(report['result'], 'contract_unresolved')
            self.assertIsNone(report['reported_request'])

    def test_give_back_and_capability_mismatch_flags_are_preserved(self):
        raw = status(rdo=0x1f032064)
        report = pd.read_contract(contract_reader(raw, raw), 1)
        request = report['reported_request']
        self.assertEqual(report['result'], 'firmware_contract_read')
        self.assertEqual((request['limit_kind'], request['limit_current_ma']), ('minimum', 1000))
        self.assertTrue(request['capability_mismatch'])

    def test_mismatch_can_request_a_maximum_above_source_capacity(self):
        raw = status(rdo=0x17032190)
        report = pd.read_contract(contract_reader(raw, raw), 1)
        self.assertEqual(report['result'], 'firmware_contract_read')
        self.assertEqual(report['reported_request']['limit_current_ma'], 4000)
        self.assertTrue(report['reported_request']['capability_mismatch'])


if __name__ == '__main__':
    unittest.main()
