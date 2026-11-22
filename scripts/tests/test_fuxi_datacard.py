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

    def test_suspend_twice_then_restore_original_profile_and_settings(self):
        settings = {datacard.NM_CON: "no", datacard.AUTO_NM_CON: "yes"}
        active = [datacard.AUTO_NM_CON]

        def shell(command, check=False):
            fields = command.split()
            if "mod" in fields:
                settings[fields[-3]] = fields[-1]
            elif "down" in fields:
                active.remove(fields[-1])
            elif "up" in fields:
                active[:] = [fields[-1]]
            else:
                self.fail(command)
            return SimpleNamespace(returncode=0)

        with patch.object(datacard, "primary_names", return_value=list(settings)), \
                patch.object(datacard, "primary_settings", side_effect=lambda _: settings.copy()), \
                patch.object(datacard, "primary_active", side_effect=lambda _: active.copy()), \
                patch.object(datacard, "sh", side_effect=shell):
            datacard.nm_slot1(False)
            snapshot = Path(str(self.state) + ".nm-restore")
            original = snapshot.read_bytes()
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
            self.assertEqual(active, [])
            self.assertEqual(set(settings.values()), {"no"})
            datacard.nm_slot1(False)
            self.assertEqual(snapshot.read_bytes(), original)
            active.append(datacard.NM_CON)
            datacard.nm_slot1(True)
            self.assertEqual(active, [datacard.AUTO_NM_CON])
            self.assertEqual(settings, {datacard.NM_CON: "no", datacard.AUTO_NM_CON: "yes"})
            self.assertFalse(snapshot.exists())

    def test_link_without_address_times_out(self):
        result = SimpleNamespace(stdout="qmapmux0.8 UP\n", returncode=0)
        with patch.object(datacard, "sh", return_value=result), patch.object(datacard.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                datacard.set_slot(2)
            result.stdout = "41: qmapmux0.8    inet 192.0.2.4/30 scope global qmapmux0.8\n"
            self.assertEqual(datacard.secondary_address(), "192.0.2.4/30")
            result.returncode = 1
            self.assertIsNone(datacard.secondary_address())

    def test_duplicate_or_ambiguous_profile_queries_fail_before_mutation(self):
        name = datacard.AUTO_NM_CON
        with patch.object(datacard, "sh", return_value=SimpleNamespace(stdout=f"{name}\n{name}\n")):
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                datacard.nm_slot1(False)
        self.assertFalse(Path(str(self.state) + ".nm-restore").exists())
        with patch.object(datacard, "sh", return_value=SimpleNamespace(stdout="deactivated")):
            self.assertEqual(datacard.primary_active([name]), [])


class DatacardReplyTest(unittest.TestCase):
    def setUp(self):
        self.modem = object.__new__(datacard.Modem)
        self.modem.dev = Mock()
        self.modem.dsd = Mock()
        self.request = (42, 9, 17, 79)
        self.qmi = Mock()
        self.qmi.message_is_response.return_value = True
        self.qmi.message_get_service.side_effect = lambda msg: msg[0]
        self.qmi.message_get_client_id.side_effect = lambda msg: msg[1]
        self.qmi.message_get_transaction_id.side_effect = lambda msg: msg[2]
        self.qmi.message_get_message_id.side_effect = lambda msg: msg[3]
        self.qmi.message_new.return_value = self.request
        self.modem.dev.command_full_finish.return_value = self.request
        self.qmi.message_tlv_read_init.return_value = (12, 4)
        self.qmi.message_tlv_read_guint16.side_effect = [(True, 2, 0), (True, 4, 0)]
        modules = SOURCE.parents[1] / 'lib/python3/dist-packages'
        self.enterContext(patch.object(sys, 'path', [str(modules), *sys.path]))
        self.enterContext(patch.object(datacard, 'Qmi', self.qmi))
        self.enterContext(patch.object(datacard, 'wait'))

    def test_wrong_direction_or_identity_cannot_select_dds(self):
        for index in range(len(self.request)):
            reply = list(self.request)
            reply[index] += 1
            self.modem.dev.command_full_finish.return_value = reply
            with self.subTest(index=index), self.assertRaisesRegex(RuntimeError, 'header'):
                self.modem.set_dds(2)
        self.modem.dev.command_full_finish.return_value = self.request
        self.qmi.message_is_response.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'header'):
            self.modem.set_dds(2)
        self.qmi.message_tlv_write_guint32.assert_not_called()

    def test_confirmed_slot_and_invalid_readback(self):
        for valid, slot in ((True, 1), (True, 2), (False, 1), (True, 0), (True, 3)):
            self.qmi.message_tlv_read_guint16.side_effect = [(True, 2, 0), (True, 4, 0)]
            self.qmi.message_tlv_read_guint32.return_value = (valid, 4, slot)
            with self.subTest(valid=valid, slot=slot):
                if valid and slot in (1, 2):
                    self.assertEqual(self.modem.get_dds(), slot)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'Unknown DDS'):
                        self.modem.get_dds()
        self.qmi.message_tlv_read_guint16.side_effect = [(True, 2, 0), (True, 4, 0)]
        self.qmi.message_tlv_read_init.side_effect = [(12, 4), (19, 2)]
        with self.assertRaisesRegex(RuntimeError, 'DDS subscription length'):
            self.modem.get_dds()

    def test_malformed_result_cannot_confirm_nas_binding(self):
        for length, status, error in ((2, 0, 0), (4, 0, 3), (4, 1, 0)):
            self.qmi.message_tlv_read_init.return_value = (12, length)
            self.qmi.message_tlv_read_guint16.side_effect = [(True, 2, status), (True, 4, error)]
            with self.subTest(length=length, status=status, error=error), self.assertRaises(RuntimeError):
                self.modem.raw_u8(Mock(), Mock(), 69, 1, 1)


class DatacardWaitTest(unittest.TestCase):
    def test_mismatched_subscription_never_binds_mux_or_removes_existing_interface(self):
        modem = Mock()
        session = datacard.Session(modem)
        bind, readback = Mock(), Mock()
        readback.get_subscription_id.return_value = datacard.Qmi.SubscriptionType.PRIMARY
        with patch.object(datacard, 'call', side_effect=[bind, readback]) as calls, \
                patch.object(datacard, 'sh') as shell:
            with self.assertRaisesRegex(RuntimeError, 'secondary subscription'):
                session.up()
            session.teardown()
            self.assertEqual([entry.args[0] for entry in calls.call_args_list],
                             ['bind_subscription', 'get_bind_subscription'])
            modem.release.assert_called_once_with(modem.alloc.return_value)
            shell.assert_not_called()

    def test_post_mux_binding_change_prevents_start_network(self):
        session = datacard.Session(Mock())
        secondary, primary = Mock(), Mock()
        secondary.get_subscription_id.return_value = datacard.Qmi.SubscriptionType.SECONDARY
        primary.get_subscription_id.return_value = datacard.Qmi.SubscriptionType.PRIMARY
        replies = [Mock(), secondary, Mock(), Mock(), Mock(), primary]
        with patch.object(datacard, 'call', side_effect=replies) as calls, patch.object(datacard, 'sh') as shell:
            with self.assertRaisesRegex(RuntimeError, 'secondary subscription'):
                session.up()
            self.assertEqual([entry.args[0] for entry in calls.call_args_list],
                             ['bind_subscription', 'get_bind_subscription', 'bind_mux_data_port',
                              'set_ip_family', 'bind_subscription', 'get_bind_subscription'])
            session.teardown()
            shell.assert_not_called()

    def test_teardown_only_once_after_link_creation(self):
        session = datacard.Session(Mock())
        session.link_created = True
        with patch.object(datacard, 'sh') as shell:
            session.teardown()
            session.teardown()
        shell.assert_called_once_with(f'ip link del {datacard.IF}')

    class MainLoop:
        def __init__(self):
            self.action = None
            self.quit_called = False

        def run(self):
            if self.action is not None:
                self.action()

        def quit(self):
            self.quit_called = True

    def setUp(self):
        self.loop = self.MainLoop()
        self.callbacks = {}
        self.removed = []
        self.patches = [
            patch.object(datacard.GLib.MainLoop, "new", return_value=self.loop),
            patch.object(datacard.GLib, "timeout_add_seconds",
                         side_effect=self.add_timeout),
            patch.object(datacard.GLib, "source_remove", side_effect=self.removed.append),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_timeout(self, _seconds, callback):
        source = len(self.callbacks) + 1
        self.callbacks[source] = callback
        return source

    def test_packet_status_event_quits_wait_without_polling(self):
        session = datacard.Session(None)
        session.alive = Mock(side_effect=AssertionError("event path must not poll"))
        datacard.Qmi.WdsConnectionStatus.CONNECTED = 0
        event = SimpleNamespace(get_connection_status=lambda: (True, 1))
        self.loop.action = lambda: session.on_status(None, event)
        active_loop = [None]

        session.wait_until_down(lambda: False, active_loop)

        self.assertTrue(session.down)
        self.assertTrue(self.loop.quit_called)
        self.assertEqual(active_loop, [None])
        self.assertIsNone(session.loop)
        self.assertEqual(self.removed, [1])

    def test_timeout_status_fallback_quits_and_source_is_removed_once(self):
        session = datacard.Session(None)
        session.alive = Mock(return_value=False)
        active_loop = [None]
        self.loop.action = lambda: self.assertTrue(self.callbacks[1]())

        session.wait_until_down(lambda: False, active_loop)

        session.alive.assert_called_once_with()
        self.assertTrue(session.down)
        self.assertTrue(self.loop.quit_called)
        self.assertEqual(self.removed, [1])
        self.assertEqual(active_loop, [None])

    def test_stop_signal_quits_wait_and_cleans_timeout(self):
        session = datacard.Session(None)
        stop = []
        active_loop = [None]
        self.loop.action = lambda: self.assertFalse(
            datacard.request_stop(stop, active_loop))

        session.wait_until_down(lambda: bool(stop), active_loop)

        self.assertEqual(stop, [1])
        self.assertTrue(self.loop.quit_called)
        self.assertEqual(self.removed, [1])
        self.assertEqual(active_loop, [None])
        self.assertIsNone(session.loop)


if __name__ == "__main__":
    unittest.main()
