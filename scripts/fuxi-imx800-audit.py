#!/usr/bin/env python3
"""Audit the two pinned fuxi IMX800 mode-1 sources without generating writes."""

import argparse
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


SPEC = importlib.util.spec_from_file_location(
    "fuxi_camera_module", Path(__file__).with_name("fuxi-camera-module.py"))
camera = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(camera)

PROFILES = {
    "semco": "33b6c7ebdbd15eb9db937162bf1df4988e1dd5b4f504bbab6fb520c66d355a02",
    "sunny": "c4c32782450beb70800d7f0cac7cedea2a029c6e6e0b57180c4a2ce88d02c815",
}
CONTROL_ROLES = {"stream_on": 104, "stream_off": 106,
                 "group_hold_on": 112, "group_hold_off": 114}


def extract(module):
    summary = module.summary()
    ident = next(ident for ident, entry in module.symbols.items()
                 if entry[0] == "sensorDriverData")
    root = camera.words(module.value(ident, "sensorDriverData", 692))
    camera.require(root[67] == 1 and root[124] == 1, "expected one resolution/init group")
    resolution = camera.words(module.array(root[67], root[68], "resolutionInfo", 16)[0])
    modes = module.array(resolution[2], resolution[3], "resolutionData", 280)
    camera.require(len(modes) > 1, "mode 1 is missing")
    mode = camera.words(modes[1])
    init = camera.words(module.array(root[124], root[125], "initSettings", 28)[0])
    tables = {"init": module.registers(init[1], init[2]),
              "mode1": module.registers(mode[30], mode[31])}
    for role, index in CONTROL_ROLES.items():
        tables[role] = module.registers(root[index], root[index + 1])
    power = {}
    for role, index in (("up", 22), ("down", 24)):
        power[role] = [list(camera.words(record)) for record in
                       module.array(root[index], root[index + 1], "powerSetting", 12)]
    return {"tables": tables, "power_encoded": power,
            "identity_encoded": summary["identity_encoded"], "mode": summary["modes"][1]}


def byte_map(records):
    result = {}
    for record in records:
        camera.require(record["address_width_encoded"] == 2 and
                       record["data_width_encoded"] == 1 and
                       record["operation_encoded"] == 0 and
                       record["slave_override_encoded"] is None and
                       record["delay_encoded"] in (None, 0) and
                       len(record["data"]) == 1 and
                       0 <= record["data"][0] <= 255 and
                       0 <= record["address"] <= 65535,
                       "analysis requires scalar byte writes with no override/delay")
        result[record["address"]] = record["data"][0]
    return result


def compare_profiles(left, right):
    # Compare ordered records before reducing writes to final register values.
    for key in ("tables", "power_encoded", "identity_encoded", "mode"):
        camera.require(left[key] == right[key], f"module variants differ in {key}")
    return left


def model(profile):
    tables = profile["tables"]
    for records in tables.values():
        byte_map(records)
    regs = byte_map(tables["init"] + tables["mode1"])

    def word(address):
        return regs[address] * 256 + regs[address + 1]

    try:
        camera.require(regs[0x0111] == 3 and regs[0x0112] == regs[0x0113] == 10 and
                       regs[0x0114] == 2, "expected three-trio C-PHY RAW10")
        camera.require(word(0x0136) == 0x1333, "expected encoded 19.2 MHz input")
        camera.require(all(regs[address] for address in (0x0301, 0x0303, 0x0305,
                                                        0x030B, 0x030D)),
                       "zero PLL divider")
        input_clock = 19200000
        op_sys = Fraction(input_clock * word(0x030E), regs[0x030D] * regs[0x030B])
        link_freq = op_sys / 2
        data_rate = link_freq * 32 / 7
        vt_clock = Fraction(input_clock * word(0x0306),
                            regs[0x0305] * regs[0x0303] * regs[0x0301])
        # The parallel factor is a candidate model, not a measured sensor capability.
        timing_rate = vt_clock * 16
        line_length, frame_length = word(0x0342), word(0x0340)
        camera.require(line_length > 0 and frame_length > 0, "zero frame timing")
        width, height = word(0x034C), word(0x034E)
    except KeyError as error:
        raise camera.FormatError(f"missing required register 0x{error.args[0]:04x}") from error
    mode = profile["mode"]
    camera.require((width, height, line_length, frame_length) ==
                   (4096, 3072, mode["line_length_encoded"], mode["frame_length_encoded"]),
                   "mode metadata and register geometry/timing disagree")
    camera.require(abs(float(timing_rate / (line_length * frame_length)) -
                       mode["frame_rate_encoded"]) < 0.000001,
                   "candidate VT model and encoded frame rate disagree")
    selected = sorted({0x0136, 0x0137, 0x0111, 0x0112, 0x0113, 0x0114,
                       0x0301, 0x0303, 0x0305, 0x0306, 0x0307, 0x030B, 0x030D,
                       0x030E, 0x030F, *range(0x0340, 0x0350)})
    return {
        "input_clock_hz": input_clock,
        "selected_register_bytes": {f"0x{address:04x}": regs[address] for address in selected},
        "link_frequency_hz_candidate": float(link_freq),
        "per_trio_bits_per_second_ceil_candidate": -(-data_rate.numerator // data_rate.denominator),
        "pixel_rate_csi_hz_candidate": float(data_rate * 3 / 10),
        "vt_clock_hz_candidate": float(vt_clock),
        "vt_parallel_factor_assumed": 16,
        "pixel_array_timing_rate_hz_candidate": float(timing_rate),
        "frame_rate_hz_candidate": float(timing_rate / (line_length * frame_length)),
        "streams_encoded": mode["streams"],
        "settle_field_encoded": mode["settle_time_ns_encoded"],
        "stream_on_register_bytes": {f"0x{key:04x}": value for key, value in
                                      byte_map(tables["stream_on"]).items()},
    }


def audit(sources):
    profiles = {}
    for name, path in sources.items():
        with path.open("rb") as source:
            data = source.read(32 * 1024 * 1024 + 1)
        camera.require(hashlib.sha256(data).hexdigest() == PROFILES[name],
                       f"{name}: source SHA256 does not match the pinned module")
        profiles[name] = extract(camera.Module(data))
    common = compare_profiles(profiles["semco"], profiles["sunny"])
    table_report = {}
    for role, records in common["tables"].items():
        encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("ascii")
        table_report[role] = {"records": len(records),
                              "ordered_records_sha256": hashlib.sha256(encoded).hexdigest()}
    return {"schema": "fuxi-imx800-mode1-audit-v1", "source_sha256": PROFILES,
            "sources_match_pinned_hashes": True, "integrity_verified": False,
            "hardware_verified": False, "variants_equal_for_audited_roles": True,
            "tables": table_report, "power_encoded": common["power_encoded"],
            "identity_encoded": common["identity_encoded"], "candidate_model": model(common),
            "unverified": ["sensor PLL model and VT parallel factor on hardware",
                           "actual module assembly and trio wiring/order",
                           "Bayer order after 0x0101=3 orientation",
                           "auxiliary CSI data filtering and hardware settle calibration"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semco", type=Path, required=True)
    parser.add_argument("--sunny", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit({"semco": args.semco, "sunny": args.sunny})
    except (OSError, camera.FormatError) as error:
        print(f"IMX800 audit: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
