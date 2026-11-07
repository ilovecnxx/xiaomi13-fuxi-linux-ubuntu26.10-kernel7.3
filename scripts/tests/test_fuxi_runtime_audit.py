"""Validate read-only ioctls and distinguish missing evidence from disabled config."""
import errno
import gzip
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[2] / "packaging/fuxi-device/root/usr/libexec/fuxi/fuxi-runtime-audit"
LOADER = importlib.machinery.SourceFileLoader("fuxi_runtime_audit", str(PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
AUDIT = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(AUDIT)


class RuntimeAuditTest(unittest.TestCase):
    def test_matching_running_config_and_explicit_disabled_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config-other-kernel").write_text("CONFIG_KVM=y\nCONFIG_TUN=y\n")
            report = AUDIT.kernel_config("running", root, root)
            self.assertEqual(report["status"], "unavailable")
            self.assertIsNone(report["values"]["CONFIG_KVM"])
            (root / "config-running").write_text(
                "# CONFIG_KVM is not set\nCONFIG_TUN=m\nCONFIG_NETFILTER=y\nCONFIG_KVM_EXTRA=y\n")
            report = AUDIT.kernel_config("running", root, root)
            self.assertEqual(report["values"]["CONFIG_KVM"], "n")
            self.assertEqual(report["values"]["CONFIG_TUN"], "m")
            self.assertIsNone(report["values"]["CONFIG_NF_TABLES"])

    def test_proc_config_precedes_boot_and_corrupt_archive_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config-running").write_text("CONFIG_TUN=m\n")
            (root / "config.gz").write_bytes(gzip.compress(b"CONFIG_TUN=y\n"))
            self.assertEqual(AUDIT.kernel_config("running", root, root)["values"]["CONFIG_TUN"], "y")
            (root / "config.gz").write_bytes(b"invalid gzip")
            self.assertEqual(AUDIT.kernel_config("running", root, root)["values"]["CONFIG_TUN"], "m")

    def test_config_decompression_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.gz").write_bytes(gzip.compress(b"x" * 65))
            with patch.object(AUDIT, "CONFIG_LIMIT", 64):
                report = AUDIT.kernel_config("running", root, root)
            self.assertEqual(report["status"], "unavailable")
            self.assertEqual(report["attempts"][0]["status"], "too_large")

    def probe(self, minor, request, ioctl, mode=stat.S_IFCHR):
        device = SimpleNamespace(st_mode=mode, st_rdev=os.makedev(10, minor))
        with patch.object(AUDIT.os, "open", return_value=27) as opened, \
                patch.object(AUDIT.os, "fstat", return_value=device), \
                patch.object(AUDIT.os, "close") as closed, \
                patch.object(AUDIT.fcntl, "ioctl", side_effect=ioctl) as query:
            result = AUDIT.device_api("/dev/test", 232 if request == 0xAE00 else 200, request)
            self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
            closed.assert_called_once_with(27)
        return result, query

    def test_kvm_query_never_creates_vm_and_checks_version(self):
        report, query = self.probe(232, AUDIT.KVM_GET_API_VERSION, lambda *_: 12)
        self.assertEqual(report, {"status": "ok", "api_version": 12})
        query.assert_called_once_with(27, 0xAE00)
        report, _ = self.probe(232, AUDIT.KVM_GET_API_VERSION, lambda *_: 99)
        self.assertEqual(report["status"], "unexpected_api")

    def test_tun_getfeatures_does_not_attach_interface(self):
        def query(fd, request, buffer, mutate):
            self.assertEqual((fd, request, mutate), (27, 0x800454CF, True))
            buffer[:] = struct.pack("=I", 0x1001)
            return 0

        report, call = self.probe(200, AUDIT.TUNGETFEATURES, query)
        self.assertTrue(report["iff_tun"])
        self.assertTrue(report["iff_no_pi"])
        self.assertEqual(call.call_count, 1)
        report, _ = self.probe(200, AUDIT.TUNGETFEATURES, lambda *_: 0)
        self.assertFalse(report["iff_tun"])

    def test_wrong_device_or_regular_file_never_receives_ioctl(self):
        for minor, mode in ((1, stat.S_IFCHR), (232, stat.S_IFREG)):
            report, query = self.probe(minor, AUDIT.KVM_GET_API_VERSION, lambda *_: 12, mode)
            self.assertEqual(report["status"], "unexpected_device")
            query.assert_not_called()

    def test_ioctl_failure_always_closes_fd(self):
        def fail(*_):
            raise OSError(errno.ENOTTY, "not the expected ioctl")

        report, _ = self.probe(232, AUDIT.KVM_GET_API_VERSION, fail)
        self.assertEqual(report, {"status": "failed", "errno": errno.ENOTTY})

    def test_device_open_failure_does_not_close_unowned_descriptor(self):
        for code, status in ((errno.ENOENT, "absent"), (errno.EACCES, "permission_denied"),
                             (errno.ELOOP, "failed")):
            with patch.object(AUDIT.os, "open", side_effect=OSError(code, "private text")), \
                    patch.object(AUDIT.os, "close") as closed:
                report = AUDIT.device_api("/dev/kvm", 232, AUDIT.KVM_GET_API_VERSION)
            self.assertEqual(report["status"], status)
            self.assertNotIn("private", json.dumps(report))
            closed.assert_not_called()

    def test_boot_level_uses_only_current_kernel_log_and_discards_raw_output(self):
        for output, expected in (("CPU: All CPU(s) started at EL1\n", "EL1"),
                                 ("CPU: All CPU(s) started at EL2\n", "EL2"),
                                 ("unrelated EL2 private log", None),
                                 ("CPU: All CPU(s) started at EL1\nCPU: All CPU(s) started at EL2\n", None)):
            with patch.object(AUDIT.subprocess, "run", return_value=SimpleNamespace(
                    stdout=output, returncode=0)) as run:
                report = AUDIT.boot_level()
            self.assertEqual(report["level"], expected)
            self.assertNotIn("private", json.dumps(report))
            self.assertEqual(run.call_args.args[0][1:3], ["-b", "-k"])
            self.assertEqual(run.call_args.kwargs["timeout"], 8)

    def test_missing_log_and_permissions_do_not_imply_el1(self):
        for error, status in ((FileNotFoundError(errno.ENOENT, "journalctl"), "absent"),
                              (PermissionError(errno.EACCES, "denied"), "permission_denied"),
                              (subprocess.TimeoutExpired("journalctl", 8), "timed_out")):
            with patch.object(AUDIT.subprocess, "run", side_effect=error):
                report = AUDIT.boot_level()
            self.assertEqual(report["status"], status)
            self.assertIsNone(report["level"])
        with patch.object(AUDIT.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            self.assertIsNone(AUDIT.boot_level()["level"])

    def test_prerequisites_never_become_guest_or_proxy_validation(self):
        with patch.object(AUDIT, "kernel_config", return_value={
                "status": "unavailable", "values": dict.fromkeys(AUDIT.CONFIG_KEYS)}), \
                patch.object(AUDIT, "device_api", side_effect=[
                    {"status": "ok", "api_version": 12}, {"status": "ok", "iff_tun": True}]), \
                patch.object(AUDIT, "boot_level", return_value={"status": "ok", "level": "EL1"}), \
                patch.object(AUDIT.shutil, "which", return_value="/usr/bin/qemu-system-aarch64"):
            report = AUDIT.collect()
        self.assertEqual(report["kvm"]["guest_execution"], "not_tested")
        self.assertEqual(report["tun"]["interface_creation"], "not_tested")
        self.assertEqual(report["tun"]["proxy_routing"], "not_tested")
        self.assertEqual(report["wayland_guest"], "not_tested")
        self.assertNotIn("running_kernel_kvm_disabled", report["findings"])
        self.assertIn("booted_at_el1_enabling_kvm_config_alone_is_insufficient", report["findings"])


if __name__ == "__main__":
    unittest.main()
