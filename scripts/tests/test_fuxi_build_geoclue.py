"""GeoClue packaging preserves distro policy and rejects invalid build inputs."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "build_geoclue", Path(__file__).resolve().parents[1] / "fuxi-build-geoclue.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class GeoCluePackageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "sources/geoclue"
        self.source.mkdir(parents=True)
        (self.source / ".fuxi-source.json").write_text('{"fixture": true}\n')
        (self.source / "COPYING").write_text("license fixture\n")
        self.binary = self.root / "geoclue"
        self.binary.write_bytes(b"synthetic package fixture\n")
        self.environment = self.root / "environment.lock"
        self.environment.write_text("libc6:arm64=2.44\n")
        self.args = argparse.Namespace(version="0.20261118.2101",
                                       sources=self.source.parent,
                                       output=self.root / "output",
                                       environment=self.environment)

    def test_deb_payload_retains_system_policy_and_records(self):
        staging = self.root / "staging"
        builder.stage_package(staging, self.source, self.binary, self.environment,
                              self.args.version, "libc6 (>= 2.44)", 1794960000,
                              builder.OPTIONS)
        deb = self.root / "fixture.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(staging), str(deb)],
                       check=True, stdout=subprocess.DEVNULL)
        extracted = self.root / "extracted"
        subprocess.run(["dpkg-deb", "-R", str(deb), str(extracted)], check=True)
        self.assertFalse((extracted / "etc").exists())
        self.assertFalse((extracted / "usr/libexec/geoclue").exists())
        self.assertFalse((extracted / "usr/lib/systemd/system/geoclue.service").exists())
        self.assertFalse((extracted / "usr/share/dbus-1").exists())
        dropin = extracted / "usr/lib/systemd/system/geoclue.service.d/50-fuxi.conf"
        directives = [line for line in dropin.read_text().splitlines()
                      if line and not line.startswith(("#", "["))]
        self.assertEqual(directives, [
            "BindReadOnlyPaths=/usr/libexec/fuxi/geoclue:/usr/libexec/geoclue"])
        installed = extracted / "usr/libexec/fuxi/geoclue"
        self.assertEqual(installed.read_bytes(), self.binary.read_bytes())
        self.assertEqual(installed.stat().st_mode & 0o777, 0o755)
        records = extracted / "usr/share/fuxi-geoclue"
        record = json.loads((records / "build.json").read_text())
        self.assertEqual(record["binary_sha256"], builder.sources.sha256(installed))
        self.assertEqual(record["environment_sha256"], builder.sources.sha256(self.environment))
        self.assertEqual((records / "source.json").read_bytes(),
                         (self.source / ".fuxi-source.json").read_bytes())
        subprocess.run(["md5sum", "--quiet", "-c", "DEBIAN/md5sums"],
                       cwd=extracted, check=True)
        control = (extracted / "DEBIAN/control").read_text()
        self.assertIn("geoclue-2.0 (= 2.7.2-2ubuntu3), systemd, libc6 (>= 2.44)", control)
        for name in ("postinst", "postrm"):
            script = extracted / "DEBIAN" / name
            subprocess.run(["sh", "-n", str(script)], check=True)
            commands = [line.strip() for line in script.read_text().splitlines()
                        if line.strip().startswith("systemctl ")]
            self.assertEqual(commands, ["systemctl daemon-reload"])

    def test_foreign_architecture_rejected_before_build(self):
        with patch.object(builder, "output", return_value="amd64"), \
                patch.object(builder, "run") as run:
            with self.assertRaisesRegex(ValueError, "native arm64"):
                builder.build(self.args)
            run.assert_not_called()

    def prepare_build(self, *, changed_inventory=False, test_failure=False):
        calls = []
        inventories = iter(["libc6:arm64=2.44", "changed" if changed_inventory else "libc6:arm64=2.44"])

        def output(*args, **kwargs):
            if args[0] == "dpkg":
                return "arm64"
            if args[0] == "dpkg-query":
                return next(inventories)
            if args[0] == "dpkg-buildflags":
                return "-O2" if args[2] == "CFLAGS" else ""
            raise AssertionError(args)

        def run(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("meson", "test") and test_failure:
                raise subprocess.CalledProcessError(1, args)

        for context in (patch.object(builder, "ROOT", self.root),
                        patch.object(builder, "output", side_effect=output),
                        patch.object(builder, "run", side_effect=run),
                        patch.object(builder.sources, "check"),
                        patch.dict(os.environ, SOURCE_DATE_EPOCH="1794960000")):
            context.start()
            self.addCleanup(context.stop)
        return calls

    def test_stale_environment_rejected_before_compilation(self):
        calls = self.prepare_build()
        self.environment.write_text("obsolete\n")
        with self.assertRaisesRegex(ValueError, "differs"):
            builder.build(self.args)
        self.assertFalse(any(call[0] == "meson" for call in calls))

    def test_failed_tests_do_not_publish_package(self):
        self.prepare_build(test_failure=True)
        with self.assertRaises(subprocess.CalledProcessError):
            builder.build(self.args)
        self.assertEqual(list(self.args.output.glob("*.deb")), [])

    def test_environment_change_during_compilation_rejects_package(self):
        self.prepare_build(changed_inventory=True)
        with self.assertRaisesRegex(ValueError, "changed while compiling"):
            builder.build(self.args)
        self.assertEqual(list(self.args.output.glob("*.deb")), [])

    def test_published_version_is_not_overwritten(self):
        calls = self.prepare_build()
        self.args.output.mkdir()
        artifact = self.args.output / f"fuxi-geoclue_{self.args.version}_arm64.deb"
        artifact.write_bytes(b"existing package")
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            builder.build(self.args)
        self.assertEqual(artifact.read_bytes(), b"existing package")
        self.assertFalse(any(call[0] == "meson" for call in calls))


if __name__ == "__main__":
    unittest.main()
