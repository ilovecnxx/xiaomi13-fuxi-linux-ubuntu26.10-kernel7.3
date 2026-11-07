import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "fuxi_charger_log", Path(__file__).resolve().parents[1] / "fuxi-charger-log.py")
log = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(log)


class ChargerLogTests(unittest.TestCase):
    def test_request_matches_vendor_wire_format(self):
        self.assertEqual(log.log_request(0x18), bytes.fromhex(
            "0a800000010000001800000000200000"))

    def test_response_is_bounded_by_first_nul(self):
        payload = b"PD: ready\n\0stale".ljust(log.LOG_SIZE, b"\0")
        data = struct.pack("<III", log.OWNER, 1, 0x18) + payload
        self.assertEqual(log.decode_response(data, 0x18), b"PD: ready\n")

    def test_wrong_channel_or_opcode_is_rejected(self):
        for header in ((32779, 1, 0x18), (log.OWNER, 2, 0x18),
                       (log.OWNER, 1, 0x23)):
            with self.subTest(header=header), self.assertRaises(RuntimeError):
                log.decode_response(struct.pack("<III", *header) +
                                    bytes(log.LOG_SIZE), 0x18)

    def test_truncated_or_oversized_response_is_rejected(self):
        for length in (0, 12, 8203, 8205):
            with self.subTest(length=length), self.assertRaises(RuntimeError):
                log.decode_response(bytes(length), 0x18)

    def test_timeout_does_not_retry_or_change_log_configuration(self):
        with mock.patch.object(log.os, "write", return_value=16) as write, \
                mock.patch.object(log.select, "select", return_value=([], [], [])), \
                self.assertRaises(TimeoutError):
            log.read_chunk(7, 0x18, 1)
        write.assert_called_once_with(7, log.log_request(0x18))

    def test_endpoint_binding_is_reused_and_reader_failure_closes_fd(self):
        for already_bound in (False, True):
            with self.subTest(already_bound=already_bound), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                channel = root / "sys/bus/rpmsg/devices" / f"edge.{log.CHANNEL}.-1.-1"
                channel.mkdir(parents=True)
                (channel / "name").write_text(log.CHANNEL)
                (channel / "driver_override").write_text("(null)\n")
                driver = root / "sys/bus/rpmsg/drivers/rpmsg_chrdev"
                driver.mkdir(parents=True)
                (driver / "bind").touch()
                if already_bound:
                    (channel / "driver").symlink_to(driver, target_is_directory=True)
                endpoint = channel / "rpmsg/rpmsg0"
                endpoint.mkdir(parents=True)
                devices = root / "sys/class/rpmsg"
                devices.mkdir(parents=True)
                (devices / "rpmsg0").symlink_to(endpoint, target_is_directory=True)

                def mapped_path(path):
                    return root / path.lstrip("/")

                with mock.patch.object(log, "Path", side_effect=mapped_path), \
                        mock.patch.object(log.os, "open", return_value=7), \
                        mock.patch.object(log.os, "close") as close:
                    with self.assertRaisesRegex(RuntimeError, "read failed"):
                        with log.log_endpoint() as fd:
                            self.assertEqual(fd, 7)
                            raise RuntimeError("read failed")
                close.assert_called_once_with(7)
                self.assertFalse((driver / "unbind").exists())
                self.assertEqual((driver / "bind").read_text(),
                                 "" if already_bound else channel.name + "\n")
                self.assertEqual((channel / "driver_override").read_text(),
                                 "(null)\n" if already_bound else "\n")


if __name__ == "__main__":
    unittest.main()
