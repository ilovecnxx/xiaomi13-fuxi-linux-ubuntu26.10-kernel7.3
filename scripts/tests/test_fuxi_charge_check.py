import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "fuxi_charge_check", Path(__file__).resolve().parents[1] / "fuxi-charge-check.py")
charge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(charge)


def samples(counters=(300000, 310000, 320000), current=1400000):
    return [{"boottime": n * 20, "end_boottime": n * 20 + 0.2, "errors": {},
             "battery": {"status": "Charging", "charge_counter": counter,
                         "current_now": current},
             "usb": {"online": 1, "usb_type": "SDP [PD] PD_PPS",
                     "voltage_now": 4900000, "current_now": 1397000}}
            for n, counter in enumerate(counters)]


class ChargeCheckTests(unittest.TestCase):
    def test_net_charge_needs_counter_and_positive_current(self):
        result = charge.summarize(samples())
        self.assertEqual(result["result"], "net_charging")
        self.assertEqual(result["charge_delta_uah"], 20000)
        self.assertEqual(result["elapsed_seconds"], 40)
        self.assertAlmostEqual(result["usb_input_watts_range"][0], 6.8453)
        self.assertFalse(result["fast_charge_verified"])

    def test_charging_status_does_not_hide_net_discharge(self):
        result = charge.summarize(samples((320000, 310000, 300000), -100000))
        self.assertEqual(result["result"], "net_discharging")

    def test_stale_counter_or_conflicting_current_is_inconclusive(self):
        for readings in (samples((300000, 300000, 300000)),
                         samples(current=-100000), samples(current=0),
                         samples((300000, 290000, 320000))):
            with self.subTest(readings=readings):
                self.assertEqual(charge.summarize(readings)["result"], "inconclusive")

    def test_offline_or_changed_supply_cannot_pass(self):
        readings = samples()
        readings[1]["usb"]["online"] = 0
        self.assertEqual(charge.summarize(readings)["result"],
                         "usb_not_continuously_online")
        readings = samples()
        readings[1]["usb"]["usb_type"] = "[SDP] PD PD_PPS"
        self.assertEqual(charge.summarize(readings)["result"], "usb_type_changed")

    def test_missing_short_or_unordered_evidence_cannot_pass(self):
        readings = samples()
        broken = copy.deepcopy(readings)
        broken[1]["errors"]["battery.charge_counter"] = "missing"
        overlapping = copy.deepcopy(readings)
        overlapping[0]["end_boottime"] = 20
        for data in ([], readings[:1], readings[:2], readings[::-1], broken, overlapping):
            with self.subTest(data=data):
                self.assertEqual(charge.summarize(data)["result"], "insufficient_evidence")

    def test_snapshot_only_reads_allowlisted_battmgr_properties(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for _, (name, fields) in charge.FIELDS.items():
                supply = root / name
                supply.mkdir()
                for field in fields:
                    value = "Charging" if field == "status" else "1"
                    (supply / field).write_text(value)
            ucsi = root / "ucsi-source-psy"
            ucsi.mkdir()
            (ucsi / "current_now").write_text("3000000")
            result = charge.snapshot(root)
            self.assertEqual(result["errors"], {})
            self.assertEqual(result["usb"]["current_now"], 1)
            self.assertEqual((ucsi / "current_now").read_text(), "3000000")
            (root / "qcom-battmgr-bat/current_now").write_text("invalid")
            (root / "qcom-battmgr-usb/online").unlink()
            result = charge.snapshot(root)
            self.assertIn("battery.current_now", result["errors"])
            self.assertIn("usb.online", result["errors"])


if __name__ == "__main__":
    unittest.main()
