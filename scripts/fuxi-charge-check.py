#!/usr/bin/env python3
"""Check net battery charge on fuxi using read-only, bounded sysfs sampling."""

import argparse
import json
from pathlib import Path
import time


ROOT = Path("/sys/class/power_supply")
FIELDS = {
    "battery": ("qcom-battmgr-bat", (
        "status", "capacity", "charge_counter", "current_now", "voltage_now", "temp")),
    "usb": ("qcom-battmgr-usb", (
        "online", "usb_type", "voltage_now", "current_now",
        "current_max", "input_current_limit")),
}


def snapshot(root=ROOT):
    sample = {"boottime": time.clock_gettime(time.CLOCK_BOOTTIME), "errors": {}}
    for group, (name, fields) in FIELDS.items():
        values = sample[group] = {}
        for field in fields:
            try:
                raw = (root / name / field).read_text().strip()
                values[field] = raw if field in ("status", "usb_type") else int(raw)
            except (OSError, ValueError) as error:
                sample["errors"][f"{group}.{field}"] = str(error)
    sample["end_boottime"] = time.clock_gettime(time.CLOCK_BOOTTIME)
    return sample


def summarize(samples):
    report = {
        "result": "insufficient_evidence",
        "sample_count": len(samples),
        "charge_delta_uah": None,
        "elapsed_seconds": None,
        "usb_input_watts_range": None,
        "fast_charge_verified": False,
    }
    if len(samples) < 2 or any(s["errors"] for s in samples):
        return report
    elapsed = samples[-1]["boottime"] - samples[0]["boottime"]
    if elapsed < 30 or any(b["boottime"] <= a["end_boottime"]
                           for a, b in zip(samples, samples[1:])):
        return report
    report["elapsed_seconds"] = elapsed
    # Use battmgr measurements, never UCSI's declared current or PDO voltages.
    watts = [s["usb"]["voltage_now"] * s["usb"]["current_now"] / 1e12
             for s in samples]
    report["usb_input_watts_range"] = [min(watts), max(watts)]
    if any(s["usb"]["online"] != 1 for s in samples):
        report["result"] = "usb_not_continuously_online"
        return report
    if len({s["usb"]["usb_type"] for s in samples}) != 1:
        report["result"] = "usb_type_changed"
        return report
    charge = [s["battery"]["charge_counter"] for s in samples]
    current = [s["battery"]["current_now"] for s in samples]
    delta = charge[-1] - charge[0]
    report["charge_delta_uah"] = delta
    if delta > 0 and all(i > 0 for i in current) and all(
            b >= a for a, b in zip(charge, charge[1:])):
        report["result"] = "net_charging"
    elif delta < 0 and all(i < 0 for i in current) and all(
            b <= a for a, b in zip(charge, charge[1:])):
        report["result"] = "net_discharging"
    else:
        report["result"] = "inconclusive"
    return report


def collect(seconds, interval):
    samples = [snapshot()]
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
        samples.append(snapshot())
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()
    if not 30 <= args.seconds <= 600 or not 5 <= args.interval <= 60:
        parser.error("seconds must be 30..600 and interval 5..60")
    samples = collect(args.seconds, args.interval)
    report = summarize(samples)
    print(json.dumps({"summary": report, "samples": samples}, indent=2))
    return 0 if report["result"] == "net_charging" else 1


if __name__ == "__main__":
    raise SystemExit(main())
