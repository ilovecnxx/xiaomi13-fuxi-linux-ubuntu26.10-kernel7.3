import importlib.util
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "camera_trace_audit", Path(__file__).resolve().parents[1] / "fuxi-camera-trace-audit.py"
)
audit_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_module)


class CameraTraceAuditTests(unittest.TestCase):
    def audit(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.log"
            path.write_text(text)
            return audit_module.audit(path)

    def test_missing_rpmh_is_not_reported_as_success(self):
        result = self.audit(
            "# tracer: nop\n"
            " CameraManager-1 [006] d..1. 1188.320361: rpm_status: acb7000.isp status=RPM_RESUMING\n"
            " CameraManager-1 [006] d..1. 1198.557445: rpm_status: acb7000.isp status=RPM_ACTIVE\n"
            "overrun: 0\n"
        )
        self.assertEqual(result["resume_intervals"][0]["seconds"], 10.237084)
        self.assertTrue(any("Missing RPMh" in value for value in result["warnings"]))
        self.assertEqual(len(result["buffer_statistics"]), 1)

    def test_other_devices_do_not_complete_camera_resume(self):
        result = self.audit(
            " task-1 [000] ..... 1.000000: rpm_status: acb7000.isp status=RPM_RESUMING\n"
            " task-1 [000] ..... 1.100000: rpm_status: genpd:3:acb7000.isp status=RPM_ACTIVE\n"
            " task-1 [000] ..... 2.000000: rpm_status: acb7000.isp status=RPM_SUSPENDED\n"
        )
        self.assertEqual(len(result["pm_transitions"]), 2)
        self.assertEqual(result["resume_intervals"][0]["end_status"], "RPM_SUSPENDED")

    def test_counts_are_not_command_ack_pairing(self):
        result = self.audit(
            " task-1 [000] ..... 1.000000: rpmh_send_msg: command 0\n"
            " task-1 [000] ..... 1.000001: rpmh_send_msg: command 1\n"
            " task-1 [000] ..... 1.000010: rpmh_tx_done: batch complete\n"
        )
        self.assertEqual(result["event_counts"]["rpmh_send_msg"], 2)
        self.assertFalse(any("Missing RPMh" in value for value in result["warnings"]))
        self.assertTrue(any("do not prove complete" in value for value in result["warnings"]))

    def test_six_commands_share_one_completion_and_other_tcs_can_finish(self):
        lines = []
        for index in range(6):
            lines.append(
                f" task-1 [000] ..... 1.00000{index}: rpmh_send_msg: apps_rsc: "
                f"tcs(m): 0 [active] cmd(n): {index} msgid: 0x10108 "
                f"addr: 0x{0x50000 + index * 4:x} data: 0x1 complete: 1\n"
            )
        lines.extend([
            " task-2 [001] ..... 2.000000: rpmh_send_msg: apps_rsc: tcs(m): 1 [active] cmd(n): 0 msgid: 0x10108 addr: 0x40b04 data: 0x1 complete: 0\n",
            " task-2 [001] ..... 2.000100: rpmh_tx_done: apps_rsc: ack: tcs-m: 1 addr: 0x40b04 data: 0x1\n",
        ])
        result = self.audit("".join(lines))
        self.assertEqual(result["rpmh_active_batches_completed"], 1)
        self.assertEqual(len(result["rpmh_active_batches_unmatched"]), 1)
        self.assertEqual(len(result["rpmh_active_batches_unmatched"][0]["commands"]), 6)
        lines.append(" task-1 [000] ..... 3.000000: rpmh_tx_done: apps_rsc: ack: tcs-m: 0 addr: 0x50000 data: 0x1\n")
        result = self.audit("".join(lines))
        self.assertEqual(result["rpmh_active_batches_completed"], 2)
        self.assertEqual(result["rpmh_active_batches_unmatched"], [])

    def test_sleep_programming_is_not_an_active_request(self):
        result = self.audit(
            " task-1 [000] ..... 1.000000: rpmh_send_msg: apps_rsc: tcs(m): 3 [sleep] cmd(n): 0 msgid: 0x10008 addr: 0x50000 data: 0x0 complete: 0\n"
        )
        self.assertEqual(result["rpmh_active_batches_unmatched"], [])

    def test_wrong_completion_does_not_clear_pending_request(self):
        result = self.audit(
            " task-1 [000] ..... 1.000000: rpmh_send_msg: apps_rsc: tcs(m): 0 [active] cmd(n): 0 msgid: 0x10108 addr: 0x50000 data: 0x1 complete: 1\n"
            " task-1 [000] ..... 2.000000: rpmh_tx_done: apps_rsc: ack: tcs-m: 0 addr: 0x50000 data: 0x2\n"
        )
        self.assertEqual(result["rpmh_pairing_anomalies"]["completion_identity_mismatch"], 1)
        self.assertEqual(len(result["rpmh_active_batches_unmatched"]), 1)

    def test_since_excludes_old_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.log"
            path.write_text(
                " task-1 [000] ..... 1.000000: tracing_mark_write: old\n"
                " task-1 [000] ..... 2.000000: tracing_mark_write: new\n"
            )
            result = audit_module.audit(path, since=2.0)
        self.assertEqual(result["first_event"], 2.0)
        self.assertEqual([marker["text"] for marker in result["markers"]], ["new"])
