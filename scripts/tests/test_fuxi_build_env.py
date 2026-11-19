"""Check the fixed container runner's network policy without starting a container."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/fuxi-build-env.sh"


class BuildEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.capture = self.directory / "engine-args.json"
        self.engine = self.directory / "fake-engine"
        self.engine.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "Path(os.environ['CAPTURE_ARGS']).write_text(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        self.engine.chmod(0o755)

    def run_runner(self, network=None):
        environment = os.environ.copy()
        environment["FUXI_CONTAINER_ENGINE"] = str(self.engine)
        environment["CAPTURE_ARGS"] = str(self.capture)
        environment.pop("FUXI_BUILD_NETWORK", None)
        if network is not None:
            environment["FUXI_BUILD_NETWORK"] = network
        return subprocess.run(
            [str(RUNNER), "run", "true"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_default_network_is_disabled(self):
        result = self.run_runner()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--network=none", json.loads(self.capture.read_text(encoding="utf-8")))

    def test_host_network_requires_explicit_opt_in(self):
        result = self.run_runner("host")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--network=host", json.loads(self.capture.read_text(encoding="utf-8")))

    def test_invalid_network_is_rejected_before_engine_invocation(self):
        result = self.run_runner("bridge")

        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.capture.exists())


if __name__ == "__main__":
    unittest.main()
