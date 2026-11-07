#!/usr/bin/env python3
"""Experimental cpufreq requests; hardware frequency and energy savings are unverified."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time


CPUFREQ = Path("/sys/devices/system/cpu/cpufreq")


def inspect(root=CPUFREQ):
    policies = []
    for path in sorted(root.glob("policy[0-9]*")):
        policy = {"policy": path.name}
        for field in ("scaling_min_freq", "scaling_max_freq", "cpuinfo_min_freq", "cpuinfo_max_freq"):
            policy[field] = int((path / field).read_text())
        for field in ("scaling_driver", "scaling_governor", "affected_cpus"):
            policy[field] = (path / field).read_text().strip()
        policy["available_frequencies"] = sorted(set(map(
            int, (path / "scaling_available_frequencies").read_text().split())))
        policies.append(policy)
    if not policies:
        raise ValueError("no CPU frequency policies found")
    return policies


def plan(policies, profile):
    if profile not in ("balanced", "power-saver"):
        raise ValueError("unknown CPU profile")
    changes = []
    for policy in policies:
        low, high = policy["cpuinfo_min_freq"], policy["cpuinfo_max_freq"]
        minimum, current = policy["scaling_min_freq"], policy["scaling_max_freq"]
        frequencies = policy["available_frequencies"]
        if (policy["scaling_driver"] != "qcom-cpufreq-hw"
                or not 0 < low <= minimum <= current <= high
                or not frequencies or min(frequencies) < low or max(frequencies) > high):
            raise ValueError(f"unsupported or inconsistent policy: {policy['policy']}")
        if profile == "balanced":
            target = high
        else:
            choices = [freq for freq in frequencies if minimum <= freq and freq * 100 <= high * 70]
            if not choices:
                raise ValueError(f"minimum frequency prevents power-saver: {policy['policy']}")
            # Preserve an existing, stricter administrator or cooling limit.
            target = min(current, max(choices))
        changes.append({"policy": policy["policy"], "previous_max_khz": current,
                        "requested_max_khz": target})
    return changes


def wait_ceiling(path, ceiling):
    # cpufreq QoS changes are applied asynchronously by the kernel workqueue.
    for attempt in range(51):
        effective = int(path.read_text())
        if 0 < effective <= ceiling:
            return
        if attempt < 50:
            time.sleep(.02)
    raise ValueError(f"frequency ceiling not accepted: {path.parent.name}")


def apply(changes, root=CPUFREQ):
    written = []
    try:
        for change in changes:
            if change["previous_max_khz"] == change["requested_max_khz"]:
                continue
            path = root / change["policy"] / "scaling_max_freq"
            written.append(change)
            path.write_text(str(change["requested_max_khz"]))
            wait_ceiling(path, change["requested_max_khz"])
    except BaseException as error:
        failures = []
        for change in reversed(written):
            try:
                (root / change["policy"] / "scaling_max_freq").write_text(str(change["previous_max_khz"]))
            except OSError:
                failures.append(change["policy"])
        if failures:
            raise RuntimeError("rollback failed for: " + ", ".join(failures)) from error
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    for action in ("plan", "set"):
        sub.add_parser(action).add_argument("profile", choices=("balanced", "power-saver"))
    args = parser.parse_args()
    try:
        if args.command == "status":
            report = {"policies": inspect()}
        elif args.command == "plan":
            report = {"profile": args.profile, "changes": plan(inspect(), args.profile)}
        else:
            if os.geteuid() != 0:
                raise PermissionError("setting CPU limits requires root")
            with open("/run/fuxi-cpu-profile.lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                changes = plan(inspect(), args.profile)
                apply(changes)
                report = {"profile": args.profile, "changes": changes, "policies": inspect()}
        report["hardware_ceiling_verified"] = False
        print(json.dumps(report, indent=2, sort_keys=True))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"fuxi-cpu-profile: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
