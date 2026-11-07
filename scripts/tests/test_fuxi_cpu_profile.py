"""Verify bounded frequency plans, all-policy preflight and partial failure recovery."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[2] / "scripts/experiments/fuxi-cpu-profile.py"
LOADER = importlib.machinery.SourceFileLoader("fuxi_cpu_profile", str(PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
CPU = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(CPU)


class CpuProfileTest(unittest.TestCase):
    def policy(self, name="policy0", current=2000, minimum=300):
        return {"policy": name, "scaling_driver": "qcom-cpufreq-hw",
                "scaling_governor": "schedutil", "affected_cpus": "0 1 2",
                "cpuinfo_min_freq": 300, "cpuinfo_max_freq": 2000,
                "scaling_min_freq": minimum, "scaling_max_freq": current,
                "available_frequencies": [300, 600, 1000, 1300, 1500, 2000]}

    def tree(self, root, policies):
        for policy in policies:
            path = root / policy["policy"]
            path.mkdir()
            for field, value in policy.items():
                if field in ("policy", "available_frequencies"):
                    continue
                (path / field).write_text(str(value))
            (path / "scaling_available_frequencies").write_text(
                " ".join(map(str, policy["available_frequencies"])))

    def test_power_saver_rounds_down_and_retains_stricter_limits(self):
        changes = CPU.plan([self.policy(), self.policy("policy3", current=1000)], "power-saver")
        self.assertEqual([change["requested_max_khz"] for change in changes], [1300, 1000])
        self.assertEqual(CPU.plan([self.policy(current=1000)], "balanced")[0]["requested_max_khz"], 2000)

    def test_conflicting_minimum_or_unsupported_policy_is_rejected(self):
        cases = [self.policy(minimum=1500), {**self.policy(), "scaling_driver": "other"},
                 {**self.policy(), "available_frequencies": []}, self.policy(current=2500)]
        for policy in cases:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                CPU.plan([self.policy(), policy], "power-saver")

    def test_status_and_plan_never_write_and_missing_policies_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                CPU.inspect(root)
            self.tree(root, [self.policy()])
            with patch.object(Path, "write_text", side_effect=AssertionError("read-only")):
                self.assertEqual(CPU.inspect(root)[0]["scaling_governor"], "schedutil")
                self.assertEqual(CPU.plan(CPU.inspect(root), "power-saver")[0]["requested_max_khz"], 1300)

    def test_apply_changes_only_maximum_and_balanced_restores_nominal_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.tree(root, [self.policy(), self.policy("policy3")])
            CPU.apply(CPU.plan(CPU.inspect(root), "power-saver"), root)
            self.assertEqual([policy["scaling_max_freq"] for policy in CPU.inspect(root)], [1300, 1300])
            self.assertTrue(all(policy["scaling_governor"] == "schedutil" for policy in CPU.inspect(root)))
            CPU.apply(CPU.plan(CPU.inspect(root), "balanced"), root)
            self.assertEqual([policy["scaling_max_freq"] for policy in CPU.inspect(root)], [2000, 2000])

    def test_second_policy_write_failure_rolls_back_previous_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.tree(root, [self.policy(), self.policy("policy3")])
            changes = CPU.plan(CPU.inspect(root), "power-saver")
            original = Path.write_text

            def write(path, value):
                if path.parent.name == "policy3" and value == "1300":
                    raise OSError("simulated sysfs failure")
                return original(path, value)

            with patch.object(Path, "write_text", write), self.assertRaises(OSError):
                CPU.apply(changes, root)
            self.assertEqual([policy["scaling_max_freq"] for policy in CPU.inspect(root)], [2000, 2000])

    def test_failed_rollback_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.tree(root, [self.policy(), self.policy("policy3")])
            changes = CPU.plan(CPU.inspect(root), "power-saver")
            with patch.object(Path, "write_text", side_effect=OSError("write unavailable")):
                with self.assertRaisesRegex(RuntimeError, "rollback failed"):
                    CPU.apply(changes, root)

    def test_unaccepted_ceiling_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.tree(root, [self.policy()])
            changes = CPU.plan(CPU.inspect(root), "power-saver")
            with patch.object(Path, "read_text", return_value="2000"), patch.object(CPU.time, "sleep"):
                with self.assertRaisesRegex(ValueError, "not accepted"):
                    CPU.apply(changes, root)
            self.assertEqual(CPU.inspect(root)[0]["scaling_max_freq"], 2000)

    def test_asynchronous_qos_update_is_accepted_with_bounded_wait(self):
        path = Path("/sys/policy0/scaling_max_freq")
        with patch.object(Path, "read_text", side_effect=["2000", "2000", "1300"]) as read, \
                patch.object(CPU.time, "sleep") as sleep:
            CPU.wait_ceiling(path, 1300)
            self.assertEqual(read.call_count, 3)
            self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
