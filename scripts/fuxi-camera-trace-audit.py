#!/usr/bin/env python3
"""Summarize saved camera ftrace evidence without accessing a device."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re


EVENT = re.compile(r"\s(?P<time>\d+\.\d+): (?P<event>\w+): (?P<body>.*)$")
STATUS = re.compile(r"(?P<device>\S+) status=(?P<status>RPM_\w+)$")
SEND = re.compile(
    r"(?P<driver>\S+): tcs\(m\): (?P<tcs>\d+) \[(?P<state>\w+)\] "
    r"cmd\(n\): (?P<index>\d+) msgid: (?P<msgid>0x[0-9a-f]+) "
    r"addr: (?P<addr>0x[0-9a-f]+) data: (?P<data>0x[0-9a-f]+) complete: (?P<wait>[01])$"
)
DONE = re.compile(
    r"(?P<driver>\S+): ack: tcs-m: (?P<tcs>\d+) "
    r"addr: (?P<addr>0x[0-9a-f]+) data: (?P<data>0x[0-9a-f]+)$"
)


def audit(path, device="acb7000.isp", since=0.0):
    counts = Counter()
    transitions = []
    markers = []
    stats = []
    start = end = None
    resuming = None
    durations = []
    pending = {}
    completed = 0
    longest = []
    anomalies = Counter()
    with path.open(errors="replace") as stream:
        for number, line in enumerate(stream, 1):
            match = EVENT.search(line)
            if not match:
                if line.startswith(("overrun:", "dropped events:", "commit overrun:")):
                    stats.append({"line": number, "value": line.strip()})
                continue
            timestamp = float(match["time"])
            if timestamp < since:
                continue
            start = timestamp if start is None else min(start, timestamp)
            end = timestamp if end is None else max(end, timestamp)
            event, body = match["event"], match["body"]
            counts[event] += 1
            if event == "rpmh_send_msg":
                send = SEND.fullmatch(body)
                if send is None:
                    anomalies["unparsed_send"] += 1
                elif send["state"] == "active":
                    key = (send["driver"], int(send["tcs"]))
                    index = int(send["index"])
                    if index == 0:
                        if key in pending:
                            anomalies["reused_tcs_without_observed_completion"] += 1
                        pending[key] = {
                            "driver": key[0], "tcs": key[1], "start": timestamp,
                            "line": number, "commands": [],
                        }
                    batch = pending.get(key)
                    if batch is None:
                        anomalies["command_without_batch_start"] += 1
                    elif index != len(batch["commands"]):
                        anomalies["nonsequential_command"] += 1
                    else:
                        batch["commands"].append({
                            "addr": send["addr"], "data": send["data"],
                            "wait": int(send["wait"]),
                        })
            elif event == "rpmh_tx_done":
                done = DONE.fullmatch(body)
                if done is None:
                    anomalies["unparsed_completion"] += 1
                else:
                    key = (done["driver"], int(done["tcs"]))
                    batch = pending.get(key)
                    if batch is None:
                        anomalies["completion_without_observed_start"] += 1
                    elif (batch["commands"][0]["addr"], batch["commands"][0]["data"]) != (
                        done["addr"], done["data"]
                    ):
                        anomalies["completion_identity_mismatch"] += 1
                    else:
                        pending.pop(key)
                        completed += 1
                        batch["end"] = timestamp
                        batch["seconds"] = round(timestamp - batch["start"], 6)
                        longest.append(batch)
                        longest.sort(key=lambda item: item["seconds"], reverse=True)
                        del longest[10:]
            if event == "tracing_mark_write":
                markers.append({"line": number, "time": timestamp, "text": body})
            if event != "rpm_status":
                continue
            state = STATUS.fullmatch(body)
            if state is None or state["device"] != device:
                continue
            status = state["status"]
            transitions.append({"line": number, "time": timestamp, "status": status})
            if status == "RPM_RESUMING":
                resuming = timestamp
            elif resuming is not None:
                durations.append({
                    "start": resuming, "end": timestamp,
                    "seconds": round(timestamp - resuming, 6),
                    "end_status": status,
                })
                resuming = None
    warnings = []
    if not counts["rpmh_send_msg"] or not counts["rpmh_tx_done"]:
        warnings.append("Missing RPMh send/completion events; cannot identify an unacknowledged batch.")
    if not transitions:
        warnings.append("No runtime-PM status events for the selected device.")
    warnings.append("Event counts and recorded buffer statistics do not prove complete capture.")
    if pending:
        warnings.append("Unmatched active batches are observations, not proof of hardware failure; correlate with timeout logs.")
    return {
        "trace": str(path), "device": device, "since": since,
        "first_event": start, "last_event": end,
        "event_counts": dict(sorted(counts.items())),
        "markers": markers, "pm_transitions": transitions,
        "resume_intervals": durations, "buffer_statistics": stats,
        "rpmh_active_batches_completed": completed,
        "rpmh_active_batches_unmatched": list(pending.values()),
        "rpmh_longest_completed_batches": longest,
        "rpmh_pairing_anomalies": dict(anomalies),
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--device", default="acb7000.isp")
    parser.add_argument("--since", type=float, default=0.0,
                        help="Only analyze events at or after this trace timestamp")
    args = parser.parse_args()
    print(json.dumps(audit(args.trace, args.device, args.since), indent=2))


if __name__ == "__main__":
    main()
