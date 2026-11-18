"""Compile the actual LOC report callback; invalid fixes must never be published."""

from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "packaging/sources/modemmanager-fuxi-loc.patch"


class ModemManagerLocTest(unittest.TestCase):
    def test_initialization_failures(self):
        if not shutil.which("cc") or not shutil.which("pkg-config"):
            self.skipTest("C compiler and pkg-config required")
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "gio-2.0"],
                               capture_output=True, text=True, check=False)
        if flags.returncode:
            self.skipTest("libglib2.0-dev required")
        patch = ROOT / "packaging/sources/modemmanager-loc-initialization.patch"
        resulting = "\n".join(line[1:] for line in patch.read_text().splitlines()
                              if line.startswith(("+", " ")) and not line.startswith("+++"))
        callbacks = []
        for name in ("loc_unlock_location_engine_ready", "qmi_port_allocate_client_ready"):
            callback = re.search(r"static void\n" + name + r" .*?\n}", resulting, re.S)
            self.assertIsNotNone(callback, name)
            callbacks.append(callback[0])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "loc-initialization.inc").write_text("\n".join(callbacks) + "\n")
            binary = target / "loc-initialization-test"
            result = subprocess.run([
                "cc", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                "-I" + str(target),
                str(ROOT / "scripts/tests/fixtures/fuxi_loc_initialization.c"),
                "-o", str(binary), *shlex.split(flags.stdout),
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_decoded_reports_and_raw_dictionary(self):
        if not shutil.which("cc") or not shutil.which("pkg-config"):
            self.skipTest("C compiler and pkg-config required")
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "glib-2.0"],
                               capture_output=True, text=True, check=False)
        if flags.returncode:
            self.skipTest("libglib2.0-dev required; checked in pinned build environment")
        additions = "\n".join(line[1:] for line in PATCH.read_text().splitlines()
                              if line.startswith("+") and not line.startswith("+++"))
        callback = re.search(r"static void\nloc_position_indication_cb .*?\n}",
                             additions, re.S)
        self.assertIsNotNone(callback)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            subprocess.run(["git", "apply", "--include=src/mm-fuxi-loc.h", str(PATCH)],
                           cwd=target, check=True, capture_output=True, timeout=10)
            (target / "loc-callback.inc").write_text(callback[0] + "\n")
            binary = target / "loc-report-test"
            subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                            "-I" + str(target), "-I" + str(target / "src"),
                            str(ROOT / "scripts/tests/fixtures/fuxi_loc_report.c"),
                            "-o", str(binary), *shlex.split(flags.stdout), "-lm"],
                           check=True, capture_output=True, timeout=30)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=10)
