import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-source-state.py"
SPEC = importlib.util.spec_from_file_location("source_state", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SourceStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = self.root / "tree"
        self.tree.mkdir()
        self.git("init", "-q")
        (self.tree / ".gitignore").write_text("*.o\n")
        (self.tree / "driver.c").write_text("original\n")
        (self.tree / "removed").write_text("remove me\n")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@localhost",
                 "commit", "-qm", "base")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.tree), *args])

    def test_dirty_snapshot_replays_binary_new_files_deletions_modes_and_symlinks(self):
        (self.tree / "driver.c").write_text("staged\n")
        self.git("add", "driver.c")
        (self.tree / "driver.c").write_text("actual\n")
        (self.tree / "removed").unlink()
        (self.tree / "new file.bin").write_bytes(bytes(range(256)))
        (self.tree / "tool").write_text("#!/bin/sh\nexit 0\n")
        (self.tree / "tool").chmod(0o755)
        (self.tree / "link").symlink_to("driver.c")
        (self.tree / "ignored.o").write_text("generated")
        index = self.git("ls-files", "--stage")
        output = self.root / "state"
        state = MODULE.capture(self.tree, output)
        self.assertEqual(index, self.git("ls-files", "--stage"))
        self.assertTrue(state["source_dirty"])
        self.assertEqual(state, MODULE.verify(self.tree, output))
        replay = self.root / "replay"
        subprocess.run(["git", "clone", "-q", str(self.tree), str(replay)], check=True)
        subprocess.run(["git", "-C", str(replay), "apply", "--index",
                        str(output / "source.patch")], check=True)
        replay_tree = subprocess.check_output(["git", "-C", str(replay), "write-tree"])
        self.assertEqual(state["source_tree"], replay_tree.decode().strip())
        self.assertFalse((replay / "ignored.o").exists())

    def test_clean_source_and_ignored_build_output_remain_valid(self):
        state = MODULE.capture(self.tree, self.root / "state")
        self.assertFalse(state["source_dirty"])
        (self.tree / "generated.o").write_bytes(b"output")
        MODULE.verify(self.tree, self.root / "state")
        self.assertEqual(b"", (self.root / "state/source.patch").read_bytes())

    def test_changes_after_snapshot_are_rejected(self):
        MODULE.capture(self.tree, self.root / "state")
        (self.tree / "new.c").write_text("changed")
        with self.assertRaisesRegex(ValueError, "source changed"):
            MODULE.verify(self.tree, self.root / "state")

    def test_patch_tampering_is_rejected(self):
        MODULE.capture(self.tree, self.root / "state")
        (self.root / "state/source.patch").write_text("different")
        with self.assertRaisesRegex(ValueError, "patch changed"):
            MODULE.verify(self.tree, self.root / "state")

    def test_capture_refuses_overwrite(self):
        MODULE.capture(self.tree, self.root / "state")
        with self.assertRaises(FileExistsError):
            MODULE.capture(self.tree, self.root / "state")
