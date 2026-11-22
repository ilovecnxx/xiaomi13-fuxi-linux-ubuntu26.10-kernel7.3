import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch


USR = Path(__file__).resolve().parents[2] / 'packaging/fuxi-modem/root/usr'
spec = importlib.util.spec_from_file_location('fuxi_sub2_inbox', USR / 'lib/python3/dist-packages/fuxi_sub2_inbox.py')
inbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inbox)


def deliver(data, udl=None, flags=0, dcs=0, address=b'\x03\x91\x21\xf3'):
    return (bytes([flags]) + address + bytes([0, dcs]) + bytes.fromhex('62112221436500') +
            bytes([len(data) if udl is None else udl]) + data)


def line(pdu, fmt=6):
    return json.dumps(dict(t='2026-11-22 12:34:56', txn=42, format=fmt, pdu=pdu.hex())).encode() + b'\n'


class InboxTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'fictional.jsonl'
        self.enterContext(patch.object(inbox, 'SMS_FILE', str(self.path)))
        self.enterContext(patch.object(inbox.os, 'geteuid', return_value=0))
        real_fstat = os.fstat

        def root_owned(fd):
            fields = list(real_fstat(fd))
            fields[4] = 0
            return os.stat_result(fields)

        self.enterContext(patch.object(inbox.os, 'fstat', side_effect=root_owned))

    def store(self, data):
        self.path.write_bytes(data)
        self.path.chmod(0o600)

    def call(self, cursor=0, limit=50, raw=None):
        request = json.dumps(dict(version=1, cursor=cursor, limit=limit)).encode() if raw is None else raw
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = inbox.run_inbox(io.BytesIO(request))
        return status, json.loads(stdout.getvalue())

    def test_existing_dispatcher_and_append_after_eof(self):
        helper = runpy.run_path(str(USR / 'sbin/fuxi-sim2-telephony'))
        with patch.dict('sys.modules', fuxi_sub2_inbox=inbox), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(helper['main'](['sms-inbox'], io.BytesIO(b'{"version":1,"cursor":0,"limit":1}')), 0)
        first = line(deliver(bytes.fromhex('e8329bfd06'), 5))
        second = line(deliver('你好'.encode('utf-16-be'), dcs=8))
        self.store(first + second[:-1])
        status, page = self.call(limit=1)
        self.assertEqual(status, 0)
        self.assertEqual(page['items'], [dict(id=0, received_at='2026-11-22 12:34:56',
                                               sender='+123', encoding='gsm7', part=None, text='hello')])
        self.assertEqual(page['next_cursor'], len(first))
        status, page = self.call(len(first))
        self.assertEqual((status, page['next_cursor'], page['items'], page['more']), (0, len(first), [], False))
        with self.path.open('ab') as output:
            output.write(b'\n')
        status, page = self.call(len(first))
        self.assertEqual((status, page['items'][0]['id'], page['items'][0]['text']), (0, len(first), '你好'))
        self.assertEqual((page['more'], page['next_cursor']), (False, len(first + second)))
        self.assertEqual(self.path.read_bytes(), first + second)

    def test_concatenation_with_gsm_fill_extensions_and_16bit_ref_binary(self):
        header = bytes.fromhex('050003610302')
        data = header + ((27 | 101 << 7) << 1).to_bytes(2, 'little')
        first = line(deliver(data, 9, 0x40, address=b'\x02\xd0A'))
        second = line(deliver(bytes.fromhex('090701ff08041234030100ff'), flags=0x40, dcs=4))
        self.store(first + second)
        _, page = self.call(limit=1)
        first_item = page['items'][0]
        self.assertEqual((first_item['sender'], first_item['text']), ('A', '€'))
        self.assertEqual(first_item['part'], dict(reference=97, reference_bits=8, total=3, index=2))
        _, page = self.call(page['next_cursor'], 1)
        second_item = page['items'][0]
        self.assertEqual(second_item['part'], dict(reference=4660, reference_bits=16, total=3, index=1))
        self.assertEqual(second_item['data_hex'], '00ff')

    def test_message_from_paged_ids_out_of_order_with_duplicate_and_missing(self):
        def segment(index, text, address=b'\x03\x91\x21\xf3', port=80):
            header = bytes([9, 0, 3, 61, 2, index, 4, 2, port, 1])
            return line(deliver(header + text.encode('utf-16-be'), flags=0x40, dcs=8, address=address))

        data = [segment(2, '好'), segment(1, '你'), segment(1, '你'), segment(1, '另'),
                segment(1, '你', port=81), segment(1, '你', address=b'\x03\x91\x22\xf3'),
                segment(1, '你').replace(b'11-22', b'11-24')]
        self.store(b''.join(data))
        ids = [item['id'] for item in self.call()[1]['items']]
        def assemble(indices):
            request = json.dumps(dict(version=1, ids=[ids[index] for index in indices])).encode()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(inbox.run_message(io.BytesIO(request)), 0)
            return json.loads(output.getvalue())
        self.assertEqual(assemble([0])['missing'], [1])
        self.assertEqual(assemble([0, 2, 1])['text'], '你好')
        for mixed in ([0, 1, 3], [0, 4], [0, 5], [0, 6]):
            reply = assemble(mixed)
            self.assertEqual(reply['result'], 'conflict')
            self.assertNotIn('text', reply)

    def test_message_request_must_reference_complete_private_records(self):
        first = line(deliver(bytes.fromhex('05000301010100ff'), flags=0x40, dcs=4))
        self.store(first + first[:-1])
        self.assertEqual(inbox.assemble_message([0])['data_hex'], '00ff')
        for offset in (3, len(first), 2**60):
            with self.assertRaises(ValueError):
                inbox.assemble_message([offset])
        self.path.chmod(0o644)
        with self.assertRaises(OSError):
            inbox.assemble_message([0])
        for ids in ([], [True], [0, 0], [0] * 511):
            raw = json.dumps(dict(version=1, ids=ids)).encode()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(inbox.run_message(io.BytesIO(raw)), 2)

    def test_invalid_records_consume_slots_and_withhold_raw_contents(self):
        valid = deliver(b'A', 1)
        records = [line(valid[:size]) for size in range(len(valid))]
        records.extend([b'bad secret\n', b'[]\n', b'\xff\n', line(valid + b'\x00'),
                        line(deliver(b'\x00', dcs=8)), line(deliver(b'\x1b', 1)),
                        line(deliver(bytes.fromhex('050003010302'), 6, 0x40)),
                        line(deliver(bytes.fromhex('050003010304'), flags=0x40, dcs=8)),
                        line(deliver(bytes.fromhex('020003'), flags=0x40, dcs=4)),
                        line(deliver(b'', address=b'\x03\x91\x2f\xf3'))])
        self.store(b''.join(records) + line(valid, fmt=0) + line(valid))
        _, page = self.call()
        offset = 0
        for raw, item in zip(records, page['items']):
            self.assertEqual(item, dict(id=offset, error='malformed'))
            offset += len(raw)
        self.assertEqual(page['items'][-2], dict(id=offset, error='unsupported'))
        self.assertEqual(page['items'][-1]['text'], 'A')

    def test_large_line_bounded_skip_and_resume(self):
        corrupted = b'x' * (5 * inbox.SCAN_BLOCK + 3) + b'\n'
        valid = line(deliver(b'', dcs=8))
        self.store(corrupted + valid)
        cursor = 0
        items = []
        for _ in range(3):
            status, page = self.call(cursor)
            self.assertEqual(status, 0)
            self.assertLessEqual(page['next_cursor'] - cursor, 2 * inbox.SCAN_BLOCK)
            self.assertGreater(page['next_cursor'], cursor)
            cursor = page['next_cursor']
            items.extend(page['items'])
        self.assertEqual([item['id'] for item in items], [0, len(corrupted)])
        self.assertEqual(items[0]['error'], 'malformed')
        self.assertFalse(page['more'])

    def test_request_limits_and_duplicate_fields_before_open(self):
        with patch.object(inbox.os, 'open', side_effect=AssertionError):
            for cursor, limit in ((True, 1), (0, False), (-1, 1), (2**63, 1), (0, 0), (0, 51), ('0', 1)):
                self.assertEqual(self.call(cursor, limit)[0], 2)
            for raw in (b' ' * 257, b'{}', b'\xff', b'{"version":1,"cursor":0,"limit":1,"path":"fixture"}',
                        b'{"version":1,"cursor":0,"limit":2,"limit":1}'):
                self.assertEqual(self.call(raw=raw)[1]['error'], 'invalid_request')
            with patch.object(inbox.os, 'geteuid', return_value=1000):
                self.assertEqual(self.call()[1]['error'], 'permission_denied')

    def test_file_type_permissions_and_cursor_boundary(self):
        self.assertEqual(self.call()[1]['items'], [])
        self.assertEqual(self.call(1)[0], 2)
        self.store(b'x' * (inbox.SCAN_BLOCK - 3) + b'\n{}\n')
        for cursor in (1, inbox.SCAN_BLOCK, self.path.stat().st_size + 1):
            self.assertEqual(self.call(cursor)[1]['error'], 'invalid_request')
        self.path.chmod(0o644)
        self.assertEqual(self.call()[1]['error'], 'storage_unavailable')
        self.path.unlink()
        self.path.symlink_to(self.path.parent / 'missing')
        self.assertEqual(self.call()[1]['error'], 'storage_unavailable')
        self.path.unlink()
        os.mkfifo(self.path, 0o600)
        self.assertEqual(self.call()[1]['error'], 'storage_unavailable')


if __name__ == '__main__':
    unittest.main()
