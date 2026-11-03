#!/usr/bin/env python3
"""Run the SuperTerm test suite without needing PYTHONPATH=src.

Usage (from the repo root or from an installed copy):
    python3 tests/run_tests.py            # unit + integration
    python3 tests/run_tests.py unit       # unit only
    python3 tests/run_tests.py --verbose
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

args = [a for a in sys.argv[1:] if a not in ("unit", "integration")]
which = [a for a in sys.argv[1:] if a in ("unit", "integration")]

# Preflight: the suite creates SQLite DBs and log dirs in temp dirs. In a read-only
# sandbox TMPDIR is unusable; say so explicitly instead of reporting 40+ fake failures.
import tempfile
try:
    _probe = tempfile.mkdtemp(prefix="superterm-probe-")
    os.rmdir(_probe)
except Exception as _e:  # noqa: BLE001
    print(f"ENVIRONMENT: no writable temp dir ({_e}).")
    print("This suite needs a writable TMPDIR; it cannot run in a read-only sandbox.")
    print("Set TMPDIR to a writable path, then re-run:  TMPDIR=/tmp python3 tests/run_tests.py")
    raise SystemExit(3)

loader = unittest.TestLoader()
suite = unittest.TestSuite()
dirs = which or ["unit", "integration"]
for name in dirs:
    d = os.path.join(HERE, name)
    if os.path.isdir(d):
        suite.addTests(loader.discover(start_dir=d))

verbosity = 2 if "-v" in sys.argv or "--verbose" in sys.argv else 1
result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
