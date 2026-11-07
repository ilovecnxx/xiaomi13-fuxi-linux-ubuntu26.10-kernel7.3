"""An unhealthy desktop or failed service must not confirm a new kernel."""

import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "packaging/fuxi-device/root/usr/sbin/fuxi-boot-health"
loader = importlib.machinery.SourceFileLoader("boot_health", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
health = importlib.util.module_from_spec(spec)
loader.exec_module(health)


class BootHealthTest(unittest.TestCase):
    def test_inactive_radio_recovery_keeps_boot_unconfirmed(self):
        for service in ("fuxi-wifi-init", "fuxi-bt-init"):
            with self.subTest(service=service), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "proc").mkdir()
                (root / "boot/fuxi").mkdir(parents=True)
                (root / "proc/cmdline").write_text("fuxi.entry=new")

                def run(command, **kwargs):
                    if command[-1] == service:
                        raise subprocess.CalledProcessError(3, command)
                    return subprocess.CompletedProcess(command, 0)

                with patch.object(health, "Path", side_effect=lambda p: root / str(p).lstrip("/")), \
                     patch.object(health.subprocess, "run", side_effect=run) as mocked, \
                     patch.object(health.subprocess, "check_output", return_value=""):
                    with self.assertRaises(subprocess.CalledProcessError):
                        health.main()
                    self.assertFalse(any("confirm" in call.args[0] for call in mocked.call_args_list))

    def test_failed_service_keeps_boot_unconfirmed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "proc").mkdir()
            (root / "boot/fuxi").mkdir(parents=True)
            (root / "proc/cmdline").write_text("fuxi.entry=new")
            with patch.object(health, "Path", side_effect=lambda p: root / str(p).lstrip("/")), \
                 patch.object(health.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                 patch.object(health.subprocess, "check_output", return_value="broken.service loaded failed failed"):
                with self.assertRaisesRegex(RuntimeError, "failed system services"):
                    health.main()
                self.assertFalse(any("confirm" in call.args[0] for call in run.call_args_list))

    def test_success_confirms_after_display_and_clears_legacy_tries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in ("proc", "boot/fuxi", "sys/class/drm/card0-DSI-1"):
                (root / path).mkdir(parents=True)
            (root / "proc/cmdline").write_text("fuxi.entry=new")
            (root / "boot/fuxi/tries").write_text("1")
            (root / "sys/class/drm/card0-DSI-1/status").write_text("connected")
            with patch.object(health, "Path", side_effect=lambda p: root / str(p).lstrip("/")), \
                 patch.object(health.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                 patch.object(health.subprocess, "check_output", return_value=""):
                health.main()
                self.assertIn("confirm", run.call_args_list[-1].args[0])
                self.assertFalse((root / "boot/fuxi/tries").exists())
