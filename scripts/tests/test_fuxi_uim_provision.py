import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[2] / "packaging/fuxi-modem/root/usr/sbin/fuxi-uim-provision.py"
spec = importlib.util.spec_from_file_location("uim", PATH)
uim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uim)


def status(primary="present", secondary="present", second_card=True):
    return f"""Primary GW: {primary}
Secondary GW: {secondary}
Slot [1]:
    Card state: 'present'
    Application type: 'usim (2)'
    Application ID:
        A0:00:00:01
Slot [2]:
    Card state: '{'present' if second_card else 'absent'}'
    Application type: 'usim (2)'
    Application ID:
        A0:00:00:02
"""


class UIMProvisionTest(unittest.TestCase):
    def execute(self, states):
        with patch.object(uim, "card_status", side_effect=states) as read, \
                patch.object(uim.time, "sleep") as sleep, \
                patch.object(uim.subprocess, "run") as run:
            uim.main()
        return read, sleep, run

    def test_ready_dual_sim_skips_settle_and_qmi_writes(self):
        read, sleep, run = self.execute([status()])
        self.assertEqual(read.call_count, 1)
        sleep.assert_not_called()
        run.assert_not_called()

    def test_absent_second_card_does_not_delay_ready_first_card(self):
        _, sleep, run = self.execute([status(secondary="doesn't exist", second_card=False)])
        sleep.assert_not_called()
        run.assert_not_called()

    def test_missing_session_still_gets_settle_window(self):
        _, sleep, run = self.execute([status(primary="doesn't exist"), status()])
        sleep.assert_called_once_with(uim.SETTLE)
        run.assert_not_called()

    def test_unknown_session_is_not_treated_as_ready(self):
        _, sleep, _ = self.execute([status().replace("Primary GW: present", ""), status()])
        sleep.assert_called_once_with(uim.SETTLE)

    def test_still_missing_session_is_activated_after_settle(self):
        with patch.object(uim, "card_status", side_effect=[status(primary="doesn't exist"),
                                                         status(primary="doesn't exist"), status()]), \
                patch.object(uim.time, "sleep") as sleep, \
                patch.object(uim.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "success"
            run.return_value.stderr = ""
            uim.main()
        self.assertIn("session-type=primary-gw-provisioning,activate=yes,slot=1,aid=A0:00:00:01",
                      run.call_args.args[0][-1])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [uim.SETTLE, 2])


if __name__ == "__main__":
    unittest.main()
