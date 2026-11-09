"""Compile and exercise the exact matrix helper shipped in the source patch."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class LibsscMatrixTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc") and shutil.which("git"), "C compiler and git required")
    def test_candidate_rotation_validation_and_complete_identity_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            subprocess.run([
                "git", "apply", "--include=src/libssc-matrix-private.h",
                str(ROOT / "packaging/sources/libssc-matrix-validation.patch"),
            ], cwd=output, check=True, capture_output=True)
            binary = output / "matrix-test"
            subprocess.run([
                "cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-O2",
                "-I", str(output / "src"),
                str(ROOT / "scripts/tests/test_fuxi_libssc_matrix.c"), "-lm", "-o", str(binary),
            ], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, timeout=10, capture_output=True)
