import importlib.util
import json
from pathlib import Path
import tempfile
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

    def test_untracked_file_blocks_release_check(self):
        self.approved["unsigned_prerelease_approved"] = True
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy_path = root / "packaging" / "release-policy.json"
            policy_path.parent.mkdir()
            policy_path.write_text(json.dumps(self.approved), encoding="utf-8")
            with patch.object(CHECK, "ROOT", root), \
                    patch.object(CHECK.subprocess, "check_output", return_value="?? new-file.txt\n") as status, \
                    patch("sys.argv", ["fuxi-release-check.py", "--unsigned-prerelease"]):
                with self.assertRaisesRegex(SystemExit, "clean worktree"):
                    CHECK.main()
            status.assert_called_once_with(["git", "-C", str(root), "status", "--porcelain"], text=True)


if __name__ == "__main__":
    unittest.main()
