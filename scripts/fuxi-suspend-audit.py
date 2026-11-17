#!/usr/bin/env python3
"""Audit a saved suspend-check directory without accessing the phone."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re


EVENT = re.compile(r"\s(\d+\.\d+): (\w+): (.*)$")
SEND = re.compile(
    r"(\S+): tcs\(m\): (\d+) \[(sleep|wake|active)\] cmd\(n\): (\d+) "
    r"msgid: (\w+) addr: (\w+) data: (\w+) complete: ([01])$")
LOSS_KEYS = ("overrun", "commit overrun", "dropped events")


def command_db(text):
    resources = {}
    slave = None
    for line in text.splitlines():
        if line.startswith("Slave "):
            slave = line.split()[1]
        match = re.fullmatch(r"(0x[0-9a-fA-F]+): (\S+)(?: \[.*\])?", line)
        if match and slave:
            address = int(match[1], 16)
            resource = resources.setdefault(address, {"slave": slave, "names": []})
            if resource["slave"] != slave:
                raise ValueError(f"Ambiguous command DB slave for {match[1]}")
            if match[2] not in resource["names"]:
                resource["names"].append(match[2])
    return resources


def read_events(path):
    events = []
    with path.open(errors="replace") as stream:
        for number, line in enumerate(stream, 1):
            match = EVENT.search(line)
            if match:
                events.append({"line": number, "time": float(match[1]),
                               "event": match[2], "body": match[3]})
    return events


def one_marker(events, name):
    matches = [event for event in events if event["event"] == "suspend_resume"
               and event["body"] == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one {name}, found {len(matches)}")
    return matches[0]


def trace_health(directory, warnings):
    header = (directory / "before-interrupts.txt").read_text().splitlines()[0]
    cpus = re.findall(r"\bCPU(\d+)\b", header)
    if not cpus:
        warnings.append("No CPU list in before-interrupts.txt; loss coverage unknown.")
    counters = {}
    complete = bool(cpus)
    for cpu in cpus:
        path = directory / f"trace-cpu{cpu}-stats.txt"
        values = {}
        if path.exists():
            for line in path.read_text().splitlines():
                key, separator, value = line.partition(":")
                if separator and key in LOSS_KEYS and value.strip().isdigit():
                    values[key] = int(value)
        counters[f"cpu{cpu}"] = values
        complete &= all(key in values for key in LOSS_KEYS)
    lost = any(value for values in counters.values() for value in values.values())
    if not complete:
        warnings.append("Missing per-CPU loss counters; zero loss is not established.")
    if lost:
        warnings.append("Trace loss detected; event absence cannot exclude a source.")
    config_path = directory / "trace-config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    required = {"power/suspend_resume", "power/device_pm_callback_end",
                "irq/irq_handler_entry", "rpmh/rpmh_send_msg",
                "qcom_aoss/aoss_send", "qcom_aoss/aoss_send_done"}
    missing = sorted(required - set(config.get("enabled_events", [])))
    if missing:
        warnings.append("Required audit events not recorded as enabled: " + ", ".join(missing))
    if config.get("irq_scope") != "all handlers, including local timers":
        warnings.append("Unfiltered IRQ coverage is not recorded in trace-config.json.")
    if config.get("clock") != "boot":
        warnings.append("Expected boot trace clock is not recorded in trace-config.json.")
    return {"cpu_loss_counters": counters, "loss_counters_complete": complete,
            "loss_detected": lost, "missing_audit_events": missing}


def audit(directory, db_path=None):
    warnings = []
    events = read_events(directory / "trace.txt")
    enter = one_marker(events, "machine_suspend[3] begin")
    leave = one_marker(events, "machine_suspend[3] end")
    start = one_marker(events, "syscore_suspend[0] begin")
    end = one_marker(events, "syscore_suspend[0] end")
    if not start["line"] < end["line"] < enter["line"] < leave["line"]:
        raise ValueError("Suspend markers are out of order")
    db_path = db_path or directory / "before-cmd-db.txt"
    resources = command_db(db_path.read_text()) if db_path.exists() else {}
    if not resources:
        warnings.append("No command DB mapping; resource names and BCM decoding unavailable.")
    commands = {"sleep": [], "wake": [], "active": []}
    unparsed = 0
    for event in events:
        if not start["line"] < event["line"] < end["line"]:
            continue
        if event["event"] != "rpmh_send_msg":
            continue
        match = SEND.fullmatch(event["body"])
        if not match:
            unparsed += 1
            continue
        driver, tcs, state, index, msgid, address, data, wait = match.groups()
        address, data = int(address, 16), int(data, 16)
        command = {"driver": driver, "tcs": int(tcs), "index": int(index),
                   "address": f"0x{address:x}", "data": f"0x{data:x}",
                   "msgid": msgid, "wait": bool(int(wait)), "line": event["line"]}
        resource = resources.get(address)
        if resource:
            command["resource"] = resource
            if resource["slave"] == "BCM":
                command["bcm"] = {"commit": bool(data & (1 << 30)),
                                  "valid": bool(data & (1 << 29)),
                                  "vote_x": (data >> 14) & 0x3fff,
                                  "vote_y": data & 0x3fff}
        commands[state].append(command)
    if unparsed:
        warnings.append(f"Unparsed RPMh commands in syscore suspend: {unparsed}")
    if not commands["sleep"] or not commands["wake"]:
        warnings.append("No new sleep/wake pair observed; cached TCS data may still exist.")
    unknown = [command for command in commands["sleep"] if "bcm" not in command]
    nonzero = [command for command in commands["sleep"] if "bcm" in command
               and (command["bcm"]["vote_x"] or command["bcm"]["vote_y"])]
    zero = (bool(commands["sleep"]) and not unknown and not nonzero and not unparsed)
    errors = [event for event in events if event["line"] < enter["line"]
              and event["event"] == "device_pm_callback_end"
              and (match := re.search(r"\berr=(-?\d+)\b", event["body"]))
              and int(match[1]) != 0]
    aoss = Counter(event["event"] for event in events if event["line"] < enter["line"]
                   and event["event"] in ("aoss_send", "aoss_send_done"))
    irqs = [event for event in events if event["line"] > leave["line"]
            and event["event"] == "irq_handler_entry"][:6]
    warnings.extend([
        "TCS programming does not prove firmware executed the votes or platform collapse.",
        "First traced IRQs after return do not identify the earliest wake source.",
        "Trace timekeeping pauses during sleep; machine timestamps are not residency.",
    ])
    health = trace_health(directory, warnings)
    return {"trace_health": health, "syscore_suspend": {"begin": start, "end": end},
            "machine_suspend": {"begin": enter, "end": leave},
            "rpmh_syscore_commands": commands, "unparsed_rpmh_commands": unparsed,
            "all_observed_sleep_commands_are_zero_bcm_votes": zero,
            "sleep_commands_without_bcm_decode": unknown,
            "sleep_bcm_nonzero_votes": nonzero, "callback_errors_before_entry": errors,
            "aoss_events_before_entry": {key: aoss[key] for key in ("aoss_send", "aoss_send_done")},
            "first_traced_irqs_after_return": irqs, "wake_cause": None, "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--cmd-db", type=Path, help="Saved read-only debugfs cmd-db dump")
    args = parser.parse_args()
    try:
        report = audit(args.evidence, args.cmd_db)
    except (OSError, ValueError, IndexError) as error:
        parser.exit(2, f"Cannot audit suspend evidence: {error}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
