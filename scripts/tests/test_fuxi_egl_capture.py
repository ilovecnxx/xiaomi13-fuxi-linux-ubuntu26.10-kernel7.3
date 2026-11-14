import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "packaging/experiments/libcamera-gpu-lifecycle/check-capture.py"
SPEC = importlib.util.spec_from_file_location("egl_capture", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class EglCaptureTests(unittest.TestCase):
    def log(self, sizes=(4096, 4096), **updates):
        counts = dict(attempts=2, created=2, destroyed=2, live=0,
                      peak=1, injected=0, bad_destroy=0)
        counts.update(updates)
        rows = [f"1.{i:06d} (30.00 fps) cam0-stream0 seq: {i:06d} bytesused: {size}"
                for i, size in enumerate(sizes)]
        return "\n".join(rows + ["EGL_AUDIT " + " ".join(f"{k}={v}" for k, v in counts.items())])

    def test_balanced_and_injected_failures(self):
        for log, errors in ((self.log(), 0),
                            (self.log((4096, 0), injected=1), 1),
                            (self.log((0, 0), created=0, destroyed=0, peak=0, injected=2), 2)):
            with self.subTest(errors=errors):
                self.assertEqual(MODULE.check_capture(log, 2, errors)["error_buffers"], errors)

    def test_leak_and_double_destroy_fail(self):
        for changes in (dict(destroyed=0, live=2, peak=2), dict(bad_destroy=1), dict(peak=2)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                MODULE.check_capture(self.log(**changes), 2, 0)

    def test_incomplete_or_reordered_capture_fails(self):
        for log in (self.log((4096,)), self.log().replace("seq: 000001", "seq: 000000")):
            with self.subTest(log=log), self.assertRaises(ValueError):
                MODULE.check_capture(log, 2, 0)

    def test_unexpected_error_or_injection_fails(self):
        for log in (self.log((4096, 0)), self.log(injected=1), self.log(attempts=0),
                    self.log(created=0, destroyed=0, peak=0)):
            with self.subTest(log=log), self.assertRaises(ValueError):
                MODULE.check_capture(log, 2, 0)

    def test_missing_or_duplicate_report_fails(self):
        log = self.log()
        for text in (log.split("EGL_AUDIT")[0], log + "\n" + log.splitlines()[-1]):
            with self.subTest(text=text), self.assertRaises(ValueError):
                MODULE.check_capture(text, 2, 0)


if __name__ == "__main__":
    unittest.main()
