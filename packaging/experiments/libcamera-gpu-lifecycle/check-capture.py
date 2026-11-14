#!/usr/bin/env python3
"""Check a single GPU capture log from cam with the EGL audit preloaded."""

import argparse
import json
from pathlib import Path
import re


def check_capture(text, frames, errors):
    if frames < 1 or not 0 <= errors <= frames:
        raise ValueError("invalid expected frame/error count")
    rows = re.findall(
        r"^\d+\.\d+ \([\d.]+ fps\) cam0-stream0 seq: (\d+) bytesused: (\d+)$",
        text, re.MULTILINE,
    )
    if len(rows) != frames:
        raise ValueError(f"completed {len(rows)}/{frames} frames")
    sequences = [int(row[0]) for row in rows]
    if any(right <= left for left, right in zip(sequences, sequences[1:])):
        raise ValueError("duplicate or out-of-order frame sequence")
    failed = sum(int(row[1]) == 0 for row in rows)
    if failed != errors:
        raise ValueError(f"expected {errors} empty error buffers, got {failed}")
    reports = re.findall(r"^EGL_AUDIT (.+)$", text, re.MULTILINE)
    if len(reports) != 1:
        raise ValueError("missing or ambiguous EGL audit report")
    report = reports[0]
    pattern = (r"attempts=(\d+) created=(\d+) destroyed=(\d+) live=(\d+) "
               r"peak=(\d+) injected=(\d+) bad_destroy=(\d+)")
    match = re.fullmatch(pattern, report)
    if not match:
        raise ValueError("malformed EGL audit report")
    keys = ("attempts", "created", "destroyed", "live", "peak", "injected", "bad_destroy")
    counts = dict(zip(keys, map(int, match.groups())))
    if (counts["attempts"] < frames or
            not frames - errors <= counts["created"] <= counts["attempts"] or
            counts["injected"] > counts["attempts"]):
        raise ValueError("inconsistent EGL import count")
    if counts["created"] != counts["destroyed"] or counts["live"] or counts["bad_destroy"]:
        raise ValueError("EGL images leaked or destruction failed")
    if counts["peak"] != int(counts["created"] > 0):
        raise ValueError("more than one EGL image in flight or inconsistent peak")
    if counts["injected"] < errors or (errors < frames and counts["injected"] != errors):
        raise ValueError("unexpected fault injection count")
    return {"frames": frames, "error_buffers": failed, **counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--errors", type=int, default=0)
    args = parser.parse_args()
    try:
        result = check_capture(args.log.read_text(), args.frames, args.errors)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
