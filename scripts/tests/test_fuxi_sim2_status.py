import contextlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


HELPER = Path(__file__).resolve().parents[2] / "packaging/fuxi-modem/root/usr/sbin/fuxi-simctl"


class Sim2StatusTest(unittest.TestCase):
    def setUp(self):
        repository = SimpleNamespace(Qmi=Mock(), Qrtr=Mock(), GLib=Mock(Error=RuntimeError))
        with patch.dict(sys.modules, {"gi": Mock(), "gi.repository": repository}):
            self.main = runpy.run_path(str(HELPER))["main"]
        self.helper = self.main.__globals__
        self.modem = Mock(spec=["alloc", "nas", "close"])
        self.open_modem = Mock(return_value=self.modem)
        self.card_response = Mock(spec=["get_card_status", "get_result"])
        self.network_response = Mock(spec=["get_serving_system", "get_result"])
        self.network_response.get_serving_system.return_value = (True, "registered", None, None, None, [])
        responses = {"get_card_status": self.card_response, "get_serving_system": self.network_response}
        self.call = Mock(side_effect=lambda method, *_: responses[method])
        self.uim = repository.Qmi.Service.UIM
        self.configure_card()

    def configure_card(self, state="present", secondary=0x100, app_state="ready"):
        sim2 = SimpleNamespace(card_state=state, applications=[SimpleNamespace(state=app_state)])
        self.card_response.get_card_status.return_value = (True, 0, 0xffff, secondary, 0xffff, [None, sim2])

    def query(self, active=True):
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(self.helper, Modem=self.open_modem, listener_active=lambda: active, call=self.call), \
                patch.object(sys, "argv", [str(HELPER), "sim2-status"]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(self.main(), 0)
        self.assertEqual(errors.getvalue(), "")
        result = json.loads(output.getvalue())
        self.assertEqual(set(result), {"version", "slot", "listener_active", "card_state",
                                       "subscription_ready", "registered"})
        self.assertEqual((result["version"], result["slot"], result["listener_active"]), (1, 2, active))
        return result["card_state"], result["subscription_ready"], result["registered"]

    def test_registered_independent_of_listener(self):
        self.assertEqual(self.query(False), ("present", True, True))
        self.modem.alloc.assert_called_once_with(self.uim)
        self.modem.nas.assert_called_once_with(1)
        self.modem.close.assert_called_once_with()
        self.assertEqual([args.args[0] for args in self.call.call_args_list],
                         ["get_card_status", "get_serving_system"])

    def test_absent_error_and_locked(self):
        for card, app, expected in (("absent", "ready", False), ("error", "ready", False),
                                    ("present", "pin1-or-upin-pin-required", False),
                                    ("present", "unknown", None)):
            with self.subTest(card=card, app=app):
                self.configure_card(card, app_state=app)
                self.assertEqual(self.query(), (card, expected, None))
        self.modem.nas.assert_not_called()

    def test_wrong_slot_unprovisioned_and_invalid_app(self):
        for index, ready in ((0, False), (0xffff, False), (0x101, None)):
            with self.subTest(index=index):
                self.configure_card(secondary=index)
                self.assertEqual(self.query(), ("present", ready, None))
        self.modem.nas.assert_not_called()

    def test_no_second_slot_and_qmi_failure(self):
        self.card_response.get_card_status.return_value = (0, 0xffff, 0xffff, 0xffff, [None])
        self.assertEqual(self.query(), ("unknown", None, None))
        self.card_response.get_result.side_effect = RuntimeError("private diagnostic")
        self.assertEqual(self.query(), ("unknown", None, None))
        self.assertEqual(self.modem.close.call_count, 2)
        self.open_modem.side_effect = RuntimeError("private diagnostic")
        self.assertEqual(self.query(), ("unknown", None, None))

    def test_registration_failures_preserve_card_evidence(self):
        for state, expected in (("registration-denied", False), ("not-registered-searching", False),
                                ("unknown", None)):
            with self.subTest(state=state):
                self.network_response.get_serving_system.return_value = (state, None, None, None, [])
                self.assertEqual(self.query(), ("present", True, expected))
        self.modem.nas.side_effect = RuntimeError("private diagnostic")
        self.assertEqual(self.query(), ("present", True, None))
        self.assertEqual(self.modem.close.call_count, 4)

    def test_systemd_query_is_limited_to_active_state(self):
        with patch.object(subprocess, "run") as run:
            for state, expected in (("active", True), ("activating", False), ("failed", False), ("", None)):
                run.return_value.stdout = state + "\n"
                self.assertIs(self.helper["listener_active"](), expected)
            run.side_effect = subprocess.TimeoutExpired("systemctl", 3)
            self.assertIsNone(self.helper["listener_active"]())
            self.assertEqual(run.call_args.args[0], ["/usr/bin/systemctl", "show", "--property=ActiveState",
                                                    "--value", "fuxi-sub2-msg.service"])

    def test_fixed_argv_and_existing_status(self):
        with patch.dict(self.helper, status=Mock(), sim2_status=Mock()), \
                patch.object(sys, "argv", [str(HELPER), "status"]):
            self.assertEqual(self.main(), 0)
            self.helper["status"].assert_called_once_with()
            with patch.object(sys, "argv", [str(HELPER), "sim2-status", "voice"]), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.main(), 2)
            self.helper["sim2_status"].assert_not_called()
