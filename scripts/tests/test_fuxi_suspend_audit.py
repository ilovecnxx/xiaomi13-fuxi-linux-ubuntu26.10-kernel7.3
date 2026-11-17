import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "suspend_audit", Path(__file__).resolve().parents[1] / "fuxi-suspend-audit.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def event(body, name="suspend_resume", timestamp="1.000000"):
    return f" task-1 [000] dl... {timestamp}: {name}: {body}\n"


def send(state, data="0x40000000", address="0x50000"):
    return event(f"apps_rsc: tcs(m): 3 [{state}] cmd(n): 0 msgid: 0x10008 "
                 f"addr: {address} data: {data} complete: 0", "rpmh_send_msg")


class SuspendAuditTest(unittest.TestCase):
    def audit(self, commands=None, loss=0, missing_cpu=False, db=True, extra="", suffix="",
              glink=False, pmic=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = (event("syscore_suspend[0] begin")
                     + (commands if commands is not None else send("sleep") + send("wake", "0x60004001"))
                     + event("syscore_suspend[0] end")
                     + event("machine_suspend[3] begin")
                     + event("machine_suspend[3] end")
                     + event("syscore_resume[0] end", timestamp="5.000000")
                     + event("irq=13 name=408000.mailbox", "irq_handler_entry", "5.000010")
                     + suffix)
            (root / "trace.txt").write_text(extra + trace)
            (root / "before-interrupts.txt").write_text(" CPU0 CPU1\n")
            for cpu in range(1 if missing_cpu else 2):
                (root / f"trace-cpu{cpu}-stats.txt").write_text(
                    f"overrun: {loss}\ncommit overrun: 0\ndropped events: 0\n")
            (root / "trace-config.json").write_text(json.dumps({
                "clock": "boot", "irq_scope": "all handlers, including local timers", "enabled_events": [
                "power/suspend_resume", "power/device_pm_callback_end", "irq/irq_handler_entry",
                "rpmh/rpmh_send_msg", "qcom_aoss/aoss_send", "qcom_aoss/aoss_send_done"]
                + (["qcom_glink/qcom_glink_cmd_tx_data"] if glink else [])
                + (["pmic_glink/" + name for name in AUDIT.PMIC_EVENTS] if pmic else [])}))
            if db:
                (root / "before-cmd-db.txt").write_text(
                    "Slave ARC (v16.0)\n0x30000: cx.lvl [00 10]\n"
                    "Slave BCM (v16.0)\n0x50000: MC0 [40 16]\n")
            return AUDIT.audit(root)

    def test_equal_machine_times_do_not_become_zero_residency_or_wake_cause(self):
        report = self.audit()
        self.assertEqual(report["machine_suspend"]["begin"]["time"],
                         report["machine_suspend"]["end"]["time"])
        self.assertIsNone(report["wake_cause"])
        self.assertNotIn("residency", report)
        self.assertIn("irq=13", report["first_traced_irqs_after_return"][0]["body"])
        self.assertTrue(report["trace_health"]["loss_counters_complete"])

    def test_decodes_only_sleep_votes_and_keeps_wake_votes_separate(self):
        report = self.audit(extra=send("sleep", "0x60000001"), suffix=send("sleep", "0x60000002"))
        self.assertTrue(report["all_observed_sleep_commands_are_zero_bcm_votes"])
        sleep = report["rpmh_syscore_commands"]["sleep"]
        self.assertEqual(len(sleep), 1)
        self.assertEqual(sleep[0]["resource"]["names"], ["MC0"])
        self.assertEqual(sleep[0]["bcm"], {"commit": True, "valid": False, "vote_x": 0, "vote_y": 0})
        self.assertEqual(report["rpmh_syscore_commands"]["wake"][0]["bcm"]["vote_x"], 1)

    def test_nonzero_sleep_vote_is_reported(self):
        report = self.audit(send("sleep", "0x60004008") + send("wake"))
        self.assertFalse(report["all_observed_sleep_commands_are_zero_bcm_votes"])
        self.assertEqual(report["sleep_bcm_nonzero_votes"][0]["bcm"]["vote_y"], 8)

    def test_address_range_alone_does_not_select_bcm_encoding(self):
        report = self.audit(db=False)
        self.assertFalse(report["all_observed_sleep_commands_are_zero_bcm_votes"])
        self.assertEqual(len(report["sleep_commands_without_bcm_decode"]), 1)
        report = self.audit(send("sleep", address="0x30000"))
        self.assertNotIn("bcm", report["rpmh_syscore_commands"]["sleep"][0])

    def test_absent_reprogramming_does_not_imply_absent_cached_votes(self):
        report = self.audit(commands="")
        self.assertFalse(report["all_observed_sleep_commands_are_zero_bcm_votes"])
        self.assertTrue(any("cached TCS" in warning for warning in report["warnings"]))

    def test_loss_and_missing_counters_are_not_treated_as_complete_zero_loss(self):
        report = self.audit(loss=7)
        self.assertTrue(report["trace_health"]["loss_detected"])
        report = self.audit(missing_cpu=True)
        self.assertFalse(report["trace_health"]["loss_counters_complete"])
        self.assertEqual(report["trace_health"]["cpu_loss_counters"]["cpu1"], {})

    def test_malformed_command_invalidates_zero_vote_summary(self):
        report = self.audit(send("sleep") + event("truncated", "rpmh_send_msg"))
        self.assertEqual(report["unparsed_rpmh_commands"], 1)
        self.assertFalse(report["all_observed_sleep_commands_are_zero_bcm_votes"])

    def test_callbacks_and_aoss_are_scoped_before_entry(self):
        report = self.audit(extra=event("dwc3 a600000.usb, err=-16", "device_pm_callback_end")
                            + event("request", "aoss_send"),
                            suffix=event("dwc3 a600000.usb, err=-22", "device_pm_callback_end"))
        self.assertEqual(len(report["callback_errors_before_entry"]), 1)
        self.assertEqual(report["aoss_events_before_entry"], {"aoss_send": 1, "aoss_send_done": 0})

    def test_multiple_cycles_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "found 2"):
            self.audit(suffix=event("machine_suspend[3] begin"))

    def test_command_db_rejects_ambiguous_slave_type(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            AUDIT.command_db("Slave ARC (v1.0)\n0x50000: cx.lvl\n"
                             "Slave BCM (v1.0)\n0x50000: MC0\n")

    def test_glink_direction_and_phase_follow_line_order_not_equal_timestamps(self):
        def packet(direction, channel, size):
            return event(f"{direction} remote: lpass channel: {channel}[4/8] "
                         f"iid: 2 chunk_size: {size} left_size: 0 cont: 0",
                         "qcom_glink_cmd_tx_data")

        report = self.audit(extra=packet("tx", "IPCRTR", 52),
                            suffix=packet("rx", "PMIC_RTR_ADSP_APPS", 220), glink=True)
        glink = report["glink_data"]
        self.assertTrue(glink["data_event_enabled"])
        self.assertEqual(glink["unparsed_data_events"], 0)
        self.assertEqual(glink["counts"], [
            {"phase": "after_return", "direction": "rx", "remote": "lpass",
             "channel": "PMIC_RTR_ADSP_APPS", "count": 1},
            {"phase": "before_entry", "direction": "tx", "remote": "lpass",
             "channel": "IPCRTR", "count": 1}])
        first = glink["first_data_after_return"][0]
        self.assertEqual(first["chunk_size"], 220)
        self.assertEqual(first["iid"], 2)
        self.assertFalse(first["cont"])
        self.assertIsNone(report["wake_cause"])

    def test_glink_missing_coverage_and_malformed_metadata_are_explicit(self):
        report = self.audit(suffix=event("truncated", "qcom_glink_cmd_tx_data"))
        glink = report["glink_data"]
        self.assertFalse(glink["data_event_enabled"])
        self.assertEqual(glink["unparsed_data_events"], 1)
        self.assertEqual(glink["first_data_after_return"], [])
        self.assertTrue(any("absence is not evidence" in warning for warning in report["warnings"]))

    def test_pmic_headers_keep_transport_results_and_equal_time_phases_separate(self):
        def message(stage, owner, opcode, ret=0):
            return event(f"dev=pmic-glink len=220 header_valid=1 owner={owner} "
                         f"type=1 opcode={opcode} ret={ret}", "pmic_glink_" + stage)

        report = self.audit(extra=message("tx", 32778, "0x30")
                            + message("tx_done", 32778, "0x30", -110),
                            suffix=message("rx", 32779, "0x11"), pmic=True)
        pmic = report["pmic_messages"]
        self.assertEqual(pmic["missing_events"], [])
        self.assertEqual(pmic["unparsed_events"], 0)
        before = pmic["last_messages_before_entry"]
        self.assertEqual(before[0]["owner_name"], "BATTMGR")
        self.assertEqual(before[1]["ret"], -110)
        after = pmic["first_messages_after_return"]
        self.assertEqual(after[0]["owner_name"], "USBC")
        self.assertEqual(after[0]["opcode"], 17)
        self.assertEqual(pmic["counts"][2]["phase"], "after_return")
        self.assertIsNone(report["wake_cause"])

    def test_pmic_truncated_headers_are_not_decoded_and_missing_events_are_explicit(self):
        report = self.audit(suffix=event("dev=pmic-glink len=4 header_valid=0 "
                                        "owner=0 type=0 opcode=0x0 ret=0", "pmic_glink_rx")
                            + event("truncated", "pmic_glink_tx_done"))
        pmic = report["pmic_messages"]
        self.assertEqual(len(pmic["missing_events"]), 3)
        self.assertEqual(pmic["unparsed_events"], 1)
        self.assertIsNone(pmic["first_messages_after_return"][0]["owner"])
        self.assertIsNone(pmic["first_messages_after_return"][0]["opcode"])
        self.assertTrue(any("not firmware completion" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
