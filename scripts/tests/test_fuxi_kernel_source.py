"""Exercise source reconstruction with a tiny real Git repository and tarball."""

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "fuxi_kernel_source", Path(__file__).resolve().parents[1] / "fuxi-kernel-source.py")
SOURCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE)


class KernelSourceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.catalog = self.root / "kernel"
        (self.catalog / "patches").mkdir(parents=True)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@localhost")
        (self.repo / "Makefile").write_text("VERSION = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()
        base_tree = self.git("rev-parse", "HEAD^{tree}").decode().strip()
        self.archive = self.root / "linux.tar.gz"
        with tarfile.open(self.archive, "w:gz") as archive:
            archive.add(self.repo / "Makefile", arcname="linux-test/Makefile")
        (self.repo / "Makefile").write_text("VERSION = 2\n")
        self.git("add", ".")
        self.git("commit", "-qm", "update")
        content = self.git("diff", "--binary", "--full-index", self.base, "HEAD")
        (self.catalog / "patches/0001-test.patch").write_bytes(content)
        self.manifest = {
            "archive_root": "linux-test",
            "archive_sha256": SOURCE.digest(self.archive),
            "base_tree": base_tree,
            "tree": self.git("rev-parse", "HEAD^{tree}").decode().strip(),
            "source_date_epoch": 1700000000,
            "patches": [{"file": "0001-test.patch", "sha256": hashlib.sha256(content).hexdigest()}],
        }
        self.save_manifest()
        self.target = self.root / "prepared"
        self.args = argparse.Namespace(tree=self.target, archive=self.archive)
        self.catalog_patch = patch.object(SOURCE, "CATALOG", self.catalog)
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args])

    def save_manifest(self):
        (self.catalog / "manifest.json").write_text(json.dumps(self.manifest))

    def test_reconstruct_and_reuse_verified_tree(self):
        SOURCE.prepare(self.args)
        self.assertEqual((self.target / "Makefile").read_text(), "VERSION = 2\n")
        first = SOURCE.git(self.target, "rev-parse", "HEAD")
        SOURCE.prepare(self.args)
        self.assertEqual(SOURCE.git(self.target, "rev-parse", "HEAD"), first)

    def test_refuse_modified_existing_source(self):
        SOURCE.prepare(self.args)
        (self.target / "Makefile").write_text("local work\n")
        with self.assertRaisesRegex(ValueError, "dirty"):
            SOURCE.prepare(self.args)
        self.assertEqual((self.target / "Makefile").read_text(), "local work\n")

    def test_reject_tampered_patch_before_creating_target(self):
        (self.catalog / "patches/0001-test.patch").write_text("tampered\n")
        with self.assertRaisesRegex(ValueError, "Patch checksum"):
            SOURCE.prepare(self.args)
        self.assertFalse(self.target.exists())

    def test_reject_bad_archive_checksum(self):
        self.manifest["archive_sha256"] = "0" * 64
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "Archive checksum"):
            SOURCE.prepare(self.args)
        self.assertFalse(self.target.exists())

    def test_reject_wrong_base_tree_and_clean_temporary_directory(self):
        self.manifest["base_tree"] = "0" * 40
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "Archive tree"):
            SOURCE.prepare(self.args)
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.root.glob(".fuxi-source-*")))

    def test_reject_archive_path_escape(self):
        with tarfile.open(self.archive, "w:gz") as archive:
            member = tarfile.TarInfo("../escape")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        self.manifest["archive_sha256"] = SOURCE.digest(self.archive)
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "Unexpected archive path"):
            SOURCE.prepare(self.args)
        self.assertFalse((self.root / "escape").exists())

    def test_export_rejects_unassigned_changes(self):
        (self.catalog / "series.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "Unassigned"):
            SOURCE.export(argparse.Namespace(tree=self.repo, base=self.base))
        self.assertTrue((self.catalog / "patches/0001-test.patch").exists())

    def test_preserve_safe_symlink_text_exactly(self):
        with tarfile.open(self.archive, "w:gz") as archive:
            member = tarfile.TarInfo("linux-test/link")
            member.type = tarfile.SYMTYPE
            member.linkname = "./Makefile"
            archive.addfile(member)
        target = self.root / "extract"
        target.mkdir()
        SOURCE.extract_archive(self.archive, target, "linux-test")
        self.assertEqual(os.readlink(target / "linux-test/link"), "./Makefile")
