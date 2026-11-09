import importlib.machinery
import importlib.util
import json
import math
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]


def load(name):
    path = ROOT / "packaging/fuxi-desktop/root/usr/bin" / name
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


power = load("fuxi-desktop-power")
light = load("fuxi-auto-brightness")


class DesktopPowerTest(unittest.TestCase):
    def test_merge_preserves_unrelated_settings_and_original_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = {"revision": 42, "power": {"idleSuspendEnabled": False},
                        "appearance": {"wallpaper": "unchanged"}}
            path.write_text(json.dumps(original))
            power.configure(path, lock=False, idle_seconds=120)
            settings = json.loads(path.read_text())
            self.assertEqual(settings["appearance"], original["appearance"])
            self.assertFalse(settings["power"]["idleSuspendEnabled"])
            self.assertFalse(settings["power"]["idleLockEnabled"])
            self.assertEqual(settings["power"]["idleDpmsTimeoutSeconds"], 120)
            power.configure(path, lock=True)
            self.assertEqual(json.loads(path.with_name("settings.before-fuxi-power.json").read_text()), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_bad_json_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("broken")
            with self.assertRaises(ValueError):
                power.configure(path, lock=False)
            self.assertEqual(path.read_text(), "broken")


class AmbientLightTest(unittest.TestCase):
    def test_auto_brightness_tracks_each_supported_desktop_session(self):
        root = ROOT / "packaging/fuxi-desktop/root/usr/lib/systemd/system"
        service = (root / "fuxi-auto-brightness.service").read_text()
        self.assertIn("PartOf=phosh.service denial.service plasmamobile.service", service)
        self.assertNotIn("BindsTo=denial.service", service)
        for session in ("phosh.service.d/20-fuxi-session.conf", "denial.service",
                        "plasmamobile.service"):
            text = (root / session).read_text()
            self.assertRegex(text, r"(?m)^Wants=.*\bfuxi-auto-brightness\.service\b")

    def test_signal_handlers_work_with_current_and_legacy_glib(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                glib = Mock(PRIORITY_DEFAULT=0)
                signal_add = Mock()
                unix = SimpleNamespace() if legacy else SimpleNamespace(signal_add=signal_add)
                glib.unix_signal_add = signal_add if legacy else Mock()
                repository = SimpleNamespace(Gio=Mock(), GLib=glib, GLibUnix=unix)
                controller = Mock()
                glib.MainLoop.return_value.run.side_effect = lambda: signal_add.call_args.args[2]()
                with patch.dict(sys.modules, {"gi": Mock(), "gi.repository": repository}), \
                        patch.object(light, "Controller", return_value=controller):
                    light.main()
                self.assertEqual([call.args[1] for call in signal_add.call_args_list],
                                 [signal.SIGTERM, signal.SIGINT])
                glib.MainLoop.return_value.quit.assert_called_once()
                controller.release.assert_called_once()
                if not legacy:
                    glib.unix_signal_add.assert_not_called()

    def test_curve_is_bounded_and_monotonic(self):
        values = [light.target_brightness(lux, 4095)
                  for lux in (0, 1, 5, 30, 100, 300, 1000, 5000, 20000, 1e9)]
        self.assertEqual(values, sorted(values))
        self.assertGreaterEqual(min(values), 1)
        self.assertLessEqual(max(values), round(4095 * .65))

    def test_invalid_sensor_values_are_rejected(self):
        for value in (-1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                light.target_brightness(value, 4095)

    def test_hysteresis_and_steps(self):
        self.assertEqual(light.next_brightness(1000, 1010, 4095), 1000)
        self.assertEqual(light.next_brightness(1000, 3000, 4095), 1123)
        self.assertEqual(light.next_brightness(1000, 100, 4095), 877)

    def controller(self, root):
        controller = light.Controller.__new__(light.Controller)
        controller.backlight = root
        controller.proxy = Mock()
        controller.proxy.get_name_owner.return_value = ":1.2"
        controller.gio = Mock()
        controller.glib = Mock(Error=RuntimeError)
        controller.claimed = True
        controller.sample = 100
        controller.last_observed = 1000
        controller.adjusted = False
        controller.hold_until = 0
        controller.error = None
        controller.property = lambda name: {"HasAmbientLight": True, "LightLevelUnit": "lux"}[name]
        (root / "bl_power").write_text("0")
        (root / "brightness").write_text("1000")
        (root / "max_brightness").write_text("4095")
        return controller

    def test_screen_off_releases_sensor_and_never_writes_brightness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self.controller(root)
            (root / "bl_power").write_text("4")
            self.assertTrue(controller.tick())
            self.assertFalse(controller.claimed)
            self.assertIsNone(controller.sample)
            self.assertEqual((root / "brightness").read_text(), "1000")
            self.assertEqual(controller.proxy.call_sync.call_args.args[0], "ReleaseLight")

    def test_manual_change_wins_for_a_minute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self.controller(root)
            (root / "brightness").write_text("2000")
            with patch.object(light.time, "monotonic", return_value=100):
                controller.tick()
            self.assertEqual(controller.hold_until, 160)
            self.assertEqual((root / "brightness").read_text(), "2000")

    def test_invalid_measurement_keeps_current_brightness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self.controller(root)
            controller.sample = math.nan
            controller.tick()
            self.assertEqual((root / "brightness").read_text(), "1000")

    def test_first_claim_can_use_a_stable_cached_light_level(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self.controller(root)
            controller.claimed = False
            controller.sample = None
            controller.last_observed = None
            controller.property = lambda name: {"HasAmbientLight": True, "LightLevelUnit": "lux",
                                                 "LightLevel": 100}[name]
            controller.tick()
            self.assertEqual(controller.sample, 100)
            controller.hold_until = 0
            controller.tick()
            self.assertLess(int((root / "brightness").read_text()), 1000)

    def test_manual_change_before_first_adjustment_is_held(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self.controller(root)
            controller.last_observed = None
            initial = light.target_brightness(controller.sample, 4095)
            (root / "brightness").write_text(str(initial))
            with patch.object(light.time, "monotonic", return_value=100):
                controller.tick()
                self.assertFalse(controller.adjusted)
                (root / "brightness").write_text("2000")
                controller.tick()
            self.assertEqual(controller.hold_until, 160)
            self.assertEqual((root / "brightness").read_text(), "2000")

    def test_manual_change_during_initial_sensor_wait_is_held(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self.controller(root)
            controller.claimed = False
            controller.last_observed = None
            controller.property = lambda name: {"HasAmbientLight": True, "LightLevelUnit": "lux",
                                                 "LightLevel": 100}[name]
            with patch.object(light.time, "monotonic", return_value=100):
                controller.tick()
            (root / "brightness").write_text("2000")
            with patch.object(light.time, "monotonic", return_value=101):
                controller.tick()
            self.assertEqual(controller.hold_until, 161)
            self.assertEqual((root / "brightness").read_text(), "2000")

    def test_manual_change_without_sensor_sample_is_still_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self.controller(root)
            controller.sample = None
            (root / "brightness").write_text("2000")
            with patch.object(light.time, "monotonic", return_value=100):
                controller.tick()
            controller.sample = 100
            with patch.object(light.time, "monotonic", return_value=110):
                controller.tick()
            self.assertEqual(controller.hold_until, 160)
            self.assertEqual((root / "brightness").read_text(), "2000")

    def test_hold_expires_and_own_adjustments_do_not_extend_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self.controller(root)
            (root / "brightness").write_text("2000")
            with patch.object(light.time, "monotonic", return_value=100):
                controller.tick()
            with patch.object(light.time, "monotonic", return_value=159):
                controller.tick()
            self.assertEqual((root / "brightness").read_text(), "2000")
            with patch.object(light.time, "monotonic", return_value=160):
                controller.tick()
            first = int((root / "brightness").read_text())
            with patch.object(light.time, "monotonic", return_value=161):
                controller.tick()
            self.assertLess(first, 2000)
            self.assertLess(int((root / "brightness").read_text()), first)
            self.assertEqual(controller.hold_until, 160)


if __name__ == "__main__":
    unittest.main()
