import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-release-check.py"
SPEC = importlib.util.spec_from_file_location("fuxi_release_check", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class ReleasePolicyTests(unittest.TestCase):
    def setUp(self):
        self.approved = {
            "project_license_approved": True,
            "third_party_redistribution_reviewed": True,
            "public_release_approved": True,
        }

    def test_explicitly_approved_unsigned_prerelease(self):
        self.approved["unsigned_prerelease_approved"] = True
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(CHECK.policy_requirements(self.approved, True), [])

    def test_unsigned_prerelease_requires_separate_approval(self):
        self.assertEqual(CHECK.policy_requirements(self.approved, True), ["unsigned_prerelease_approved"])

    def test_signed_release_still_requires_key_and_fingerprint(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(CHECK.policy_requirements(self.approved, False),
                             ["release_key_fingerprint", "FUXI_SIGNING_KEY"])

    def test_unsigned_mode_still_requires_release_approvals(self):
        self.approved["unsigned_prerelease_approved"] = True
        self.approved["third_party_redistribution_reviewed"] = False
        self.assertEqual(CHECK.policy_requirements(self.approved, True), ["third_party_redistribution_reviewed"])


if __name__ == "__main__":
    unittest.main()
