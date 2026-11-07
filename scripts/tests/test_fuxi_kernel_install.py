"""Boot-entry lifecycle checks in a temporary root; never touch host /boot."""

import gzip
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "packaging/fuxi-device/root/usr/sbin/fuxi-kernel-install"


class KernelInstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for path in ("boot/fuxi", "etc/kernel", "usr/sbin", "proc"):
            (self.root / path).mkdir(parents=True)
        (self.root / "etc/kernel/cmdline").write_text("root=UUID=test rw panic=10\n")
        (self.root / "proc/cmdline").write_text("fuxi.entry=old\n")
        bootcount = self.root / "usr/sbin/fuxi-bootcount"
        bootcount.write_text(f'#!/bin/sh\necho "$*" >> "{self.root}/bootcount.calls"\n')
        bootcount.chmod(0o755)
        content = SCRIPT.read_text()
        content = content.replace('[ "$(id -u)" = 0 ] || die "要 root"', ":")
        for path in ("/boot/", "/etc/", "/usr/sbin/", "/proc/cmdline"):
            content = content.replace(path, str(self.root) + path)
        self.script = self.root / "installer"
        self.script.write_text(content)
        self.image = self.root / "Image.gz"
        self.image.write_bytes(gzip.compress(b"kernel image"))
        self.dtb = self.root / "board.dtb"
        self.dtb.write_bytes(b"dtb")
        self.entry("old")
        (self.root / "boot/fuxi/default").write_text("old\n")
        self.entry("safe")
        (self.root / "boot/fuxi/fallback").write_text("safe\n")

    def entry(self, name):
        directory = self.root / "boot/fuxi" / name
        directory.mkdir()
        (directory / "Image").write_bytes(b"kernel")
        (directory / "board.dtb").write_bytes(b"dtb")
        (directory / "cmdline").write_text("root=UUID=test rw\n")

    def run_install(self, *args, success=True):
        result = subprocess.run(["sh", str(self.script), *args], text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_add_initrd_extlinux_and_confirmed_fallback(self):
        (self.root / "boot/initrd.img-new").write_bytes(b"initrd")
        self.run_install("add", "new", str(self.image), str(self.dtb))
        directory = self.root / "boot/fuxi/new"
        self.assertEqual((directory / "Image").read_bytes(), b"kernel image")
        self.assertEqual((directory / "initrd").stat().st_ino,
                         (directory / "initrd.img").stat().st_ino)
        self.assertEqual((self.root / "boot/fuxi/fallback").read_text(), "old\n")
        config = (self.root / "boot/extlinux/extlinux.conf").read_text()
        self.assertIn("default new\n", config)
        self.assertIn("initrd " + str(directory / "initrd.img"), config)
        subprocess.run(["sha256sum", "--check", "SHA256SUMS"], cwd=directory,
                       check=True, capture_output=True)
        self.assertEqual((self.root / "bootcount.calls").read_text(), "arm\n")

    def test_unknown_running_entry_does_not_guess_fallback(self):
        (self.root / "proc/cmdline").write_text("root=UUID=test\n")
        self.run_install("add", "new", str(self.image), str(self.dtb))
        self.assertEqual((self.root / "boot/fuxi/fallback").read_text(), "safe\n")

    def test_remove_protects_default_fallback_and_running(self):
        self.entry("running")
        (self.root / "proc/cmdline").write_text("fuxi.entry=running\n")
        for name in ("old", "safe", "running"):
            self.run_install("remove", name, success=False)
            self.assertTrue((self.root / "boot/fuxi" / name).exists())
        self.entry("obsolete")
        self.run_install("remove", "obsolete")
        self.assertFalse((self.root / "boot/fuxi/obsolete").exists())

    def test_refresh_default_initrd_relinks_and_arms_bootcount(self):
        initrd = self.root / "new-initrd"
        initrd.write_bytes(b"updated initrd")
        self.run_install("initrd", "old", str(initrd))
        directory = self.root / "boot/fuxi/old"
        self.assertEqual((directory / "initrd").read_bytes(), b"updated initrd")
        self.assertEqual((directory / "initrd").stat().st_ino,
                         (directory / "initrd.img").stat().st_ino)
        self.assertEqual((self.root / "bootcount.calls").read_text(), "arm\n")

    def test_reject_path_traversal(self):
        self.run_install("add", "../bad", str(self.image), str(self.dtb), success=False)
        self.assertFalse((self.root / "boot/bad").exists())
