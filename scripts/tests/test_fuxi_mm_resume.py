"""Exercise the maintained watcher with controlled asynchronous QRTR completions."""

from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "packaging/sources/modemmanager-suspend-resume.patch"


def patched_text(name):
    """Read resulting lines from the patch; watcher hunks include the whole file."""
    text = PATCH.read_text().split("+++ b/" + name + "\n", 1)[1]
    text = text.split("\n--- a/", 1)[0]
    return "\n".join(line[1:] for line in text.splitlines()
                     if line.startswith((" ", "+"))) + "\n"


class ModemManagerResumeTest(unittest.TestCase):
    def test_async_discovery_and_authorization_lifecycle(self):
        if not shutil.which("cc") or not shutil.which("pkg-config"):
            self.skipTest("C compiler and pkg-config required")
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "gio-2.0"],
                               capture_output=True, text=True, check=False)
        if flags.returncode:
            self.skipTest("libglib2.0-dev required; checked in build environment")
        manager = patched_text("src/mm-base-manager.c")
        callbacks = []
        for name in ("resume_authorization", "manager_cleanup_context_maybe_complete"):
            match = re.search(r"static void\n" + name + r" .*?\n}", manager, re.S)
            self.assertIsNotNone(match, name)
            callbacks.append(match[0])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in ("mm-qrtr-bus-watcher.c", "mm-qrtr-bus-watcher.h"):
                (target / name).write_text(patched_text("src/" + name))
            for name in ("ModemManager.h", "libqrtr-glib.h", "libqmi-glib.h", "mm-utils.h"):
                (target / name).write_text("/* Supplied by the lifecycle fixture. */\n")
            (target / "manager-callbacks.inc").write_text("\n".join(callbacks) + "\n")
            binary = target / "mm-resume-test"
            result = subprocess.run([
                "cc", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                "-DGLIB_DISABLE_DEPRECATION_WARNINGS", "-I" + str(target),
                str(ROOT / "scripts/tests/fixtures/fuxi_mm_resume.c"),
                "-o", str(binary), *shlex.split(flags.stdout),
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
