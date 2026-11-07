#!/usr/bin/env python3
"""Read bounded ADSP charger log snapshots on the Linux phone."""

import argparse
import contextlib
import fcntl
import os
from pathlib import Path
import select
import struct
import sys


CHANNEL = "PMIC_LOGS_ADSP_APPS"
OWNER = 32778
LOG_SIZE = 8192
HEADER = struct.Struct("<III")
OPCODES = {"runtime": 0x18, "init": 0x23}


def log_request(opcode):
    return struct.pack("<IIII", OWNER, 1, opcode, LOG_SIZE)


def decode_response(data, opcode):
    if len(data) != HEADER.size + LOG_SIZE:
        raise RuntimeError(f"invalid charger log response length: {len(data)}")
    if HEADER.unpack_from(data) != (OWNER, 1, opcode):
        raise RuntimeError("charger log response does not match the request")
    return data[HEADER.size:].split(b"\0", 1)[0]


@contextlib.contextmanager
def log_endpoint():
    devices = Path("/sys/bus/rpmsg/devices")
    matches = [p for p in devices.glob(f"*{CHANNEL}.*")
               if (p / "name").read_text().strip() == CHANNEL]
    if len(matches) != 1:
        raise RuntimeError(f"expected one charger log channel, found {len(matches)}")
    channel = matches[0]
    driver = Path("/sys/bus/rpmsg/drivers/rpmsg_chrdev")
    if (channel / "driver").exists():
        if (channel / "driver").resolve() != driver:
            raise RuntimeError("charger log channel already has a different driver")
    else:
        override = channel / "driver_override"
        if override.read_text().strip() not in ("", "(null)"):
            raise RuntimeError("charger log channel already has a driver override")
        try:
            override.write_text("rpmsg_chrdev\n")
            (driver / "bind").write_text(channel.name + "\n")
        finally:
            override.write_text("\n")
    endpoints = [p for p in Path("/sys/class/rpmsg").glob("rpmsg*")
                 if p.resolve().is_relative_to(channel.resolve())]
    if len(endpoints) != 1:
        raise RuntimeError("charger log channel did not create one endpoint")
    fd = os.open("/dev/" + endpoints[0].name, os.O_RDWR | os.O_NONBLOCK)
    try:
        yield fd
    finally:
        os.close(fd)
        # Unbinding sends GLINK CLOSE and can remove the channel until reboot.
        # Keep the dedicated endpoint bound; no requests run in the background.


def read_chunk(fd, opcode, timeout):
    request = log_request(opcode)
    if os.write(fd, request) != len(request):
        raise RuntimeError("incomplete charger log request")
    readable, _, _ = select.select([fd], [], [], timeout)
    if not readable:
        raise TimeoutError(f"charger log request {opcode:#x} timed out")
    return decode_response(os.read(fd, HEADER.size + LOG_SIZE + 1), opcode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=OPCODES, default="runtime")
    parser.add_argument("--chunks", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=3)
    args = parser.parse_args()
    if not 1 <= args.chunks <= 128 or not 0 < args.timeout <= 10:
        parser.error("chunks must be 1..128 and timeout must be >0 and <=10 seconds")
    if os.geteuid() != 0:
        parser.error("run as root on the Linux phone")
    # Serialize readers so a snapshot cannot take over another reader's endpoint.
    with open("/run/fuxi-charger-log.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with log_endpoint() as fd:
            for _ in range(args.chunks):
                chunk = read_chunk(fd, OPCODES[args.kind], args.timeout)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                if not chunk.endswith(b"\n"):
                    sys.stdout.buffer.write(b"\n")
                sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"fuxi-charger-log: {error}", file=sys.stderr)
        sys.exit(1)
