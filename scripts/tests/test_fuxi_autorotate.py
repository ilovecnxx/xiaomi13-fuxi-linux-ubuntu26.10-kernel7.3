import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "packaging/fuxi-desktop/root/usr/bin/fuxi-autorotate"
loader = importlib.machinery.SourceFileLoader("autorotate", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
rotate = importlib.util.module_from_spec(spec)
loader.exec_module(rotate)


class AutoRotatePolicyTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is needed to evaluate polkit JavaScript")
    def test_policy_grants_only_hardened_system_service_sensor_claim(self):
        policy = ROOT / "packaging/fuxi-desktop/root/usr/share/polkit-1/rules.d/50-fuxi-autorotate.rules"
        result = subprocess.run(["node", "-e", """
const fs = require('fs');
let rule;
global.polkit = {addRule: callback => { rule = callback; }, Result: {YES: 'yes'}};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const action = {id: 'net.hadess.SensorProxy.claim-sensor'};
const subject = {user: 'fuxi', system_unit: 'fuxi-autorotate.service', no_new_privileges: true};
const results = [rule(action, subject)];
for (const change of [{user: 'other'}, {system_unit: 'user@1000.service'},
                      {system_unit: 'fuxi-autorotate-validation.service'},
                      {no_new_privileges: false}, {system_unit: undefined}]) {
    results.push(rule(action, {...subject, ...change}));
}
results.push(rule({id: 'org.freedesktop.policykit.exec'}, subject));
console.log(JSON.stringify(results));
""", str(policy)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), ["yes"] + [None] * 6)


class AutoRotateTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.lock = Path(directory.name) / "state/orientation-lock"
        self.gio = Mock()
        self.glib = Mock(Error=RuntimeError)
        self.glib.idle_add.return_value = 7
        self.loop = Mock()
        self.proxy = self.gio.DBusProxy.new_for_bus_sync.return_value
        self.proxy.get_name_owner.return_value = ":1.2"
        self.values = {"HasAccelerometer": True, "AccelerometerOrientation": "normal"}
        self.proxy.get_cached_property.side_effect = lambda name: (
            SimpleNamespace(unpack=lambda: self.values[name]) if name in self.values else None)
        patcher = patch.object(rotate.subprocess, "run")
        self.run = patcher.start()
        self.addCleanup(patcher.stop)
        self.controller = rotate.Controller(self.gio, self.glib, self.loop, self.lock)

    def methods(self):
        return [call.args[0] for call in self.proxy.call_sync.call_args_list]

    def test_initial_claim_and_all_orientation_mappings(self):
        for orientation, rotation in rotate.MAP.items():
            self.values["AccelerometerOrientation"] = orientation
            self.controller.update()
            self.assertEqual(self.run.call_args.args[0],
                             ["kscreen-doctor", f"output.DSI-1.rotation.{rotation}"])
        self.assertEqual(self.methods(), ["ClaimAccelerometer"])
        self.assertEqual(self.run.call_args.kwargs, dict(capture_output=True, check=True, timeout=5))

    def test_unchanged_or_invalid_orientation_never_spawns_command(self):
        self.controller.update()
        self.run.reset_mock()
        for orientation in ("normal", "undefined", None):
            self.values["AccelerometerOrientation"] = orientation
            self.controller.update()
        self.run.assert_not_called()

    def test_lock_releases_and_unlock_reapplies_same_direction(self):
        self.controller.update()
        self.lock.touch()
        self.controller.update()
        self.assertFalse(self.controller.claimed)
        self.assertEqual(self.methods(), ["ClaimAccelerometer", "ReleaseAccelerometer"])
        self.lock.unlink()
        self.controller.update()
        self.assertTrue(self.controller.claimed)
        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(self.methods()[-1], "ClaimAccelerometer")

    def test_locked_start_does_not_claim_or_rotate(self):
        self.lock.touch()
        self.controller.update()
        self.assertEqual(self.methods(), [])
        self.run.assert_not_called()

    def test_missing_sensor_or_owner_does_not_claim(self):
        self.values.pop("HasAccelerometer")
        self.controller.update()
        self.values["HasAccelerometer"] = True
        self.proxy.get_name_owner.return_value = None
        self.controller.update()
        self.assertEqual(self.methods(), [])
        self.run.assert_not_called()

    def test_sensor_removal_releases_and_restoration_reapplies(self):
        self.controller.update()
        self.values["HasAccelerometer"] = False
        self.controller.update()
        self.assertFalse(self.controller.claimed)
        self.values["HasAccelerometer"] = True
        self.controller.update()
        self.assertEqual(self.methods(), ["ClaimAccelerometer", "ReleaseAccelerometer",
                                         "ClaimAccelerometer"])
        self.assertEqual(self.run.call_count, 2)

    def test_release_error_still_clears_local_state_and_exits(self):
        self.controller.update()
        self.lock.touch()
        self.proxy.call_sync.side_effect = RuntimeError("release rejected")
        self.controller.update()
        self.assertFalse(self.controller.claimed)
        self.assertIsNone(self.controller.applied)
        self.loop.quit.assert_called_once()

    def test_owner_replacement_reclaims_and_reapplies(self):
        self.controller.update()
        self.controller.owner_changed()
        self.controller.update()
        self.assertEqual(self.methods(), ["ClaimAccelerometer", "ClaimAccelerometer"])
        self.assertEqual(self.run.call_count, 2)

    def test_only_relevant_events_schedule_coalesced_updates(self):
        self.controller.update()
        self.glib.idle_add.reset_mock()
        self.controller.properties_changed(None, Mock(unpack=lambda: {"LightLevel": 3}), [])
        self.controller.lock_changed(None, Mock(get_basename=lambda: "unrelated"), None, None)
        self.glib.idle_add.assert_not_called()
        self.controller.properties_changed(None, Mock(unpack=lambda: {}), ["AccelerometerOrientation"])
        self.controller.lock_changed(None, None, Mock(get_basename=lambda: self.lock.name), None)
        self.glib.idle_add.assert_called_once()

    def test_failed_or_timed_out_display_command_exits_for_service_retry(self):
        for error in (FileNotFoundError("kscreen-doctor"),
                      subprocess.CalledProcessError(1, ["kscreen-doctor"]),
                      subprocess.TimeoutExpired(["kscreen-doctor"], 5)):
            with self.subTest(error=error):
                self.run.side_effect = error
                self.controller.update()
                self.assertIsNone(self.controller.applied)
                self.assertIs(self.controller.error, error)
        self.assertEqual(self.loop.quit.call_count, 3)

    def test_failed_claim_never_rotates(self):
        self.proxy.call_sync.side_effect = RuntimeError("claim rejected")
        self.controller.update()
        self.assertFalse(self.controller.claimed)
        self.run.assert_not_called()
        self.loop.quit.assert_called_once()

    def test_close_cancels_pending_update_and_releases_once(self):
        self.controller.update()
        self.controller.schedule()
        self.controller.close()
        self.glib.source_remove.assert_called_once_with(7)
        self.gio.File.new_for_path.return_value.monitor_directory.return_value.cancel.assert_called_once()
        self.assertEqual(self.methods(), ["ClaimAccelerometer", "ReleaseAccelerometer"])
        self.controller.release()
        self.assertEqual(len(self.methods()), 2)

    def test_current_and_legacy_glib_stop_cleanly(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                glib = Mock(PRIORITY_DEFAULT=0)
                signal_add = Mock()
                unix = SimpleNamespace() if legacy else SimpleNamespace(signal_add=signal_add)
                glib.unix_signal_add = signal_add if legacy else Mock()
                repository = SimpleNamespace(Gio=Mock(), GLib=glib, GLibUnix=unix)
                controller = Mock(error=None)
                glib.MainLoop.return_value.run.side_effect = lambda: signal_add.call_args.args[2]()
                with patch.dict(sys.modules, {"gi": Mock(), "gi.repository": repository}), \
                        patch.object(rotate, "Controller", return_value=controller):
                    self.assertEqual(rotate.main(), 0)
                self.assertEqual([call.args[1] for call in signal_add.call_args_list],
                                 [signal.SIGTERM, signal.SIGINT])
                controller.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
