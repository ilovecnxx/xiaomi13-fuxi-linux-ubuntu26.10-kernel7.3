import hashlib
import hmac
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT = (Path(__file__).resolve().parents[2] / "packaging/fuxi-device/root"
          / "usr/libexec/fuxi/fuxi-mipps-auth")
loader = importlib.machinery.SourceFileLoader("fuxi_mipps_auth", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
auth = importlib.util.module_from_spec(spec)
loader.exec_module(auth)


class BatteryAuthenticationTests(unittest.TestCase):
    def test_accepts_valid_challenge_response(self):
        challenge = "0123456789abcdef" * 4
        for key in auth.FG_KEYS:
            reply = hmac.new(bytes.fromhex(key), bytes.fromhex(challenge),
                             hashlib.sha256).hexdigest()
            self.assertTrue(auth.valid_battery_digest(challenge, reply))

    def test_rejects_replayed_and_malformed_responses(self):
        challenge = "00" * 32
        reply = hmac.new(bytes.fromhex(auth.FG_KEYS[0]), bytes.fromhex(challenge),
                         hashlib.sha256).hexdigest()
        self.assertFalse(auth.valid_battery_digest("01" * 32, reply))
        for invalid in ("", "0" * 64, "gg" * 32, reply[:32]):
            self.assertFalse(auth.valid_battery_digest(challenge, invalid))

    def test_reuses_an_already_verified_fuel_gauge(self):
        charger = auth.Charger("/nonexistent")
        with patch.object(charger, "read", return_value="1"), \
                patch.object(charger, "write") as write:
            charger.verify_battery()
        write.assert_not_called()

    @patch.object(auth.time, "sleep")
    def test_waits_for_the_current_challenge_response(self, sleep):
        challenge = "01" * 32
        reply = hmac.new(bytes.fromhex(auth.FG_KEYS[0]), bytes.fromhex(challenge),
                         hashlib.sha256).hexdigest()
        charger = auth.Charger("/nonexistent")
        with patch.object(auth.secrets, "token_hex", return_value=challenge), \
                patch.object(charger, "read", side_effect=["0", "00" * 32, reply]), \
                patch.object(charger, "write") as write:
            charger.verify_battery()
        write.assert_any_call("authentic", 1)
        self.assertEqual(sum(call.args[0] == "verify_digest"
                             for call in write.call_args_list), 1)

    @patch.object(auth.time, "sleep")
    def test_failed_verification_never_asserts_authentic(self, sleep):
        charger = auth.Charger("/nonexistent")
        writes = []
        with patch.object(charger, "write", side_effect=lambda *v: writes.append(v)), \
                patch.object(charger, "read", return_value="00" * 32):
            with self.assertRaises(RuntimeError):
                charger.verify_battery()
        self.assertNotIn(("authentic", 1), writes)
        self.assertEqual(writes[-1], ("authentic", 0))

    def test_detach_prevents_a_write(self):
        charger = auth.Charger("/nonexistent")
        with patch.object(auth, "usb_online", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "detached"):
                charger.write("authentic", 1)

    def test_adapter_digest_binds_identity(self):
        self.assertNotEqual(auth.adapter_digest("00" * 16, "00000001"),
                            auth.adapter_digest("00" * 16, "00000002"))

    @patch.object(auth.glob, "glob", return_value=[])
    def test_adapter_failure_is_reported_to_firmware(self, glob):
        charger = auth.Charger("/nonexistent")
        writes = []
        commands = []

        def vdm(cmd, payload="null", wait=True):
            commands.append((cmd, payload))
            return "00" * 16 if cmd == 5 else ""

        with patch.object(charger, "read", side_effect=lambda name: {
                "real_type": "6", "adapter_id": "1"}[name]), \
                patch.object(charger, "write", side_effect=lambda *v: writes.append(v)), \
                patch.object(charger, "vdm", side_effect=vdm):
            with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                charger.verify_adapter()
        self.assertEqual(writes, [("pd_verifed", 0)])
        self.assertIn((6, "00000000"), commands)
        self.assertIn((7, "00000000"), commands)
        self.assertFalse(any(cmd == 8 for cmd, payload in commands))


if __name__ == "__main__":
    unittest.main()
