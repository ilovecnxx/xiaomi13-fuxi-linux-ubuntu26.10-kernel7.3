"""Persist preferences without exposing partial files or switching a real modem."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = (Path(__file__).resolve().parents[2] /
          "packaging/fuxi-modem/root/usr/sbin/fuxi-datacard")
loader = importlib.machinery.SourceFileLoader("datacard_tool", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
datacard = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"gi": Mock(), "gi.repository": SimpleNamespace(
        GLib=Mock(), Qmi=Mock(), Qrtr=Mock())}):
    loader.exec_module(datacard)


class DatacardPersistenceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.state = self.directory / "state" / "datacard"
        patcher = patch.object(datacard, "STATE", str(self.state))
        patcher.start()
        self.addCleanup(patcher.stop)

    def existing(self):
        self.state.parent.mkdir()
        self.state.write_text("1\n")

    def assert_no_temporary(self):
        self.assertEqual(list(self.state.parent.iterdir()), [self.state])

    def test_first_install_and_replacement_use_readable_complete_files(self):
        datacard.save_slot(1)
        self.assertEqual(self.state.read_bytes(), b"1\n")
        datacard.save_slot(2)
        self.assertEqual(self.state.read_bytes(), b"2\n")
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o644)
        self.assert_no_temporary()

    def test_reader_sees_old_file_until_complete_synced_replacement(self):
        self.existing()
        replace, fsync = os.replace, os.fsync
        synced = []

        def sync(fd):
            synced.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            fsync(fd)

        def publish(source, destination):
            self.assertEqual(self.state.read_bytes(), b"1\n")
            self.assertEqual(Path(source).parent, self.state.parent)
            self.assertEqual(Path(source).read_bytes(), b"2\n")
            self.assertEqual(synced, ["file"])
            replace(source, destination)

        with patch.object(datacard.os, "replace", side_effect=publish), \
                patch.object(datacard.os, "fsync", side_effect=sync):
            datacard.save_slot(2)
        self.assertEqual(synced, ["file", "directory"])
        self.assertEqual(self.state.read_bytes(), b"2\n")
        self.assert_no_temporary()

    def test_file_sync_or_replace_failure_preserves_old_preference_and_cleans_temp(self):
        self.existing()
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation), \
                    patch.object(datacard.os, operation, side_effect=OSError("I/O error")):
                with self.assertRaises(OSError):
                    datacard.save_slot(2)
                self.assertEqual(self.state.read_bytes(), b"1\n")
                self.assert_no_temporary()

    def test_failed_first_save_leaves_preference_absent(self):
        with patch.object(datacard.os, "fsync", side_effect=OSError("full")):
            with self.assertRaises(OSError):
                datacard.save_slot(2)
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.state.parent.iterdir()), [])

    def test_directory_sync_failure_reports_error_after_publication(self):
        self.existing()
        fsync = os.fsync

        def sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("directory sync failed")
            fsync(fd)

        with patch.object(datacard.os, "fsync", side_effect=sync), \
                patch.object(datacard, "sh") as shell, patch.object(datacard, "Modem") as modem:
            with self.assertRaises(OSError):
                datacard.set_slot(2)
        self.assertEqual(self.state.read_bytes(), b"2\n")
        self.assert_no_temporary()
        shell.assert_not_called()
        modem.assert_not_called()

    def test_persistence_failure_never_starts_service_or_changes_dds(self):
        for slot in (1, 2):
            with self.subTest(slot=slot), \
                    patch.object(datacard, "save_slot", side_effect=OSError("full")), \
                    patch.object(datacard, "sh") as shell, patch.object(datacard, "Modem") as modem, \
                    patch.object(datacard, "nm_slot1") as nm:
                with self.assertRaises(OSError):
                    datacard.set_slot(slot)
                shell.assert_not_called()
                modem.assert_not_called()
                nm.assert_not_called()

    def test_invalid_slots_do_not_create_state_or_call_services(self):
        for slot in (0, 3, "1", None):
            with self.subTest(slot=slot), patch.object(datacard, "sh") as shell, \
                    patch.object(datacard, "Modem") as modem:
                with self.assertRaises(ValueError):
                    datacard.set_slot(slot)
                self.assertFalse(self.state.parent.exists())
                shell.assert_not_called()
                modem.assert_not_called()


if __name__ == "__main__":
    unittest.main()
