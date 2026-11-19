import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-ci-plan.py"
SPEC = importlib.util.spec_from_file_location("ci_plan", SCRIPT)
PLAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLAN)


class CiPlanTests(unittest.TestCase):
    def selected(self, *paths):
        return {key for key, value in PLAN.classify(paths).items() if value}

    def test_documentation_does_not_trigger_builds(self):
        self.assertEqual({"docs"}, self.selected("README.md", "AGENTS.md", "TASKS.md",
                                               "packaging/README.md", "docs/交接.md"))

    def test_script_checks_and_unclassified_application_paths(self):
        self.assertEqual({"docs", "scripts"}, self.selected("scripts/fuxi-health.sh"))
        self.assertEqual(set(PLAN.CHECKS), self.selected("apps/dsh-phone/dsh-web-window.py"))
        self.assertEqual({"docs", "scripts"}, self.selected("scripts/tests/fixture.md"))

    def test_shared_and_unknown_inputs_expand_coverage(self):
        for path in (".github/workflows/ci.yml", "scripts/fuxi-ci.sh",
                     "packaging/build/Containerfile", "new-module/code.c"):
            with self.subTest(path=path):
                self.assertTrue(all(PLAN.classify([path]).values()))

    def test_recipe_dependency_and_build_changes_select_required_checks(self):
        self.assertEqual({"docs", "scripts", "source", "kernel"},
                         self.selected("kernel/manifest.json"))
        self.assertEqual({"docs", "scripts", "source", "kernel"},
                         self.selected("scripts/fuxi-build-deb.sh"))
        self.assertEqual({"docs", "scripts", "third_party", "userspace"},
                         self.selected("packaging/sources/manifest.json"))
        self.assertEqual({"docs", "scripts", "userspace"},
                         self.selected("scripts/fuxi-build-libssc.sh"))
        self.assertEqual({"docs", "source"},
                         self.selected("scripts/tests/kernel/test_fuxi_volume_limit.py"))
        self.assertEqual({"docs", "scripts"},
                         self.selected("kernel/experiments/camera/imx800-power/manifest.json"))

    def test_manual_schedule_and_unavailable_history_run_all(self):
        for event in ("schedule", "workflow_dispatch", "push", "pull_request", "unknown"):
            with self.subTest(event=event):
                self.assertTrue(all(PLAN.event_plan(event, {}, Path("/nonexistent")).values()))
        event = {"before": "0" * 40, "after": "a" * 40}
        self.assertTrue(all(PLAN.event_plan("push", event, Path(".")).values()))

    def test_push_rename_out_of_scripts_still_selects_original_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args]).decode().strip()
            git("init", "-q")
            (root / "scripts").mkdir()
            (root / "scripts/health.sh").write_text("#!/bin/sh\nexit 0\n")
            git("add", ".")
            git("-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            git("mv", "scripts/health.sh", "README.md")
            git("-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "rename")
            head = git("rev-parse", "HEAD")
            result = PLAN.event_plan("push", {"before": base, "after": head}, root)
            self.assertTrue(result["scripts"])
            self.assertFalse(result["kernel"])
            pull_request = {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
            self.assertEqual(result, PLAN.event_plan("pull_request", pull_request, root))
