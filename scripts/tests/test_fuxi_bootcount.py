"""Boot confirmation must preserve recovery state unless the entry is valid."""

import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "packaging/fuxi-device/root/usr/sbin/fuxi-bootcount"
loader = importlib.machinery.SourceFileLoader("bootcount", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
bootcount = importlib.util.module_from_spec(spec)
loader.exec_module(bootcount)


class BootcountTest(unittest.TestCase):
    def test_invalid_state_cannot_be_confirmed(self):
        with patch.object(bootcount, "read_state", return_value=None), patch.object(bootcount, "put") as put:
            with self.assertRaises(SystemExit):
                bootcount.cmd_confirm()
            put.assert_not_called()

    def test_unrelated_running_entry_cannot_be_confirmed(self):
        with patch.object(bootcount, "read_state", return_value=(123, 1, 1, 0)), \
             patch.object(bootcount, "cmdline_get", return_value="other"), \
             patch.object(bootcount, "getf", side_effect=lambda name: {"default": "new", "fallback": "safe"}[name]), \
             patch.object(bootcount, "put") as put:
            with self.assertRaises(SystemExit):
                bootcount.cmd_confirm()
            put.assert_not_called()

    def test_fallback_confirmation_passes_shared_lock(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(bootcount, "B", temporary), \
             patch.object(bootcount, "read_state", return_value=(123, 1, 2, 0)), \
             patch.object(bootcount, "cmdline_get", return_value="safe"), \
             patch.object(bootcount, "getf", side_effect=lambda name: {"default": "new", "fallback": "safe"}[name]), \
             patch.object(bootcount.subprocess, "run") as run, patch.object(bootcount, "put") as put:
            bootcount.cmd_confirm()
            run.assert_called_once_with(["/usr/sbin/fuxi-kernel-install", "set-default", "safe"],
                                        check=True, pass_fds=(9,))
            put.assert_called_once_with(0, 0)
            self.assertEqual((Path(temporary) / "default.failed").read_text(), "new\n")
