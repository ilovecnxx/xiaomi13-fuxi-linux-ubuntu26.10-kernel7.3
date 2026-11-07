"""Dependency reconstruction must reject changed archives, patches and trees."""

import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("sources", Path(__file__).resolve().parents[1] / "fuxi-sources.py")
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


class SourcesTest(unittest.TestCase):
    def test_reconstruction_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                item = tarfile.TarInfo("source/test.c")
                item.size = 6
                stream.addfile(item, io.BytesIO(b"source"))
            recipe = {"archive": archive.as_uri(), "sha256": sources.sha256(archive), "root": "source"}
            target = root / "output"
            sources.prepare("test", target, recipe, root / "cache")
            sources.check("test", target, recipe)
            (target / "test.c").write_text("changed")
            with self.assertRaises(ValueError):
                sources.prepare("test", target, recipe, root / "cache")
            recipe["sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                sources.prepare("test", root / "other", recipe, root / "cache")

    def test_archive_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "unsafe.tar"
            with tarfile.open(archive, "w") as stream:
                item = tarfile.TarInfo("../../outside")
                item.size = 1
                stream.addfile(item, io.BytesIO(b"x"))
            recipe = {"archive": archive.as_uri(), "sha256": sources.sha256(archive), "root": "source"}
            with self.assertRaises((tarfile.FilterError, ValueError)):
                sources.prepare("test", root / "output", recipe, root / "cache")
