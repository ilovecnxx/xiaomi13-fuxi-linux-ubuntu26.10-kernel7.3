"""Exercise LOC failure recovery and keep provisional positions out of fix counts."""
import importlib.machinery
import importlib.util
import json
import struct
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


class FakeModemManager:
    interrupted = False

    def __init__(self, enabled=1, signals=False, capabilities=3):
        self.state = {"Enabled": enabled, "SignalsLocation": signals,
                      "Capabilities": capabilities}
        self.calls = []

    def properties(self):
        return self.state.copy()

    def setup(self, enabled, signals):
        self.calls.append((enabled, signals))
        self.state.update(Enabled=enabled, SignalsLocation=signals)

    def sample(self, _seconds):
        pass


class ModemManagerSessionTest(unittest.TestCase):
    def test_standard_session_restores_original_sources_and_signalling(self):
        for enabled in (0, 1):
            for signals in (False, True):
                t = FakeModemManager(enabled, signals)
                report = GNSS.mm_session(t, 1)
                self.assertEqual(report["status"], "completed")
                self.assertTrue(report["cleanup"]["restored"])
                self.assertEqual(t.calls, [(enabled | 2, signals), (enabled, signals)])
                self.assertEqual(report["satellite_fix"], "not_tested")

    def test_busy_unknown_or_unsupported_modem_is_never_changed(self):
        for enabled, caps in ((3, 3), (4, 7), (16, 19), (None, 3), (1, 1)):
            t = FakeModemManager(enabled, capabilities=caps)
            self.assertEqual(GNSS.mm_session(t, 1)["status"], "preflight_failed")
            self.assertEqual(t.calls, [])

    def test_start_timeout_after_server_enabled_gps_still_restores(self):
        t = FakeModemManager()
        setup = t.setup
        def timeout_after_enable(enabled, signals):
            setup(enabled, signals)
            if enabled & 2:
                raise TimeoutError("sensitive server details")
        t.setup = timeout_after_enable
        report = GNSS.mm_session(t, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failed_stage"], "enable")
        self.assertTrue(report["cleanup"]["restored"])
        self.assertEqual(t.state["Enabled"], 1)
        self.assertNotIn("sensitive", json.dumps(report))

    def test_sampling_failure_or_signal_always_restores(self):
        for interrupted in (False, True):
            t = FakeModemManager()
            def sample(_):
                if interrupted:
                    t.interrupted = True
                else:
                    raise RuntimeError("private details")
            t.sample = sample
            report = GNSS.mm_session(t, 1)
            self.assertEqual(report["status"], "interrupted" if interrupted else "failed")
            self.assertTrue(report["cleanup"]["restored"])

    def test_other_clients_changes_are_preserved_and_reported(self):
        t = FakeModemManager()
        t.sample = lambda _: t.state.update(Enabled=7, SignalsLocation=True)
        report = GNSS.mm_session(t, 1)
        self.assertEqual(t.calls[-1], (5, True))
        self.assertEqual(report["status"], "cleanup_failed")
        self.assertFalse(report["cleanup"]["restored"])

    def test_cleanup_timeout_is_visible(self):
        t = FakeModemManager()
        def fail_cleanup(_):
            t.properties = lambda: (_ for _ in ()).throw(TimeoutError())
        t.sample = fail_cleanup
        self.assertEqual(GNSS.mm_session(t, 1)["status"], "cleanup_failed")

    def test_signal_before_start_never_changes_configuration(self):
        t = FakeModemManager()
        t.interrupted = True
        self.assertEqual(GNSS.mm_session(t, 1)["status"], "interrupted")
        self.assertEqual(t.calls, [])


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

    def test_precision_evidence_requires_fields_on_same_satellite_fix(self):
        for status, tech, radius, utc, confidence, expected in (
                (1, 1, 4.5, 1, 68, (0, 0, 0)), (0, 2, 4.5, 1, 68, (0, 0, 0)),
                (0, 1, float('inf'), 1, 68, (0, 0, 0)), (0, 1, 0, 1, 68, (0, 0, 0)),
                (0, 1, 4.5, 0, 68, (1, 0, 0)), (0, 1, 4.5, 1, 100, (1, 1, 0)),
                (0, 1, 4.5, 1, 68, (1, 1, 1))):
            output = self.position(status=status, technology=tech, satellites=())
            output.get_horizontal_uncertainty_circular = lambda: radius
            output.get_utc_timestamp = lambda: utc
            output.get_horizontal_confidence = lambda: confidence
            report = self.report(output)
            self.assertEqual(tuple(report['satellite_position_with_radius' + suffix]
                                   for suffix in ('', '_utc', '_utc_confidence')), expected)
        self.assertEqual(self.report(self.position())['satellite_position_with_radius'], 0)

    def test_sv_signal_requires_valid_flag_and_finite_positive_value(self):
        report = GNSS.session(FakeTransport(), 0, 1)
        for flags, snr in ((0, 24.5), (128, float('nan')), (128, 0), (128, 24.5)):
            sv = SimpleNamespace(valid_information=flags, signal_to_noise_ratio_bhz=snr)
            GNSS.sv_counts(report, SimpleNamespace(get_list=lambda: [sv]), 128, lambda _: False)
        GNSS.sv_counts(report, SimpleNamespace(get_list=lambda: []), 128, lambda _: False)
        GNSS.sv_counts(report, SimpleNamespace(), 128, lambda _: False)
        GNSS.sv_counts(report, SimpleNamespace(), 128, lambda _: True)
        self.assertEqual(tuple(report[k] for k in ('sv_reports', 'sv_reports_with_entries',
                         'sv_reports_with_signal', 'sv_reports_unreadable',
                         'sv_reports_without_legacy_list')), (7, 4, 1, 1, 1))
        self.assertEqual(report['satellite_fix_reports'], 0)

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

    def test_expanded_sv_wire_reports(self):
        transport = GNSS.Transport.__new__(GNSS.Transport)
        transport.Qmi = SimpleNamespace(Service=SimpleNamespace(LOC=16), CID_BROADCAST=255)
        transport.client = SimpleNamespace(get_cid=lambda: 3)
        report = GNSS.session(FakeTransport(), 0, 1)
        def send(data, cid=3, extra=b''):
            payload = b'' if data is None else struct.pack('<BH', 0x11, len(data)) + data
            payload += extra
            header = struct.pack('<BHBBBBHHH', 1, 12 + len(payload), 128, 16, cid, 4, 1, 0x25, len(payload))
            transport.expanded_sv(None, header + payload, report)
        def entry(flags, snr):
            return struct.pack('<IIHBIBfffB', flags, 1, 7, 0, 0, 0, 0, 0, snr, 0)
        send(None)
        send(b'\0')
        send(b'\1')
        send(b'\2' + entry(0, 30) + entry(128, float('nan')))
        send(b'\2' + entry(128, 0) + entry(128, 30), cid=255)
        send(b'\1' + entry(128, 30), cid=4)
        send(b'\0', extra=b'\x12\x18\0')
        send(b'\0', extra=b'\x11\x01\0\0')
        self.assertEqual([report[k] for k in ('expanded_sv_lists', 'expanded_sv_lists_with_entries',
                         'expanded_sv_lists_with_signal', 'expanded_sv_list_absent',
                         'expanded_sv_list_errors', 'satellite_fix_reports')], [3, 2, 1, 1, 3, 0])

    def test_registration_wire_identity_and_order(self):
        transport = GNSS.Transport.__new__(GNSS.Transport)
        transport.handlers = []
        flags = SimpleNamespace(POSITION_REPORT=1, GNSS_SATELLITE_INFO=2, NMEA=4)
        transport.Qmi = SimpleNamespace(LocEventRegistrationFlag=flags,
                                       LocSatelliteValidInformation=SimpleNamespace(SIGNAL_TO_NOISE_RATIO=128),
                                       LocTechnologyUsed=SimpleNamespace(SATELLITE=1))
        transport.client = SimpleNamespace(connect=lambda *args: 1)
        transport.device = SimpleNamespace(connect=lambda *args: 1)
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
