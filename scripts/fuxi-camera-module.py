#!/usr/bin/env python3
"""Inspect the observed fuxi CamX 3.5 layout; never generate or execute writes."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys


class FormatError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise FormatError(message)


def words(data):
    require(len(data) % 4 == 0, "unaligned word array length")
    return struct.unpack(f"<{len(data) // 4}I", data)


class Module:
    """Partial decoder, restricted to the layout observed in six fuxi modules.

    Symbol IDs are references, not array positions or table roles. Unknown
    fields, register operations and identity byte order remain uninterpreted.
    The final four bytes and section 2 are not authenticated by this decoder.
    """

    def __init__(self, data):
        require(204 <= len(data) <= 32 * 1024 * 1024, "invalid file size")
        require(data.startswith(b"QTI Chromatix Header"), "invalid magic")
        require(struct.unpack_from("<I", data, 28)[0] == len(data) - 4,
                "file length mismatch")
        require(struct.unpack_from("<3H", data, 32) == (3, 5, 0),
                "unsupported binary version")
        require(data[40:104].split(b"\0", 1)[0] ==
                b"Parameter Parser V3.5.1 (2207122229)", "unsupported parser version")
        require(struct.unpack_from("<II", data, 160) == (168, 3),
                "unsupported section directory")
        sections = []
        end = 204
        for index in range(3):
            kind, offset, size = struct.unpack_from("<III", data, 168 + 12 * index)
            require(kind == index and offset == end and size <= len(data) - 4 - offset,
                    "invalid section bounds/order")
            sections.append(data[offset:offset + size])
            end = offset + size
        require(end == len(data) - 4 and len(sections[2]) == 20,
                "unsupported trailer layout")
        symbols, values, _ = sections
        require(len(symbols) > 0 and len(symbols) % 60 == 0, "invalid symbol table")
        self.symbols = {}
        for offset in range(0, len(symbols), 60):
            ident, name, a, b, c, start, size, alias = struct.unpack_from(
                "<I32s6I", symbols, offset)
            require(ident > 0 and ident not in self.symbols, "duplicate/zero symbol ID")
            require(start <= len(values) and size <= len(values) - start,
                    "symbol outside value section")
            try:
                name = name.split(b"\0", 1)[0].decode("ascii")
            except UnicodeDecodeError as error:
                raise FormatError("non-ASCII symbol name") from error
            require(bool(name), "empty symbol name")
            self.symbols[ident] = (name, start, size, alias, (a, b, c))
        for name, start, size, alias, _ in self.symbols.values():
            require(alias in self.symbols, "missing alias target")
            require(self.symbols[alias][1:3] == (start, size), "alias extent mismatch")
        self.values = values
        self.sha256 = hashlib.sha256(data).hexdigest()

    def value(self, ident, name, size=None):
        require(ident in self.symbols, f"missing {name} reference")
        actual, start, length, _, _ = self.symbols[ident]
        require(actual == name, f"expected {name}, found {actual}")
        require(size is None or size == length, f"invalid {name} size")
        return self.values[start:start + length]

    def array(self, count, ident, name, stride):
        data = self.value(ident, name, count * stride)
        return [data[offset:offset + stride] for offset in range(0, len(data), stride)]

    def optional(self, present, ident, name, width=4):
        require(present in (0, 1), f"invalid {name} presence flag")
        data = self.value(ident, name, present * width)
        return int.from_bytes(data, "little") if present else None

    def registers(self, count, ident):
        result = []
        for record in self.array(count, ident, "regSetting", 40):
            slave_present, slave_ref, address, ndata, data_ref, aw, dw, op, dp, dr = words(record)
            require(aw in range(5) and dw in range(5), "unknown register width encoding")
            result.append({
                "address": address,
                "data": list(words(self.value(data_ref, "registerData", ndata * 4))),
                "address_width_encoded": aw,
                "data_width_encoded": dw,
                "operation_encoded": op,
                "slave_override_encoded": self.optional(slave_present, slave_ref, "slaveAddr", 2),
                "delay_encoded": self.optional(dp, dr, "delayUs"),
            })
        return result

    def register_summary(self, count, ident):
        records = self.registers(count, ident)
        return {
            "entries": len(records),
            "data_words": sum(len(record["data"]) for record in records),
            "width_pairs_encoded": sorted({(r["address_width_encoded"], r["data_width_encoded"])
                                           for r in records}),
            "operations_encoded": sorted({r["operation_encoded"] for r in records}),
            "slave_overrides_encoded": sorted({r["slave_override_encoded"] for r in records
                                               if r["slave_override_encoded"] is not None}),
            "nonzero_delays": sum(r["delay_encoded"] not in (None, 0) for r in records),
        }

    def summary(self):
        roots = [ident for ident, value in self.symbols.items() if value[0] == "sensorDriverData"]
        require(len(roots) == 1, "expected one sensor root")
        root = words(self.value(roots[0], "sensorDriverData", 692))
        require(root[:3] == (1, 0, 0), "unsupported sensor root version")
        try:
            name = self.value(root[4], "sensorName", root[3]).rstrip(b"\0").decode("ascii")
        except UnicodeDecodeError as error:
            raise FormatError("non-ASCII sensor name") from error
        modes = []
        for resolution in self.array(root[67], root[68], "resolutionInfo", 16):
            present, version_ref, count, modes_ref = words(resolution)
            version = self.optional(present, version_ref, "sensorVersion")
            for record in self.array(count, modes_ref, "resolutionData", 280):
                mode = words(record)
                fps = struct.unpack_from("<d", record, 76)[0]
                require(math.isfinite(fps) and fps > 0, "invalid mode frame rate")
                streams = []
                for raw in self.array(mode[8], mode[9], "streamConfiguration", 60):
                    stream = words(raw)
                    streams.append({
                        "virtual_channels": list(words(self.value(stream[1], "vc", stream[0] * 4))),
                        "data_type_encoded": stream[2],
                        "x": stream[3], "y": stream[4],
                        "width": stream[5], "height": stream[6],
                        "bit_width": stream[7], "type_encoded": stream[8],
                    })
                modes.append({
                    "sensor_version": version, "streams": streams,
                    "line_length_encoded": mode[12], "frame_length_encoded": mode[13],
                    "output_pixel_clock_encoded": mode[16],
                    "binning_encoded": list(mode[17:19]), "frame_rate_encoded": fps,
                    "lane_count_encoded": mode[23], "settle_time_ns_encoded": mode[26],
                    "is_3_phase_encoded": mode[27],
                    "registers": self.register_summary(mode[30], mode[31]),
                })
        controls = {}
        for role, index in (("stream_on", 104), ("stream_off", 106),
                            ("group_hold_on", 112), ("group_hold_off", 114)):
            controls[role] = self.register_summary(root[index], root[index + 1])
        init = []
        for record in self.array(root[124], root[125], "initSettings", 28):
            setting = words(record)
            init.append({"sensor_version": setting[0],
                         "registers": self.register_summary(setting[1], setting[2])})
        power = {}
        for role, index in (("up", 22), ("down", 24)):
            power[role] = len(self.array(root[index], root[index + 1], "powerSetting", 12))
        return {
            "schema": "fuxi-camx-observed-3.5-v1", "sha256": self.sha256,
            "integrity_verified": False, "hardware_verified": False,
            "sensor_name": name, "symbol_count": len(self.symbols),
            "identity_encoded": {
                "slave_address": root[11], "address_width": root[12],
                "data_width": root[13], "register": root[14],
                "value": root[15], "mask": root[16],
            },
            "power_step_counts": power, "modes": modes, "controls": controls,
            "initialization": init,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path)
    args = parser.parse_args()
    try:
        with args.module.open("rb") as source:
            data = source.read(32 * 1024 * 1024 + 1)
        report = Module(data).summary()
    except (OSError, FormatError) as error:
        print(f"camera module: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
