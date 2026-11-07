#!/usr/bin/env python3
"""Power one fuxi camera at a time and read only its identity register."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time


# Seven-bit CCI addresses from the device's sensor-module data, not a bus scan.
CAMERAS = {
    "cam0": ("IMX800", "ac15000", "1", 0x10, 0x0016, 2, 0x0800, 0xFF00),
    "cam2": ("OV13B10", "ac15000", "0", 0x36, 0x300A, 3, 0x560D42, 0xFFFFFF),
    "cam1": ("OV32C", "ac16000", "1", 0x18, 0x300A, 3, None, None),
    "cam3": ("S5K3K1", "ac16000", "0", 0x2D, 0x0000, 2, 0x30B1, 0xFFFF),
}


def find_bus(controller, master, devices=Path("/sys/bus/i2c/devices")):
    suffix = f"/cci@{controller}/i2c-bus@{master}"
    buses = [p.name.split("-", 1)[1] for p in devices.glob("i2c-*")
             if str((p / "of_node").resolve()).endswith(suffix)]
    if len(buses) != 1:
        raise RuntimeError(f"expected one CCI bus for {suffix}, found {buses}")
    return buses[0]


def identify(camera, devices=Path("/sys/bus/platform/devices")):
    model, controller, master, address, register, count, expected, mask = CAMERAS[camera]
    power = devices / f"camera-power-{camera}" / "power_on"
    bus = find_bus(controller, master)
    if power.read_text().strip() != "0":
        raise RuntimeError(f"{camera} is already powered; refusing to take ownership")
    try:
        power.write_text("1\n")
        time.sleep(0.05)
        result = subprocess.run(
            ["i2ctransfer", "-y", bus, f"w2@0x{address:02x}",
             f"0x{register >> 8:02x}", f"0x{register & 255:02x}", f"r{count}"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        values = [int(value, 16) for value in result.stdout.split()]
        if len(values) != count or any(value < 0 or value > 255 for value in values):
            raise RuntimeError(f"invalid I2C response: {result.stdout!r}")
        identity = int.from_bytes(bytes(values), "big")
        return {
            "camera": camera, "model": model, "bus": int(bus),
            "address": f"0x{address:02x}", "register": f"0x{register:04x}",
            "identity": f"0x{identity:0{count * 2}x}",
            "matched": (identity & mask) == expected if expected is not None else None,
        }
    finally:
        power.write_text("0\n")


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", choices=[*CAMERAS, "all"], default="cam2")
    parser.add_argument("--cycles", type=int, choices=range(1, 11), default=1)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run on the Linux phone as root")
    signal.signal(signal.SIGTERM, interrupted)
    cameras = CAMERAS if args.camera == "all" else [args.camera]
    failed = False
    with open("/run/lock/fuxi-camera-identify.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for cycle in range(args.cycles):
            for camera in cameras:
                try:
                    result = identify(camera)
                    failed |= result["matched"] is False
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    result = {"camera": camera, "error": str(error)}
                    failed = True
                result["cycle"] = cycle + 1
                print(json.dumps(result), flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
