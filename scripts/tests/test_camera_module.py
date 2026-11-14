"""Synthetic format tests. No vendor tables or fixtures are included."""

import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-camera-module.py"
SPEC = importlib.util.spec_from_file_location("camera_module", SCRIPT)
camera = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(camera)


def pack(*values):
    return struct.pack(f"<{len(values)}I", *values)


class Fixture:
    def __init__(self):
        self.entries = []

    def add(self, name, data):
        self.entries.append((name, data))
        return len(self.entries)

    def register(self, address=0, values=(0x1234, 0xABCD), delay=731):
        slave = self.add("slaveAddr", b"")
        data = self.add("registerData", pack(*values))
        delay_ref = self.add("delayUs", pack(delay))
        return self.add("regSetting", pack(0, slave, address, len(values), data, 2, 2, 4, 1, delay_ref))

    def binary(self):
        values = bytearray()
        symbols = bytearray()
        for index, (name, data) in enumerate(self.entries, 1):
            symbols.extend(struct.pack("<I32s6I", index, name.encode(), 0, 0, 0xFFFFFFFF,
                                       len(values), len(data), index))
            values.extend(data)
        header = bytearray(204)
        magic = b"QTI Chromatix Header\0"
        header[:len(magic)] = magic
        struct.pack_into("<3H", header, 32, 3, 5, 0)
        parser = b"Parameter Parser V3.5.1 (2207122229)"
        header[40:40 + len(parser)] = parser
        struct.pack_into("<II", header, 160, 168, 3)
        start = 204
        for index, section in enumerate((symbols, values, bytes(20))):
            struct.pack_into("<III", header, 168 + index * 12, index, start, len(section))
            start += len(section)
        struct.pack_into("<I", header, 28, start)
        return header + symbols + values + bytes(24)

    def sensor(self):
        # Non-sensor settings deliberately precede the real sensor mode.
        self.register(address=999)
        root = [0] * 173
        root[:3] = (1, 0, 0)
        root[3:5] = (4, self.add("sensorName", b"test"))
        root[11:17] = (32, 2, 2, 22, 8, 0xFF00)
        power = self.add("powerSetting", pack(0, 19200000, 2))
        root[22:26] = (1, power, 1, power)
        vc = self.add("vc", pack(0, 1))
        stream = self.add("streamConfiguration", pack(2, vc, 43, 0, 0, 640, 480, 10, 1, 0, 0, 0, 0, 0, 0))
        regs = self.register()
        mode = [0] * 70
        mode[8:10] = (1, stream)
        mode[12:14] = (1000, 600)
        mode[16:19] = (18000000, 1, 1)
        mode[19:21] = struct.unpack("<II", struct.pack("<d", 30.0))
        mode[23], mode[26], mode[27] = 3, 30, 1
        mode[30:32] = (1, regs)
        modes = self.add("resolutionData", pack(*mode))
        version = self.add("sensorVersion", pack(0))
        resolution = self.add("resolutionInfo", pack(1, version, 1, modes))
        root[67:69] = (1, resolution)
        for index in (104, 106, 112, 114):
            root[index:index + 2] = (1, regs)
        init = self.add("initSettings", pack(0, 1, regs, 0, 0, 0, 0))
        root[124:126] = (1, init)
        self.add("sensorDriverData", pack(*root))
        return self.binary()


class CameraModuleTests(unittest.TestCase):
    def test_mode_roles_and_summary_do_not_export_tables(self):
        report = camera.Module(Fixture().sensor()).summary()
        self.assertFalse(report["integrity_verified"])
        self.assertFalse(report["hardware_verified"])
        self.assertEqual(len(report["modes"]), 1)
        mode = report["modes"][0]
        self.assertEqual(mode["streams"][0]["virtual_channels"], [0, 1])
        self.assertEqual(mode["streams"][0]["width"], 640)
        self.assertEqual(mode["frame_rate_encoded"], 30)
        self.assertEqual(mode["registers"]["entries"], 1)
        self.assertEqual(report["identity_encoded"]["value"], 8)
        self.assertNotIn("registerData", json.dumps(report))
        self.assertNotIn("4660", json.dumps(report))

    def test_preserves_zero_address_multibyte_arrays_operation_and_real_delay(self):
        fixture = Fixture()
        ident = fixture.register()
        record = camera.Module(fixture.binary()).registers(1, ident)[0]
        self.assertEqual(record["address"], 0)
        self.assertEqual(record["data"], [0x1234, 0xABCD])
        self.assertEqual(record["data_width_encoded"], 2)
        self.assertEqual(record["operation_encoded"], 4)
        self.assertEqual(record["delay_encoded"], 731)
        self.assertIsNone(record["slave_override_encoded"])

    def test_unaligned_values_are_valid(self):
        fixture = Fixture()
        fixture.add("padding", b"a")
        ident = fixture.register()
        self.assertEqual(camera.Module(fixture.binary()).registers(1, ident)[0]["delay_encoded"], 731)

    def test_slave_override_is_uint16(self):
        fixture = Fixture()
        ident = fixture.register()
        fixture.entries[0] = ("slaveAddr", struct.pack("<H", 0x30))
        record = list(camera.words(fixture.entries[-1][1]))
        record[0] = 1
        fixture.entries[-1] = ("regSetting", pack(*record))
        self.assertEqual(camera.Module(fixture.binary()).registers(1, ident)[0]["slave_override_encoded"], 0x30)

    def test_bad_headers_and_sections_rejected(self):
        base = Fixture().sensor()
        for offset, fmt, value in ((0, "B", 0), (28, "I", len(base)), (32, "H", 3 + 1),
                                   (40, "B", 0), (160, "I", 0), (164, "I", 500),
                                   (168, "I", 1), (172, "I", 203), (176, "I", 0xFFFFFFFF)):
            with self.subTest(offset=offset):
                corrupt = bytearray(base)
                struct.pack_into("<" + fmt, corrupt, offset, value)
                with self.assertRaises(camera.FormatError):
                    camera.Module(corrupt)

    def test_all_truncated_prefixes_rejected(self):
        fixture = Fixture()
        fixture.register()
        data = fixture.binary()
        for length in range(len(data)):
            with self.assertRaises(camera.FormatError):
                camera.Module(data[:length])

    def test_symbol_bounds_and_aliases_rejected(self):
        fixture = Fixture()
        fixture.register()
        base = fixture.binary()
        for offset, value in ((204, 0), (204 + 48, 0xFFFFFFFF),
                              (204 + 52, 0xFFFFFFFF), (204 + 56, 99),
                              (204 + 56, 2), (264, 1)):
            with self.subTest(offset=offset, value=value):
                data = bytearray(base)
                struct.pack_into("<I", data, offset, value)
                with self.assertRaises(camera.FormatError):
                    camera.Module(data)

    def test_valid_shared_extent_alias(self):
        fixture = Fixture()
        fixture.add("number", pack(9))
        fixture.add("number", pack(9))
        data = fixture.binary()
        struct.pack_into("<I", data, 264 + 48, 0)
        struct.pack_into("<I", data, 264 + 56, 1)
        self.assertEqual(camera.Module(data).value(2, "number", 4), pack(9))

    def test_bad_register_references_flags_counts_and_widths(self):
        for field, value in ((0, 2), (1, 999), (3, 2**32 - 1), (4, 1),
                              (5, 9), (6, 9), (8, 2), (9, 999)):
            with self.subTest(field=field):
                fixture = Fixture()
                ident = fixture.register()
                record = list(camera.words(fixture.entries[-1][1]))
                record[field] = value
                fixture.entries[-1] = ("regSetting", pack(*record))
                with self.assertRaises(camera.FormatError):
                    camera.Module(fixture.binary()).registers(1, ident)

    def test_reference_type_and_size_checked(self):
        fixture = Fixture()
        ident = fixture.register()
        module = camera.Module(fixture.binary())
        for count, ref in ((2, ident), (1, ident - 1), (1, 999)):
            with self.assertRaises(camera.FormatError):
                module.registers(count, ref)

    def test_nan_frame_rate_rejected(self):
        fixture = Fixture()
        fixture.sensor()
        for index, (name, data) in enumerate(fixture.entries):
            if name == "resolutionData":
                data = bytearray(data)
                struct.pack_into("<d", data, 76, float("nan"))
                fixture.entries[index] = (name, data)
        with self.assertRaises(camera.FormatError):
            camera.Module(fixture.binary()).summary()

    def test_cli_invalid_input_has_no_partial_json_or_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.bin"
            path.write_bytes(b"not a module")
            result = subprocess.run([sys.executable, str(SCRIPT), str(path)],
                                    capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
