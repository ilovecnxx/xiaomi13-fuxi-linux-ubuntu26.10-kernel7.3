#!/usr/bin/env python3
"""Build the local IMX800 mode-1 payload from both pinned module sources."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys


SPEC = importlib.util.spec_from_file_location(
    "imx800_audit", Path(__file__).with_name("fuxi-imx800-audit.py"))
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

PAYLOAD_SHA256 = "3838fa1cb36f845a9c07ade9f88b59fc6d7285533a8edb75282e0d086a94b284"


def encode(profile):
    audit.model(profile)
    tables = profile["tables"]
    counts = [len(tables[role]) for role in ("init", "mode1")]
    audit.camera.require(counts == [167, 135], "unexpected mode table counts")
    return struct.pack("<8s4H", b"FXIMX800", 1, *counts, 0) + b"".join(
        struct.pack(">HB", record["address"], record["data"][0])
        for role in ("init", "mode1") for record in tables[role])


def build(sources):
    audit.audit(sources)
    profiles = {}
    # Recheck the exact bytes consumed here rather than trusting an earlier read.
    for name, path in sources.items():
        with path.open("rb") as source:
            data = source.read(32 * 1024 * 1024 + 1)
        audit.camera.require(hashlib.sha256(data).hexdigest() == audit.PROFILES[name],
                             f"{name}: source changed during generation")
        profiles[name] = audit.extract(audit.camera.Module(data))
    payload = encode(audit.compare_profiles(profiles["semco"], profiles["sunny"]))
    audit.camera.require(hashlib.sha256(payload).hexdigest() == PAYLOAD_SHA256,
                         "generated payload differs from pinned mode")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semco", type=Path, required=True)
    parser.add_argument("--sunny", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = build({"semco": args.semco, "sunny": args.sunny})
        # Do not overwrite an existing artifact, including a symlink.
        with args.output.open("xb") as output:
            output.write(payload)
    except (OSError, audit.camera.FormatError) as error:
        print(f"IMX800 mode: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(args.output), "bytes": len(payload),
                      "sha256": PAYLOAD_SHA256, "hardware_verified": False}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
