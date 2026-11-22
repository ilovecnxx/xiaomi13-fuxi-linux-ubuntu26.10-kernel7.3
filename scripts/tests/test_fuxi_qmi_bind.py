import runpy
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'packaging/fuxi-modem/root/usr/lib/python3/dist-packages/fuxi_qmi_bind.py'
bind_subscription = runpy.run_path(str(MODULE))['bind_subscription']


class SubscriptionBindTest(unittest.TestCase):
    def bind(self, length=4, status=0, error=0, valid=True):
        qmi = Mock()
        qmi.message_tlv_read_init.return_value = (13, length)
        qmi.message_tlv_read_guint16.side_effect = [(True, 2, status), (valid, 4, error)]
        wait = Mock(return_value=(None, object()))
        bind_subscription(qmi, wait, Mock(), Mock(), 0x44, 1)
        qmi.message_tlv_write_guint8.assert_called_once_with(
            qmi.message_new.return_value, 1)

    def test_confirmed_binding(self):
        self.bind()

    def test_rejected_or_invalid_binding(self):
        for args in (dict(status=1, error=3), dict(error=3), dict(length=2), dict(valid=False)):
            with self.subTest(args=args), self.assertRaises(RuntimeError):
                self.bind(**args)
