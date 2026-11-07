"""Benchmark safety and failure records without containers or device access."""

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-kernel-benchmark.py"
spec = importlib.util.spec_from_file_location("kernel_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class KernelBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "kernel").mkdir()
        (self.root / "kernel/manifest.json").write_text(json.dumps(
            {"tree": "pinned-tree", "source_date_epoch": 1234}))
        self.root_patch = patch.object(benchmark, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.git_patch = patch.object(benchmark, "git", side_effect=self.fake_git)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
        self.space_patch = patch.object(benchmark.shutil, "disk_usage")
        self.space_patch.start().return_value.free = 20 * 1024**3
        self.addCleanup(self.space_patch.stop)

    @staticmethod
    def fake_git(*args):
        return "" if args[0] == "status" else "project-commit"

    def test_existing_output_is_never_removed_or_reused(self):
        output = self.root / "work/kbuild/benchmark-trial-out"
        output.mkdir(parents=True)
        (output / "keep").write_text("previous build")
        with self.assertRaises(SystemExit), patch.object(benchmark, "run_phase") as run:
            benchmark.main(["trial", "--jobs", "4"])
        run.assert_not_called()
        self.assertEqual((output / "keep").read_text(), "previous build")

    def test_failed_command_retains_logs_timing_and_checksum(self):
        output = self.root / "result"
        output.mkdir()
        report = {"phases": []}
        with self.assertRaises(subprocess.CalledProcessError):
            benchmark.run_phase(report, output, "failure", ["bash", "-c", "echo diagnostic; exit 7"],
                                os.environ.copy())
        self.assertEqual(report["phases"][0]["exit_code"], 7)
        self.assertGreaterEqual(report["phases"][0]["seconds"], 0)
        self.assertIn("diagnostic", (output / "failure.log").read_text())

    def test_failed_environment_still_has_report_and_checksums(self):
        with patch.object(benchmark, "run_phase", side_effect=OSError("container unavailable")):
            result = benchmark.main(["trial", "--jobs", "4"])
        self.assertEqual(result, 1)
        output = self.root / "work/benchmarks/trial"
        report = json.loads((output / "benchmark.json").read_text())
        self.assertEqual(report["status"], "failure")
        self.assertIn("container unavailable", report["error"])
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=output, check=True,
                       capture_output=True)

    def test_dirty_project_cannot_archive_different_recipes(self):
        with patch.object(benchmark, "git", return_value=" M script"), self.assertRaises(SystemExit):
            benchmark.main(["trial", "--jobs", "4"])
        self.assertFalse((self.root / "work").exists())

    def test_missing_environment_provenance_rejects_artifacts(self):
        package = self.root / "work/kbuild/trial"
        package.mkdir(parents=True)
        (package / "build.json").write_text(json.dumps(
            {"source_tree": "pinned-tree", "source_dirty": False,
             "build_environment_sha256": None}))
        with self.assertRaisesRegex(ValueError, "build environment"):
            benchmark.collect_artifacts("trial", self.root / "result",
                                        {"tree": "pinned-tree"}, "project-commit")

    def test_reused_source_tree_can_use_verified_shared_archive_cache(self):
        cache = self.root / "build/source-cache"
        cache.mkdir(parents=True)
        digest = hashlib.sha256(b"archive").hexdigest()
        archive = cache / digest
        archive.write_bytes(b"archive")
        manifest = {"archive": "linux.tar.gz", "archive_sha256": digest,
                    "url": "https://invalid.example/not-needed"}
        self.assertEqual(benchmark.kernel_archive(manifest), archive)

    def test_missing_archive_is_fetched_and_verified(self):
        original = self.root / "upstream.tar.gz"
        original.write_bytes(b"upstream input")
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        archive = benchmark.kernel_archive({"archive": "linux.tar.gz", "archive_sha256": digest,
                                             "url": original.as_uri()})
        self.assertEqual(archive.read_bytes(), original.read_bytes())

    def test_corrupt_download_cache_is_rejected(self):
        cache = self.root / "work/sources"
        cache.mkdir(parents=True)
        (cache / "linux.tar.gz").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            benchmark.kernel_archive({"archive": "linux.tar.gz", "archive_sha256": "wrong"})


if __name__ == "__main__":
    unittest.main()
