import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch


SOURCE = (Path(__file__).resolve().parents[2] /
          "packaging/fuxi-modem/root/usr/sbin/fuxi-qmi-sub2-send.py")
LIB = SOURCE.parents[1] / "lib/python3/dist-packages"
with patch.object(sys, "path", [str(LIB), *sys.path]):
    import fuxi_sub2_sms as sender


class SendPduTest(unittest.TestCase):
    def test_known_submit_vectors(self):
        header = bytes.fromhex("00 01 00 03 91 21 f3 00")
        for text, encoded in (("hello", "00 05 e8 32 9b fd 06"),
                              ("@£", "00 02 80 00"), ("€", "00 02 9b 32"),
                              ("中€", "08 04 4e 2d 20 ac"), ("\x1b", "08 02 00 1b")):
            with self.subTest(text=text):
                self.assertEqual(sender.build_pdu("+123", text), header + bytes.fromhex(encoded))

    def test_single_sms_limits(self):
        for text, coding, length_field in (("@" * 160, 0, 160), ("^" * 80, 0, 160),
                                           ("中" * 70, 8, 140)):
            with self.subTest(char=text[0]):
                payload = sender.build_pdu("+123", text)[8:]
                self.assertEqual(payload[:2], bytes([coding, length_field]))
                self.assertEqual(len(payload[2:]), 140)
                with self.assertRaisesRegex(ValueError, "too long"):
                    sender.build_pdu("+123", text + "A")

    def test_dry_run_reproducing_original_rejection(self):
        result = subprocess.run([sys.executable, "-S", str(SOURCE), "--dry-run", "+123", "@" * 71],
                                env={**os.environ, "PYTHONPATH": str(LIB)},
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, "pdu_len=73\n")
        self.assertEqual(result.stderr, "")

    def multipart_payloads(self, text, coding, count):
        with patch.object(sender.secrets, "randbelow", return_value=0x97) as random_ref:
            pdus = sender.build_pdus("+123", text)
        random_ref.assert_called_once_with(256)
        self.assertEqual(len(pdus), count)
        for index, pdu in enumerate(pdus, 1):
            self.assertEqual(pdu[:9], bytes.fromhex("00 41 00 03 91 21 f3 00") + bytes([coding]))
            self.assertEqual(pdu[10:16], bytes([5, 0, 3, 0x97, count, index]))
            self.assertLessEqual(len(pdu[10:]), 140)
        return [(pdu[9], pdu[16:]) for pdu in pdus]

    def test_gsm_fill_bit_and_extension_at_boundary(self):
        for text, expected in (("A" * 161, [b"A" * 153, b"A" * 8]),
                               ("A" * 152 + "€" + "B" * 7, [b"A" * 152, b"\x1be" + b"B" * 7]),
                               ("^" * 81, [b"\x1b\x14" * 76, b"\x1b\x14" * 5])):
            with self.subTest(text=text[:3]):
                parts = self.multipart_payloads(text, 0, 2)
                for (udl, data), septets in zip(parts, expected):
                    self.assertEqual(udl, len(septets) + 7)
                    self.assertEqual(len(data) + 6, (udl * 7 + 7) // 8)
                    packed = int.from_bytes(data, "little")
                    self.assertEqual(packed & 1, 0)
                    decoded = bytes((packed >> (1 + 7 * index)) & 127 for index in range(udl - 7))
                    self.assertEqual(decoded, septets)

    def test_utf16_boundary_preserves_surrogate_pairs(self):
        single = sender.build_pdus("+123", "😀" * 35)
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0][8:], b"\x08\x8c" + ("😀" * 35).encode("utf-16-be"))
        for chunks in (("中" * 67, "中" * 4), ("A" * 66, "😀" + "中" * 3), ("😀" * 33, "😀" * 3)):
            with self.subTest(chunks=chunks):
                parts = self.multipart_payloads("".join(chunks), 8, 2)
                for (udl, data), chunk in zip(parts, chunks):
                    self.assertEqual(udl, len(data) + 6)
                    self.assertEqual(data.decode("utf-16-be"), chunk)

    def test_max_segments(self):
        text = "A" * (153 * 255)
        self.assertEqual(len(sender.build_pdus("123", text)), 255)
        with self.assertRaisesRegex(ValueError, "too many"):
            sender.build_pdus("123", text + "A")


class SendApiTest(unittest.TestCase):
    def setUp(self):
        self.glib, self.qmi, self.qrtr = Mock(), Mock(), Mock()
        self.dev = self.qmi.Device.new_from_node_finish.return_value
        self.wms = self.dev.allocate_client_finish.return_value
        self.out = self.wms.raw_send_finish.return_value
        self.out.get_result.return_value = None
        self.out.get_message_id.return_value = 0
        self.qmi.message_tlv_read_init.side_effect = [(0, 4), (0, 4), (0, 1)]
        self.qmi.message_tlv_read_guint16.side_effect = [(True, 2, 0), (True, 4, 0)]
        self.qmi.message_tlv_read_guint8.side_effect = [(True, index + 1, 0) for index in range(4)] + [(True, 1, 1)]
        for start in (self.qrtr.Bus.new, self.qmi.Device.new_from_node, self.dev.open,
                      self.dev.allocate_client, self.dev.command_full, self.wms.raw_send,
                      self.dev.release_client, self.dev.close_async):
            start.side_effect = lambda *args: args[-1](None, object())
        self.libraries = self.enterContext(patch.object(sender, "_libraries", return_value=(
            self.glib, self.qmi, self.qrtr)))
        self.request = b'{"version":1,"number":"+12345","text":"private"}'

    def reply(self, request=None):
        output = io.StringIO()
        with redirect_stdout(output):
            code = sender.run_send(io.BytesIO(self.request if request is None else request))
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertNotIn("12345", output.getvalue())
        self.assertNotIn("private", output.getvalue())
        return code, json.loads(output.getvalue())

    def assert_cleanup(self):
        self.dev.release_client_finish.assert_called_once()
        self.dev.close_finish.assert_called_once()
        self.glib.MainContext.new.return_value.pop_thread_default.assert_called_once()

    def test_submitted_only_after_binding_and_matching_readback(self):
        self.assertEqual(self.reply(), (0, {"version": 1, "slot": 2, "status": "submitted", "error": None,
                                            "segments_total": 1, "segments_submitted": 1, "segments_uncertain": 0}))
        self.assertEqual([call.args[-1] for call in self.qmi.message_new.call_args_list], [0x004c, 0x0058])
        inp = self.qmi.MessageWmsRawSendInput.return_value
        inp.set_sms_on_ims.assert_called_once_with(True)
        self.wms.raw_send.assert_called_once()
        self.assert_cleanup()

    def test_bind_denied(self):
        self.qmi.message_tlv_read_guint16.side_effect = [(True, 2, 1), (True, 4, 3)]
        code, reply = self.reply()
        self.assertEqual((code, reply["status"], reply["error"]), (3, "not_submitted", "binding_unverified"))
        self.wms.raw_send.assert_not_called()
        self.assert_cleanup()

    def test_wrong_readback(self):
        self.qmi.message_tlv_read_guint8.side_effect = [(True, 1, 0)] * 5
        self.assertEqual(self.reply()[0], 3)
        self.wms.raw_send.assert_not_called()
        self.assert_cleanup()

    def test_readback_from_different_transaction_prevents_submission(self):
        self.qmi.message_get_transaction_id.side_effect = [1, 1, 1, 2]
        code, reply = self.reply()
        self.assertEqual((code, reply['error']), (3, 'binding_unverified'))
        self.wms.raw_send.assert_not_called()
        self.assert_cleanup()

    def test_finish_timeout_and_cleanup_failure(self):
        self.wms.raw_send_finish.side_effect = TimeoutError("private")
        self.dev.release_client_finish.side_effect = RuntimeError("private")
        code, reply = self.reply()
        self.assertEqual((code, reply["status"], reply["error"], reply["warning"]),
                         (5, "uncertain", "confirmation_missing", "cleanup_failed"))
        self.wms.raw_send.assert_called_once()
        self.assert_cleanup()

    def test_protocol_error_from_result(self):
        class ProtocolError(Exception):
            code = 52

            def matches(self, domain, code):
                return domain == 12 and code == self.code

        self.glib.Error = ProtocolError
        self.qmi.protocol_error_quark.return_value = 12
        self.out.get_result.side_effect = [None, ProtocolError("private")]
        code, reply = self.reply(self.request.replace(b'private', b'A' * 310))
        self.assertEqual((code, reply["status"], reply["error"]), (4, "rejected", "modem_rejected"))
        self.assertEqual((reply["segments_total"], reply["segments_submitted"], reply["segments_uncertain"]), (3, 1, 0))
        self.out.get_message_id.assert_called_once()
        self.assertEqual(self.wms.raw_send.call_count, 2)
        self.assert_cleanup()

    def test_multipart_sent_in_order_after_confirmation(self):
        order = Mock()
        order.attach_mock(self.wms.raw_send, "send")
        order.attach_mock(self.out.get_message_id, "confirm")
        code, reply = self.reply(self.request.replace(b'private', b'A' * 310))
        self.assertEqual((code, reply["status"], reply["segments_total"], reply["segments_submitted"],
                          reply["segments_uncertain"]), (0, "submitted", 3, 3, 0))
        self.assertEqual([call[0] for call in order.mock_calls], ["send", "confirm"] * 3)
        messages = self.qmi.MessageWmsRawSendInput.return_value.set_raw_message_data.call_args_list
        self.assertEqual([call.args[1][16] for call in messages], [1, 2, 3])
        self.assertEqual(self.dev.allocate_client.call_count, 1)
        self.assert_cleanup()

    def test_second_segment_unknown_stops_batch(self):
        self.out.get_message_id.side_effect = [123, ValueError("private")]
        code, reply = self.reply(self.request.replace(b'private', b'A' * 310))
        self.assertEqual((code, reply["status"], reply["segments_total"], reply["segments_submitted"],
                          reply["segments_uncertain"]), (5, "uncertain", 3, 1, 1))
        self.assertEqual(self.wms.raw_send.call_count, 2)
        self.assert_cleanup()

    def test_second_segment_prepare_failure(self):
        self.qmi.MessageWmsRawSendInput.side_effect = [Mock(), RuntimeError("private")]
        code, reply = self.reply(self.request.replace(b'private', b'A' * 310))
        self.assertEqual((code, reply["status"], reply["segments_total"], reply["segments_submitted"],
                          reply["segments_uncertain"]), (6, "partial", 3, 1, 0))
        self.wms.raw_send.assert_called_once()
        self.assert_cleanup()

    def test_missing_id(self):
        self.out.get_message_id.side_effect = ValueError("private")
        self.assertEqual(self.reply()[0], 5)
        self.wms.raw_send.assert_called_once()
        self.assert_cleanup()

    def test_release_failure_preserves_submission(self):
        self.dev.release_client_finish.return_value = False
        code, reply = self.reply()
        self.assertEqual((code, reply["status"], reply["warning"]), (0, "submitted", "cleanup_failed"))
        self.assert_cleanup()

    def test_allocate_failure_closes_device(self):
        self.dev.allocate_client_finish.side_effect = RuntimeError("private")
        code, reply = self.reply()
        self.assertEqual((code, reply["error"]), (3, "backend_unavailable"))
        self.dev.release_client.assert_not_called()
        self.dev.close_finish.assert_called_once()
        self.wms.raw_send.assert_not_called()

    def test_invalid_requests_fail_before_qmi(self):
        for data in (b'{}', b'\xff', self.request + b'{}', self.request + b' ' * 4096,
                     self.request.replace(b'"version":1', b'"version":true'),
                     self.request.replace(b'"text":', b'"text":"old","text":'),
                     self.request.replace(b'"version":1', b'"ims":false'),
                     self.request.replace(b'"+12345"', b'"12a"'),
                     self.request.replace(b'"private"', b'""'),
                     self.request.replace(b'private', b'\\ud800')):
            with self.subTest(data=data[:60]):
                code, reply = self.reply(data)
                self.assertEqual((code, reply["status"], reply["error"]), (2, "invalid", "invalid_request"))
        self.libraries.assert_not_called()

    def test_read_bound_and_default_version(self):
        stream = Mock()
        stream.read.return_value = '{"number":"123","text":"中文"}'
        self.assertEqual(sender._request_pdus(stream), [sender.build_pdu("123", "中文")])
        stream.read.assert_called_once_with(4097)

    def test_json_cli_invalid_stdin(self):
        proc = subprocess.run([sys.executable, "-S", str(SOURCE), "--json"], input=b'{}',
                              env={**os.environ, "PYTHONPATH": str(LIB)}, capture_output=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["error"], "invalid_request")
        self.assertEqual(proc.stderr, b'')


if __name__ == "__main__":
    unittest.main()
