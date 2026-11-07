"""Cached state and bounded recovery must preserve modem monitoring on failures."""
import importlib.util
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = (Path(__file__).resolve().parents[2] /
          "packaging/fuxi-modem/root/usr/sbin/fuxi-modem-keeper.py")
spec = importlib.util.spec_from_file_location("modem_keeper", SOURCE)
keeper = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"gi": Mock(), "gi.repository": SimpleNamespace(Gio=Mock(), GLib=Mock())}):
    spec.loader.exec_module(keeper)


class ModemCacheTest(unittest.TestCase):
    def object(self, path, state=11, registration=1, modem=True):
        interfaces = {}
        for name, field, value in ((keeper.IF_MODEM, "State", state),
                                   (keeper.IF_3GPP, "RegistrationState", registration)):
            proxy = Mock()
            proxy.get_cached_property.return_value = (
                Mock(unpack=Mock(return_value=value)) if value is not None else None)
            interfaces[name] = proxy
        if not modem:
            interfaces.pop(keeper.IF_MODEM)
        obj = Mock()
        obj.get_interface.side_effect = interfaces.get
        obj.get_object_path.return_value = path
        return obj

    def controller(self):
        controller = keeper.Keeper.__new__(keeper.Keeper)
        controller.manager = Mock()
        controller.bus = Mock()
        return controller

    def test_cached_values_update_without_synchronous_bus_calls(self):
        controller = self.controller()
        controller.manager.get_objects.return_value = [self.object("/modem/0")]
        self.assertEqual(controller.modems(), {"/modem/0": {
            keeper.IF_MODEM: {"State": 11}, keeper.IF_3GPP: {"RegistrationState": 1}}})
        controller.manager.get_objects.return_value = [self.object("/modem/0", 7, 2)]
        self.assertEqual(controller.modems()["/modem/0"][keeper.IF_3GPP]["RegistrationState"], 2)
        controller.bus.call_sync.assert_not_called()

    def test_removed_owner_and_replacement_modem_follow_current_cache(self):
        controller = self.controller()
        controller.manager.get_objects.side_effect = [
            [self.object("/modem/0")], [], [self.object("/modem/1")]]
        self.assertEqual(set(controller.modems()), {"/modem/0"})
        self.assertEqual(controller.modems(), {})
        self.assertEqual(set(controller.modems()), {"/modem/1"})

    def test_nonmodem_objects_and_missing_properties_do_not_supply_old_state(self):
        controller = self.controller()
        controller.manager.get_objects.return_value = [
            self.object("/sim/0", modem=False), self.object("/modem/0", None, None)]
        self.assertEqual(controller.modems(), {"/modem/0": {keeper.IF_MODEM: {}, keeper.IF_3GPP: {}}})

    def test_disabled_or_unknown_state_never_triggers_radio_recovery(self):
        for state in (keeper.ST_DISABLED, None):
            with self.subTest(state=state):
                controller = self.controller()
                controller.manager.get_objects.return_value = [self.object("/modem/0", state, 0)]
                controller.path = "/modem/0"
                controller.busy = False
                controller.reg_since = None
                controller.unreg_since = 1
                controller.cycle = Mock()
                self.assertTrue(controller.tick())
                controller.cycle.assert_not_called()
                self.assertIsNone(controller.unreg_since)

    def test_registered_cache_keeps_ims_checks_and_does_not_cycle(self):
        controller = self.controller()
        controller.manager.get_objects.return_value = [self.object("/modem/0")]
        controller.path, controller.busy = "/modem/0", False
        controller.unreg_since, controller.reg_since = 1, None
        controller.ims_checked = controller.ims2_checked = 1000
        controller.cycle, controller.check_ims, controller.check_ims2 = Mock(), Mock(), Mock()
        with patch.object(keeper.time, "monotonic", return_value=1000), patch.object(keeper, "log"):
            self.assertTrue(controller.tick())
        controller.cycle.assert_not_called()
        controller.check_ims.assert_called_once_with(1000)
        controller.check_ims2.assert_called_once_with(1000)
        self.assertEqual(controller.poll_interval, keeper.IMS_POLL)
        self.assertIsNone(controller.unreg_since)
        self.assertIsNone(controller.reject_at)

    def test_events_and_early_timer_do_not_postpone_ims_deadline(self):
        for now, expected in ((1010, 20), (1029.8, 1), (1030.1, 1)):
            with self.subTest(now=now):
                controller = self.controller()
                controller.manager.get_objects.return_value = [self.object("/modem/0")]
                controller.path, controller.busy = "/modem/0", False
                controller.unreg_since, controller.reg_since = None, 1000
                controller.ims_checked, controller.ims2_checked = 1000, 1002
                controller.check_ims, controller.check_ims2 = Mock(), Mock()
                with patch.object(keeper.time, "monotonic", return_value=now):
                    controller.tick()
                self.assertEqual(controller.poll_interval, expected)

    def test_ims_cycle_returns_to_short_recovery_poll(self):
        controller = self.controller()
        controller.manager.get_objects.return_value = [self.object("/modem/0")]
        controller.path, controller.busy = "/modem/0", False
        controller.unreg_since, controller.reg_since = None, 1000
        controller.check_ims = Mock(side_effect=lambda _: setattr(controller, "reg_since", None))
        controller.check_ims2 = Mock()
        with patch.object(keeper.time, "monotonic", return_value=1001):
            controller.tick()
        self.assertEqual(controller.poll_interval, keeper.POLL)

    def test_stalled_cache_still_obeys_recovery_threshold(self):
        for lost in (keeper.STALL_LIMIT - 1, keeper.STALL_LIMIT):
            with self.subTest(lost=lost):
                controller = self.controller()
                controller.manager.get_objects.return_value = [self.object("/modem/0", 7, 2)]
                controller.path, controller.busy = "/modem/0", False
                controller.unreg_since, controller.reject_at = 1000 - lost, None
                controller.last_cycle, controller.gap = 0, keeper.MIN_GAP
                controller.cycle = Mock()
                with patch.object(keeper.time, "monotonic", return_value=1000), \
                        patch.object(keeper, "uptime", return_value=1000), patch.object(keeper, "log"):
                    self.assertTrue(controller.tick())
                self.assertEqual(controller.cycle.call_count, int(lost >= keeper.STALL_LIMIT))
                self.assertEqual(controller.poll_interval, keeper.POLL)


class ModemMonitorTest(unittest.TestCase):
    def setUp(self):
        self.controller = Mock(path="/modem/0", poll_interval=keeper.IMS_POLL)
        self.monitor = keeper.Monitor(self.controller)
        self.glib = Mock()
        self.glib.idle_add.side_effect = range(10, 30)
        self.glib.timeout_add_seconds.side_effect = range(30, 60)
        patcher = patch.object(keeper, "GLib", self.glib)
        patcher.start()
        self.addCleanup(patcher.stop)

    def properties(self, interface, changed=None, invalidated=()):
        proxy = Mock()
        proxy.get_interface_name.return_value = interface
        self.monitor.properties_changed(None, None, proxy,
                                        Mock(unpack=Mock(return_value=changed or {})), invalidated)

    def test_event_burst_replaces_timer_with_only_one_idle_dispatch(self):
        self.monitor.dispatch()
        self.glib.timeout_add_seconds.assert_called_once_with(30, self.monitor.dispatch)
        self.properties(keeper.IF_MODEM, {"State": 7})
        self.properties(keeper.IF_3GPP, {"RegistrationState": 2})
        self.monitor.changed()
        self.glib.source_remove.assert_called_once_with(30)
        self.glib.idle_add.assert_called_once_with(self.monitor.dispatch)
        self.controller.tick.assert_called_once()
        self.controller.poll_interval = keeper.POLL
        self.assertFalse(self.monitor.dispatch())
        self.assertEqual(self.controller.tick.call_count, 2)
        self.glib.timeout_add_seconds.assert_called_with(5, self.monitor.dispatch)

    def test_signal_quality_and_unrelated_interfaces_do_not_wake_monitor(self):
        self.properties(keeper.IF_MODEM, {"SignalQuality": (70, True)})
        self.properties(keeper.IF_3GPP, {"OperatorName": "example"})
        self.properties("unrelated", {"State": 7})
        self.glib.idle_add.assert_not_called()

    def test_rejection_and_invalidated_registration_trigger_immediate_check(self):
        self.properties(keeper.IF_3GPP, {"NetworkRejection": {"error": 1}})
        self.glib.idle_add.assert_called_once()
        self.monitor.dispatch()
        self.properties(keeper.IF_3GPP, invalidated=["RegistrationState"])
        self.assertEqual(self.glib.idle_add.call_count, 2)

    def test_owner_replacement_and_removed_current_modem_reset_identity(self):
        self.monitor.owner_changed()
        self.assertIsNone(self.controller.path)
        self.monitor.dispatch()
        self.controller.path = "/modem/0"
        obj = Mock()
        obj.get_object_path.return_value = "/modem/0"
        self.monitor.removed(None, obj)
        self.assertIsNone(self.controller.path)

    def test_removing_another_object_keeps_current_modem_identity(self):
        obj = Mock()
        obj.get_object_path.return_value = "/sim/0"
        self.monitor.removed(None, obj)
        self.assertEqual(self.controller.path, "/modem/0")

    def test_event_during_check_keeps_its_pending_dispatch(self):
        self.controller.tick.side_effect = self.monitor.request
        self.monitor.dispatch()
        self.assertTrue(self.monitor.pending)
        self.glib.idle_add.assert_called_once()
        self.glib.timeout_add_seconds.assert_not_called()

    def test_cancel_removes_source_and_can_be_restarted(self):
        self.monitor.request()
        self.monitor.cancel()
        self.glib.source_remove.assert_called_once_with(10)
        self.assertIsNone(self.monitor.source)
        self.assertFalse(self.monitor.pending)
        self.monitor.request()
        self.assertEqual(self.glib.idle_add.call_count, 2)


class ModemEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.controller = keeper.Keeper.__new__(keeper.Keeper)
        patcher = patch.object(keeper.subprocess, "run")
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def test_known_ims_states_preserve_registration_and_sim2_status(self):
        for state in ("registered", "not-registered", "registering", "limited-registered"):
            with self.subTest(state=state):
                self.run.return_value = SimpleNamespace(stdout=f"  ims_registration_status: {state}\n")
                self.assertEqual(self.controller.ims_registered(), state == "registered")
                self.assertEqual(self.controller.ims2_status(), state)

    def test_missing_malformed_or_ambiguous_ims_status_is_unknown(self):
        for output in ("", "  error: QMI unavailable\n", "ims_registration_status: n/a (failed)\n",
                       "ims_registration_status: future-status\n", "ims_registration_status:\n",
                       "ims_registration_status registered\n",
                       "ims_registration_status: registered\nims_registration_status: registered\n",
                       "ims_registration_status: registered\nims_registration_status:\n",
                       "ims_registration_status: not-registered\nims_registration_status: registered\n"):
            with self.subTest(output=output):
                self.run.return_value = SimpleNamespace(stdout=output)
                self.assertIsNone(self.controller.ims_registered())
                self.assertIsNone(self.controller.ims2_status())

    def test_unrelated_settings_error_does_not_discard_valid_registration(self):
        self.run.return_value = SimpleNamespace(stdout=(
            "IMSA registration:\n  ims_registration_status: registered\n"
            "IMS settings:\n  error: settings unavailable\n"))
        self.assertIs(self.controller.ims_registered(), True)
        self.assertEqual(self.controller.ims2_status(), "registered")

    def test_failed_ims_query_never_supplies_negative_evidence(self):
        for error in (OSError("unavailable"), subprocess.TimeoutExpired("ims", 30),
                      subprocess.CalledProcessError(1, "ims", output=(
                          "ims_registration_status: not-registered\n"))):
            with self.subTest(error=error):
                self.run.side_effect = error
                self.assertIsNone(self.controller.ims_registered())
                self.assertIsNone(self.controller.ims2_status())

    def test_ims_queries_require_success_with_bounded_english_output(self):
        self.run.return_value = SimpleNamespace(stdout="ims_registration_status: registered\n")
        for binding, query in (("0", self.controller.ims_registered), ("1", self.controller.ims2_status)):
            with self.subTest(binding=binding):
                query()
                args, kwargs = self.run.call_args
                self.assertEqual(args, ([keeper.IMS_TOOL, binding],))
                self.assertTrue(kwargs["check"])
                self.assertEqual(kwargs["timeout"], 30)
                self.assertEqual(kwargs["env"]["LC_ALL"], "C")

    def test_sub2_retry_markers_and_confirmed_quiet(self):
        for output, expected in (("", False), ("registered\n", False),
                                 ("retrying start network\n", True), ("wrong PDN\n", True)):
            with self.subTest(output=output):
                self.run.return_value = SimpleNamespace(stdout=output)
                self.assertIs(keeper.sub2_retrying(), expected)

    def test_failed_journal_query_is_unknown(self):
        for error in (OSError("unavailable"), subprocess.TimeoutExpired("journalctl", 20),
                      subprocess.CalledProcessError(1, "journalctl", output="")):
            with self.subTest(error=error):
                self.run.side_effect = error
                self.assertIsNone(keeper.sub2_retrying())

    def test_retry_query_requires_success_and_only_uses_current_boot(self):
        self.run.return_value = SimpleNamespace(stdout="")
        keeper.sub2_retrying()
        args, kwargs = self.run.call_args
        self.assertIn("-b", args[0])
        self.assertIn(f"--since=-{keeper.SUB2_QUIET}s", args[0])
        self.assertTrue(kwargs["check"])
        self.assertEqual(kwargs["timeout"], 20)
        self.assertEqual(kwargs["env"]["LC_ALL"], "C")


class ModemImsDecisionTest(unittest.TestCase):
    def setUp(self):
        self.controller = keeper.Keeper.__new__(keeper.Keeper)
        self.controller.datacard = "1"
        self.controller.reg_since = 1
        self.controller.ims_checked = 0
        self.controller.ims_ok = self.controller.ims_held = False
        self.controller.ims_cycles = 0
        self.controller.ims_registered = Mock(return_value=False)
        self.controller.cycle = Mock()
        for name, value in (("datacard", "1"), ("voltd_active", True), ("uptime", 1000)):
            patcher = patch.object(keeper, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("command", "sub2_retrying", "log"):
            patcher = patch.object(keeper, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.sub2_retrying.return_value = False

    def check(self, cycles):
        self.controller.ims_checked = 0
        self.controller.ims_cycles = cycles
        self.controller.check_ims(1000)

    def test_unknown_ims_never_consumes_recovery_budget_or_checks_retries(self):
        self.controller.ims_registered.return_value = None
        for cycles in (0, 1):
            with self.subTest(cycles=cycles):
                self.check(cycles)
                self.assertEqual(self.controller.ims_cycles, cycles)
        self.command.assert_not_called()
        self.controller.cycle.assert_not_called()
        self.sub2_retrying.assert_not_called()

    def test_active_or_unknown_sub2_retries_hold_both_recovery_stages(self):
        for retrying in (True, None):
            for cycles in (0, 1):
                with self.subTest(retrying=retrying, cycles=cycles):
                    self.controller.reg_since = 1
                    self.controller.ims_held = False
                    self.sub2_retrying.return_value = retrying
                    self.check(cycles)
                    self.assertEqual(self.controller.ims_cycles, cycles)
                    self.assertTrue(self.controller.ims_held)
        self.command.assert_not_called()
        self.controller.cycle.assert_not_called()

    def test_confirmed_quiet_after_unknown_permits_restart_then_radio_recovery(self):
        self.sub2_retrying.return_value = None
        self.check(0)
        self.assertEqual(self.controller.ims_cycles, 0)
        self.sub2_retrying.return_value = False
        with patch.object(keeper.time, "monotonic", return_value=1000):
            self.check(0)
        self.assertFalse(self.controller.ims_held)
        self.command.assert_called_once_with(["systemctl", "restart", "81voltd"], 60)
        self.assertEqual(self.controller.ims_cycles, 1)
        self.controller.cycle.assert_not_called()
        self.controller.reg_since = 1
        self.check(1)
        self.assertEqual(self.controller.ims_cycles, 2)
        self.controller.cycle.assert_called_once_with()

    def test_boot_holdoff_never_queries_journal_or_starts_recovery(self):
        with patch.object(keeper, "uptime", return_value=keeper.BOOT_HOLDOFF - 1):
            self.check(0)
        self.assertTrue(self.controller.ims_held)
        self.assertEqual(self.controller.ims_cycles, 0)
        self.sub2_retrying.assert_not_called()
        self.command.assert_not_called()
        self.controller.cycle.assert_not_called()


class ModemRecoveryTest(unittest.TestCase):
    def setUp(self):
        class BusError(Exception):
            message = "bus unavailable"

        self.bus_error = BusError
        self.controller = keeper.Keeper.__new__(keeper.Keeper)
        self.controller.path = "/modem/0"
        self.controller.busy = False
        self.controller.gap = keeper.MIN_GAP
        self.controller.reject_at = self.controller.reg_since = 1
        self.controller.modem_state = Mock(return_value=keeper.ST_DISABLED)
        self.controller.call = Mock()
        self.controller.set_data_centric = Mock()
        self.commands = []
        self.failures = {}
        self.radio = "enabled\n"
        for patcher in (patch.object(keeper.GLib, "Error", BusError),
                        patch.object(keeper, "log"),
                        patch.object(keeper.time, "sleep"),
                        patch.object(keeper.time, "monotonic", return_value=1000),
                        patch.object(keeper.subprocess, "run", side_effect=self.run_command)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_command(self, args, **kwargs):
        self.commands.append(tuple(args))
        self.assertGreater(kwargs["timeout"], 0)
        self.assertTrue(kwargs["check"])
        self.assertEqual(kwargs["env"]["LC_ALL"], "C")
        failure = self.failures.get(tuple(args))
        if failure:
            raise failure
        return SimpleNamespace(returncode=0, stdout=self.radio)

    def assert_released(self):
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.last_cycle, 1000)
        self.assertEqual(self.controller.unreg_since, 1000)
        self.assertIsNone(self.controller.reject_at)
        self.assertIsNone(self.controller.reg_since)

    def test_success_restores_radio_and_original_active_service(self):
        self.controller.cycle()
        self.assertEqual(self.commands, [
            ("systemctl", "is-active", "--quiet", "81voltd"),
            ("nmcli", "radio", "wwan"), ("systemctl", "stop", "81voltd"),
            ("nmcli", "radio", "wwan", "off"), ("nmcli", "radio", "wwan", "on"),
            ("systemctl", "start", "81voltd")])
        self.controller.set_data_centric.assert_called_once()
        self.assert_released()

    def test_inactive_service_is_neither_stopped_nor_started(self):
        args = ("systemctl", "is-active", "--quiet", "81voltd")
        self.failures[args] = subprocess.CalledProcessError(3, args)
        self.controller.cycle()
        self.assertNotIn(("systemctl", "stop", "81voltd"), self.commands)
        self.assertNotIn(("systemctl", "start", "81voltd"), self.commands)
        self.assertIn(("nmcli", "radio", "wwan", "on"), self.commands)
        self.assert_released()

    def test_disabled_or_unrecognized_radio_is_left_untouched(self):
        for radio in ("disabled\n", "", "unknown\n"):
            with self.subTest(radio=radio):
                self.commands.clear()
                self.radio = radio
                self.controller.cycle()
                self.assertEqual(len(self.commands), 2)
                self.controller.call.assert_not_called()
                self.controller.set_data_centric.assert_not_called()
                self.assert_released()

    def test_unavailable_service_or_radio_state_aborts_without_mutation(self):
        for args in (("systemctl", "is-active", "--quiet", "81voltd"),
                     ("nmcli", "radio", "wwan")):
            for error in (OSError("unavailable"), subprocess.TimeoutExpired(args, 10),
                          subprocess.CalledProcessError(1, args)):
                with self.subTest(args=args, error=error):
                    self.commands.clear()
                    self.failures = {args: error}
                    self.controller.cycle()
                    self.assertEqual(self.commands[-1], args)
                    self.controller.call.assert_not_called()
                    self.assert_released()

    def test_stop_failure_still_restores_service_without_changing_radio(self):
        args = ("systemctl", "stop", "81voltd")
        for error in (OSError("unavailable"), subprocess.TimeoutExpired(args, 60),
                      subprocess.CalledProcessError(1, args)):
            with self.subTest(error=error):
                self.commands.clear()
                self.failures = {args: error}
                self.controller.cycle()
                self.assertEqual(self.commands[-1], ("systemctl", "start", "81voltd"))
                self.assertNotIn(("nmcli", "radio", "wwan", "off"), self.commands)
                self.controller.call.assert_not_called()
                self.assert_released()

    def test_radio_off_and_on_failures_do_not_skip_other_restoration(self):
        for mode in ("off", "on"):
            args = ("nmcli", "radio", "wwan", mode)
            for error in (OSError("unavailable"), subprocess.TimeoutExpired(args, 20),
                          subprocess.CalledProcessError(1, args)):
                with self.subTest(mode=mode, error=error):
                    self.commands.clear()
                    self.controller.call.reset_mock()
                    self.failures = {args: error}
                    self.controller.cycle()
                    self.assertIn(("nmcli", "radio", "wwan", "on"), self.commands)
                    self.assertEqual(self.commands[-1], ("systemctl", "start", "81voltd"))
                    self.assertEqual(self.controller.call.call_args.args[2], "Enable")
                    self.assert_released()

    def test_power_and_enable_bus_errors_still_restore_service(self):
        self.controller.call.side_effect = self.bus_error()
        self.controller.cycle()
        self.assertEqual(self.controller.call.call_count, 2)
        self.assertEqual(self.commands[-1], ("systemctl", "start", "81voltd"))
        self.assert_released()

    def test_service_start_failure_still_updates_recovery_cooldown(self):
        args = ("systemctl", "start", "81voltd")
        for error in (OSError("unavailable"), subprocess.TimeoutExpired(args, 60),
                      subprocess.CalledProcessError(1, args)):
            with self.subTest(error=error):
                self.failures = {args: error}
                self.controller.cycle()
                self.assert_released()

    def test_ims_restart_failure_preserves_tick_and_monitoring(self):
        self.check_ims_restart_failure()

    def check_ims_restart_failure(self, fail_start=False):
        controller = self.controller
        controller.ims_checked = 0
        controller.ims2_checked = 1000
        controller.datacard = "1"
        controller.ims_ok, controller.ims_cycles = False, 0
        controller.reg_since = 1
        controller.ims_registered = Mock(return_value=False)
        controller.manager = Mock()
        controller.manager.get_objects.return_value = [ModemCacheTest().object("/modem/0")]
        controller.unreg_since = None
        controller.check_ims2 = Mock()
        args = ("systemctl", "restart", "81voltd")
        for error in (OSError("unavailable"), subprocess.TimeoutExpired(args, 60),
                      subprocess.CalledProcessError(1, args)):
            with self.subTest(error=error), patch.object(keeper, "datacard", return_value="1"), \
                    patch.object(keeper, "uptime", return_value=1000), \
                    patch.object(keeper, "sub2_retrying", return_value=False):
                controller.ims_checked, controller.ims_cycles, controller.reg_since = 0, 0, 1
                self.failures = {args: error}
                if fail_start:
                    start = ("systemctl", "start", "81voltd")
                    self.failures[start] = subprocess.TimeoutExpired(start, 60)
                self.assertTrue(controller.tick())
                self.assertEqual(controller.reg_since, 1000)
                self.assertEqual(controller.ims_cycles, 1)
                controller.check_ims2.assert_called_with(1000)
                self.assertEqual(self.commands[-1], ("systemctl", "start", "81voltd"))

    def test_failed_ims_restart_and_start_do_not_stop_monitoring(self):
        self.check_ims_restart_failure(fail_start=True)

    def test_ims_monitors_skip_unavailable_service_without_escaping(self):
        controller = self.controller
        controller.ims_checked = controller.ims2_checked = 0
        controller.datacard, controller.ims_ok, controller.ims_cycles = "1", False, 0
        controller.ims_registered, controller.ims2_status = Mock(), Mock()
        args = ("systemctl", "is-active", "--quiet", "81voltd")
        self.failures[args] = subprocess.TimeoutExpired(args, 10)
        with patch.object(keeper, "datacard", return_value="1"):
            controller.check_ims(1000)
            controller.check_ims2(1000)
        controller.ims_registered.assert_not_called()
        controller.ims2_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
