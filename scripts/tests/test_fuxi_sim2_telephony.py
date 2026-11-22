import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import Mock, patch


HELPER = Path(__file__).resolve().parents[2] / "packaging/fuxi-modem/root/usr/sbin/fuxi-sim2-telephony"
dispatch = runpy.run_path(str(HELPER))["main"]


class TelephonyDispatchTest(unittest.TestCase):
    def test_routes_one_operation_with_private_input(self):
        control, sms, inbox = Mock(), Mock(), Mock()
        with patch.dict(sys.modules, fuxi_sub2_control=control, fuxi_sub2_sms=sms, fuxi_sub2_inbox=inbox):
            for action, request, argument in [("answer", {"id": 7}, "7"), ("end", {"id": 9}, "9"),
                                              ("dial", {"number": "000"}, "000")]:
                stream = io.BytesIO(json.dumps({"version": 1, **request}).encode())
                self.assertIs(dispatch([action], stream), control.run.return_value)
                control.run.assert_called_with(action, argument)
            stream = io.BytesIO()
            self.assertIs(dispatch(["calls"], stream), control.run.return_value)
            control.run.assert_called_with("calls")
            self.assertIs(dispatch(["wait-calls"], stream), control.run.return_value)
            control.run.assert_called_with("wait-calls")
            self.assertIs(dispatch(["sms-send"], stream), sms.run_send.return_value)
            sms.run_send.assert_called_once_with(stream)
            self.assertIs(dispatch(["sms-inbox"], stream), inbox.run_inbox.return_value)
            inbox.run_inbox.assert_called_once_with(stream)

    def test_invalid_request_never_dispatches(self):
        control = Mock()
        with patch.dict(sys.modules, fuxi_sub2_control=control):
            for payload in (b"", b"[]", b"[" * 257, b'\xff', b'{"version":true,"id":1}',
                            b'{"version":1,"id":true}', b'{"version":1,"id":1,"extra":0}'):
                output = io.StringIO()
                with self.subTest(payload=payload), contextlib.redirect_stdout(output):
                    self.assertEqual(dispatch(["end"], io.BytesIO(payload)), 2)
                    self.assertEqual(json.loads(output.getvalue())["error"], "invalid_request")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(dispatch(["dial", "000"], io.BytesIO()), 2)
            control.run.assert_not_called()
