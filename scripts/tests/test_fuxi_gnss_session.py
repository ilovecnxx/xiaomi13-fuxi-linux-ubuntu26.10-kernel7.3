"""Exercise LOC failure recovery and keep provisional positions out of fix counts."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest


PATH = Path(__file__).resolve().parents[2] / "packaging/fuxi-modem/root/usr/sbin/fuxi-gnss-session"
LOADER = importlib.machinery.SourceFileLoader("fuxi_gnss_session", str(PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
GNSS = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(GNSS)


class FakeTransport:
    interrupted = False

    def __init__(self, failures=()):
        self.calls = []
        self.failures = failures

    def invoke(self, name):
        self.calls.append(name)
        if name in self.failures:
            raise TimeoutError("sensitive transport details")

    def open(self, _node):
        self.invoke("open")

    def register(self, _report):
        self.invoke("register")

    def start(self):
        self.invoke("start")

    def sample(self, _seconds):
        self.invoke("sample")

    def stop(self):
        self.invoke("stop")

    def release(self):
        self.invoke("release")

    def close(self):
        self.invoke("close")


class GnssSessionTest(unittest.TestCase):
    def test_successful_session_is_not_automatically_a_fix(self):
        t = FakeTransport()
        report = GNSS.session(t, 0, 1)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["satellite_fix_reports"], 0)
        self.assertEqual(t.calls, ["open", "register", "start", "sample", "stop", "release", "close"])

    def test_failed_start_or_sampling_still_stops_and_releases(self):
        for failure in ("start", "sample"):
            t = FakeTransport([failure])
            report = GNSS.session(t, 0, 1)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["failed_stage"], failure)
            self.assertEqual(t.calls[-3:], ["stop", "release", "close"])
            self.assertNotIn("sensitive", json.dumps(report))

    def test_registration_failure_never_starts_session(self):
        t = FakeTransport(["register"])
        report = GNSS.session(t, 0, 1)
        self.assertEqual(report["failed_stage"], "register")
        self.assertEqual(t.calls, ["open", "register", "release", "close"])

    def test_open_failure_still_releases_partial_resources(self):
        t = FakeTransport(["open"])
        GNSS.session(t, 0, 1)
        self.assertEqual(t.calls, ["open", "release", "close"])

    def test_each_cleanup_failure_is_visible_and_does_not_skip_later_steps(self):
        for failure in ("stop", "release", "close"):
            t = FakeTransport([failure])
            report = GNSS.session(t, 0, 1)
            self.assertEqual(report["status"], "cleanup_failed")
            self.assertEqual(t.calls[-3:], ["stop", "release", "close"])

    def test_interruption_during_sampling_still_stops(self):
        t = FakeTransport()
        def sample(_):
            t.interrupted = True
        t.sample = sample
        report = GNSS.session(t, 0, 1)
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(t.calls[-3:], ["stop", "release", "close"])

    def test_interruption_before_start_never_starts(self):
        t = FakeTransport()
        t.interrupted = True
        report = GNSS.session(t, 0, 1)
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(t.calls, ["open", "release", "close"])

    def position(self, status=0, lat=35.1, lon=139.2, technology=1, satellites=(1,), sid=126):
        return SimpleNamespace(get_session_status=lambda: status, get_session_id=lambda: sid,
                               get_latitude=lambda: lat, get_longitude=lambda: lon,
                               get_technology_used=lambda: technology,
                               get_satellites_used=lambda: satellites)

    def report(self, output):
        report = GNSS.session(FakeTransport(), 0, 1)
        GNSS.position_counts(report, output, 1)
        return report

    def test_in_progress_coordinates_are_not_a_fix(self):
        report = self.report(self.position(status=1))
        self.assertEqual(report["session_status_counts"], {"1": 1})
        self.assertEqual(report["valid_position_reports"], 0)

    def test_invalid_coordinates_are_rejected(self):
        for lat, lon in ((float("nan"), 0), (0, float("inf")), (91, 0), (0, -181)):
            report = self.report(self.position(lat=lat, lon=lon))
            self.assertEqual(report["valid_position_reports"], 0)
            self.assertEqual(report["invalid_reports"], 1)

    def test_satellite_fix_needs_both_technology_and_satellites(self):
        for technology, satellites, expected in ((1, (4,), 1), (2, (4,), 0), (1, (), 0)):
            report = self.report(self.position(technology=technology, satellites=satellites))
            self.assertEqual(report["valid_position_reports"], 1)
            self.assertEqual(report["satellite_fix_reports"], expected)
            text = json.dumps(report)
            self.assertNotIn("35.1", text)
            self.assertNotIn("139.2", text)

    def test_foreign_session_is_ignored(self):
        self.assertEqual(self.report(self.position(sid=1))["position_reports"], 0)

    def preflight(self, enabled=None, loc="16 2 0 0 131"):
        def run(argv, **kwargs):
            self.assertEqual(kwargs["timeout"], 8)
            self.assertTrue(kwargs["check"])
            text = json.dumps({"modem": {"location": {"enabled": enabled}}}) if argv[0] == "mmcli" else loc
            return SimpleNamespace(stdout=text)
        return GNSS.preflight(run)

    def test_preflight_refuses_unknown_or_active_location(self):
        for enabled in (None, "gps-nmea", ["gps-raw"], ["agps-msa"], [42]):
            with self.assertRaises(ValueError):
                self.preflight(enabled)
        self.assertEqual(self.preflight(["3gpp-lac-ci"]), 0)

    def test_preflight_requires_unique_discovered_node(self):
        for loc in ("", "Service Version Instance Node Port", "16 2 0 0 131\n16 2 0 1 132"):
            with self.assertRaises(ValueError):
                self.preflight([], loc)
        self.assertEqual(self.preflight([], "16 2 0 7 131\n16 2 1 7 132"), 7)

    def test_preflight_query_timeout_or_failure_cannot_start_session(self):
        for failure in (subprocess.TimeoutExpired("mmcli", 8),
                        subprocess.CalledProcessError(1, "mmcli")):
            def run(*_args, **_kwargs):
                raise failure
            with self.assertRaises(type(failure)):
                GNSS.preflight(run)

    def test_registration_wire_identity_and_order(self):
        transport = GNSS.Transport.__new__(GNSS.Transport)
        transport.handlers = []
        flags = SimpleNamespace(POSITION_REPORT=1, GNSS_SATELLITE_INFO=2, NMEA=4)
        transport.Qmi = SimpleNamespace(LocEventRegistrationFlag=flags,
                                       LocTechnologyUsed=SimpleNamespace(SATELLITE=1))
        transport.client = SimpleNamespace(connect=lambda *args: 1)
        calls = []
        transport.raw = lambda message, tlvs: calls.append((message, tlvs))
        transport.register({})
        transport.start()
        transport.stop()
        self.assertEqual([c[0] for c in calls], [0x21, 0x22, 0x23])
        self.assertEqual(dict(calls[0][1]), {1: b"\x07\x00\x00\x00\x00\x00\x00\x00",
                                          16: b"MHAL", 17: b"\x01\x00\x00\x00", 18: b"\x00"})
        self.assertEqual(dict(calls[1][1])[1], b"\x7e")
        self.assertEqual(dict(calls[2][1]), {1: b"\x7e"})


if __name__ == "__main__":
    unittest.main()
