import importlib.util
from pathlib import Path
import struct
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "imx800_mode", Path(__file__).resolve().parents[1] / "fuxi-imx800-mode.py")
mode = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mode)


class Imx800ModeTests(unittest.TestCase):
    def test_payload_preserves_order_duplicates_and_big_endian_addresses(self):
        records = [{"address": 0x1234, "data": [5]},
                   {"address": 0x1234, "data": [6]}]
        profile = {"tables": {"init": records + [records[0]] * 165,
                              "mode1": [records[1]] * 135}}
        with mock.patch.object(mode.audit, "model"):
            payload = mode.encode(profile)
        self.assertEqual(payload[:16], struct.pack("<8s4H", b"FXIMX800", 1, 167, 135, 0))
        self.assertEqual(payload[16:22], b"\x12\x34\x05\x12\x34\x06")
        self.assertEqual(len(payload), 922)

    def test_wrong_counts_rejected(self):
        with mock.patch.object(mode.audit, "model"):
            with self.assertRaises(mode.audit.camera.FormatError):
                mode.encode({"tables": {"init": [], "mode1": []}})

    def test_invalid_model_rejected_before_encoding(self):
        with mock.patch.object(mode.audit, "model", side_effect=ValueError("invalid")):
            with self.assertRaisesRegex(ValueError, "invalid"):
                mode.encode({})

    def test_audit_failure_does_not_read_or_generate(self):
        with mock.patch.object(mode.audit, "audit", side_effect=ValueError("mismatch")):
            with self.assertRaisesRegex(ValueError, "mismatch"):
                mode.build({})
