"""Exercise signed publication and failure recovery using real tiny debs."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-apt-publish.py"
spec = importlib.util.spec_from_file_location("publisher", SCRIPT)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class AptPublishTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "packages"
        self.source.mkdir()
        self.target = self.root / "apt"
        package = self.root / "package"
        (package / "DEBIAN").mkdir(parents=True)
        (package / "DEBIAN/control").write_text(
            "Package: fuxi-test\nVersion: 1\nArchitecture: all\nMaintainer: Test <test@localhost>\nDescription: fixture\n")
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(package), str(self.source / "test.deb")],
                       check=True, capture_output=True)

    def test_local_snapshot_and_invalid_update_keeps_previous(self):
        publisher.publish(self.source, self.target, local=True)
        old = self.target.resolve()
        self.assertEqual(old.stat().st_mode & 0o777, 0o755)
        self.assertEqual((old / "Packages").stat().st_mode & 0o777, 0o644)
        (self.source / "bad.deb").write_bytes(b"not a deb")
        with self.assertRaises(subprocess.CalledProcessError):
            publisher.publish(self.source, self.target, local=True)
        self.assertEqual(self.target.resolve(), old)
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=old, check=True, capture_output=True)

    def test_unsigned_public_and_implicit_migration_are_rejected(self):
        with self.assertRaises(ValueError):
            publisher.publish(self.source, self.target)
        self.target.mkdir()
        with self.assertRaises(ValueError):
            publisher.publish(self.source, self.target, local=True)
        publisher.publish(self.source, self.target, local=True, adopt=True)
        self.assertTrue(self.target.is_symlink())

    def test_old_urls_remain_available_after_new_snapshot(self):
        publisher.publish(self.source, self.target, local=True)
        old = self.target.resolve()
        old_hashes = {path.name for path in (old / "by-hash/SHA256").iterdir()}
        (self.source / "test.deb").rename(self.source / "second.deb")
        publisher.publish(self.source, self.target, local=True)
        self.assertNotEqual(self.target.resolve(), old)
        self.assertTrue((self.target / "test.deb").exists())
        self.assertTrue(old_hashes.issubset({p.name for p in (self.target / "by-hash/SHA256").iterdir()}))
        self.assertNotIn("./test.deb", (self.target / "Packages").read_text())

    def test_real_signature_and_tamper_detection(self):
        home = self.root / "gnupg"
        home.mkdir(mode=0o700)
        previous = os.environ.get("GNUPGHOME")
        os.environ["GNUPGHOME"] = str(home)
        self.addCleanup(lambda: os.environ.pop("GNUPGHOME", None) if previous is None
                        else os.environ.__setitem__("GNUPGHOME", previous))
        try:
            subprocess.run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                            "--quick-generate-key", "Fuxi Test <test@localhost>", "ed25519", "sign", "1d"],
                           check=True, capture_output=True)
            publisher.publish(self.source, self.target, key="test@localhost")
            self.assertNotIn("Trusted:", (self.target / "fuxi.sources").read_text())
            release = self.target / "Release"
            release.write_text(release.read_text() + "tampered\n")
            result = subprocess.run(["gpgv", "--keyring", str(self.target / "fuxi-archive-keyring.gpg"),
                                     str(self.target / "Release.gpg"), str(release)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
        finally:
            subprocess.run(["gpgconf", "--kill", "gpg-agent"], capture_output=True)

    def test_reused_package_filename_keeps_previous_snapshot(self):
        publisher.publish(self.source, self.target, local=True)
        old = self.target.resolve()
        control = self.root / "package/DEBIAN/control"
        control.write_text(control.read_text().replace("Version: 1", "Version: 2"))
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(control.parents[1]),
                        str(self.source / "test.deb")], check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, "cannot be reused"):
            publisher.publish(self.source, self.target, local=True)
        self.assertEqual(self.target.resolve(), old)
