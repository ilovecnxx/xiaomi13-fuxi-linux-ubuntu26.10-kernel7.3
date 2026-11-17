#!/usr/bin/env python3
"""Run one logind suspend with an RTC guard and an isolated wake trace.

Run as root in a system service, outside user.slice and the SSH session:
systemd-run --unit=fuxi-suspend-check python3 /path/to/fuxi-suspend-check.py \
    --output /var/tmp/fuxi-suspend-check-result
Evidence is private and must not be committed.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time


RTC = Path("/sys/class/rtc/rtc0")
TRACE = Path("/sys/kernel/tracing/instances")
PLATFORM_STATS = Path("/sys/kernel/debug/qcom_stats")
SLEEP_STATS = ("aosd", "cxsd", "apss", "modem", "adsp", "cdsp", "slpi")
LOGIN = "org.freedesktop.login1"
MANAGER = LOGIN + ".Manager"
OBJECT = "/org/freedesktop/login1"


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=15).stdout


class SleepCycle:
    def __init__(self):
        self.requested = False
        self.prepared = False
        self.resumed = False
        self.events = []

    def observe(self, sleeping):
        self.events.append({"sleeping": sleeping,
                            "boottime": time.clock_gettime(time.CLOCK_BOOTTIME),
                            "monotonic": time.monotonic()})
        if sleeping and self.requested:
            self.prepared = True
        elif not sleeping and self.prepared:
            self.resumed = True

    @property
    def may_clear_alarm(self):
        # A request can be queued even when the caller fails or times out.
        return not self.requested or self.resumed


def wait_for_resume(cycle, dispatch, seconds, clock=None, pause=time.sleep):
    clock = clock or (lambda: time.clock_gettime(time.CLOCK_BOOTTIME))
    deadline = clock() + seconds
    while not cycle.resumed:
        dispatch()
        if cycle.resumed:
            return
        if clock() >= deadline:
            raise TimeoutError("No completed logind sleep cycle; RTC guard retained")
        pause(0.05)


class WakeTrace:
    REQUIRED_EVENTS = (
        "power/suspend_resume", "power/wakeup_source_activate",
        "power/wakeup_source_deactivate", "power/device_pm_callback_start",
        "power/device_pm_callback_end", "irq/irq_handler_entry",
        "irq/irq_handler_exit",
    )
    PLATFORM_EVENTS = (
        "rpmh/rpmh_send_msg", "rpmh/rpmh_tx_done", "qcom_aoss/aoss_send",
        "qcom_aoss/aoss_send_done", "qcom_smp2p/smp2p_notify_in",
        "qcom_smp2p/smp2p_update_bits", "qcom_smp2p/smp2p_ssr_ack",
    )

    def __init__(self, output):
        self.output = output
        self.path = TRACE / f"fuxi-suspend-{os.getpid()}"
        self.created = False

    def __enter__(self):
        self.path.mkdir()
        self.created = True
        try:
            self.write("tracing_on", "0")
            self.write("trace_clock", "boot")
            self.write("buffer_size_kb", "4096")
            enabled = list(self.REQUIRED_EVENTS)
            missing = []
            for event in self.PLATFORM_EVENTS:
                if (self.path / f"events/{event}/enable").exists():
                    enabled.append(event)
                else:
                    missing.append(event)
            for event in enabled:
                self.write(f"events/{event}/enable", "1")
            (self.output / "trace-config.json").write_text(json.dumps({
                "irq_scope": "all handlers, including local timers",
                "clock": "boot", "buffer_kb_per_cpu": 4096,
                "enabled_events": enabled, "missing_platform_events": missing,
            }, indent=2) + "\n")
            self.write("tracing_on", "1")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def write(self, name, value):
        (self.path / name).write_text(value + "\n")

    def __exit__(self, *_):
        if self.created:
            try:
                self.write("tracing_on", "0")
                # Save loss counters before consuming the trace buffer.
                for stats in sorted((self.path / "per_cpu").glob("cpu*/stats")):
                    (self.output / f"trace-{stats.parent.name}-stats.txt").write_text(
                        stats.read_text())
                (self.output / "trace.txt").write_text((self.path / "trace").read_text())
            finally:
                self.path.rmdir()


def snapshot_platform_stats(output, phase):
    report = {"boottime_begin": time.clock_gettime(time.CLOCK_BOOTTIME),
              "stats": {}, "errors": {}}
    # Do not read ddr_stats: that interface can send a QMP frequency-sync request.
    for name in SLEEP_STATS:
        try:
            report["stats"][name] = (PLATFORM_STATS / name).read_text()
        except OSError as error:
            report["errors"][name] = {"errno": error.errno}
    report["boottime_end"] = time.clock_gettime(time.CLOCK_BOOTTIME)
    (output / f"{phase}-platform-sleep.json").write_text(
        json.dumps(report, indent=2) + "\n")


def snapshot(output, phase):
    for name, source in {
        "interrupts": "/proc/interrupts",
        "wakeup-sources": "/sys/kernel/debug/wakeup_sources",
        "rtc-alarm": str(RTC / "wakealarm"),
        "rtc-time": str(RTC / "since_epoch"),
    }.items():
        (output / f"{phase}-{name}.txt").write_text(Path(source).read_text())
    snapshot_platform_stats(output, phase)


def check_idle():
    if (RTC / "wakealarm").read_text().strip():
        raise RuntimeError("Existing RTC alarm; refusing to replace it")
    if (RTC / "device/power/wakeup").read_text().strip() != "enabled":
        raise RuntimeError("RTC wakeup is not enabled")
    if "[deep]" not in Path("/sys/power/mem_sleep").read_text():
        raise RuntimeError("Deep suspend is not selected")
    state = run("systemctl", "show", "systemd-suspend.service", "--value",
                "--property=ActiveState").strip()
    if state != "inactive":
        raise RuntimeError(f"Suspend service is not inactive: {state}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=20, choices=range(10, 61),
                        metavar="10..60")
    parser.add_argument("--output", required=True, type=Path,
                        help="new private evidence directory")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("root required")
    from gi.repository import Gio, GLib

    os.umask(0o077)
    lock = os.open("/run/fuxi-suspend-check.lock",
                   os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        args.output.mkdir(mode=0o700)
        check_idle()
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        if bus.call_sync(LOGIN, OBJECT, "org.freedesktop.DBus.Properties", "Get",
                         GLib.Variant("(ss)", (MANAGER, "PreparingForSleep")),
                         None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]:
            raise RuntimeError("logind is already preparing for sleep")
        cycle = SleepCycle()
        subscription = bus.signal_subscribe(
            LOGIN, MANAGER, "PrepareForSleep", OBJECT, None,
            Gio.DBusSignalFlags.NONE,
            lambda _bus, _sender, _path, _interface, _signal, params:
                cycle.observe(params.unpack()[0]))
        bus.flush_sync(None)
        report = {"seconds": args.seconds, "result": "incomplete"}
        armed = False
        try:
            with WakeTrace(args.output) as trace:
                try:
                    # rtcwake uses the hardware clock, which need not match wall time.
                    # A timed-out rtcwake may already have programmed the alarm.
                    armed = True
                    report["rtcwake"] = run("rtcwake", "-m", "no", "-s", str(args.seconds))
                    snapshot(args.output, "before")
                    trace.write("trace_marker", "fuxi logind suspend request")
                    cycle.requested = True
                    bus.call_sync(LOGIN, OBJECT, MANAGER, "Suspend",
                                  GLib.Variant("(b)", (False,)), None,
                                  Gio.DBusCallFlags.NONE, 10000, None)
                    context = GLib.MainContext.default()

                    def dispatch():
                        while context.pending():
                            context.iteration(False)

                    wait_for_resume(cycle, dispatch, args.seconds + 45)
                    trace.write("trace_marker", "fuxi logind resume complete")
                    report["result"] = "resumed"
                finally:
                    snapshot(args.output, "after")
        finally:
            bus.signal_unsubscribe(subscription)
            report["events"] = cycle.events
            report["alarm_retained"] = armed and not cycle.may_clear_alarm
            if armed and cycle.may_clear_alarm:
                (RTC / "wakealarm").write_text("0\n")
            (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report), flush=True)
    finally:
        os.close(lock)


if __name__ == "__main__":
    main()
