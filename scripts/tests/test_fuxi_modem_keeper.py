"""Cached modem state must preserve recovery decisions without periodic bus calls."""
import importlib.util
from pathlib import Path
import sys
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
        controller.cycle, controller.check_ims, controller.check_ims2 = Mock(), Mock(), Mock()
        with patch.object(keeper.time, "monotonic", return_value=1000), patch.object(keeper, "log"):
            self.assertTrue(controller.tick())
        controller.cycle.assert_not_called()
        controller.check_ims.assert_called_once_with(1000)
        controller.check_ims2.assert_called_once_with(1000)
        self.assertIsNone(controller.unreg_since)
        self.assertIsNone(controller.reject_at)

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


if __name__ == "__main__":
    unittest.main()
