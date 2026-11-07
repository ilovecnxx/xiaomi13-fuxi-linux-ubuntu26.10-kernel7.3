"""Failed kernel builds must never proceed to package collection."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "fuxi-build-deb.sh"


class DebBuildFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kernel = self.root / "kernel"
        for path in ("scripts", "bin", "kernel/arch/arm64/configs", "kernel/scripts"):
            (self.root / path).mkdir(parents=True)
        shutil.copy2(SOURCE, self.root / "scripts/fuxi-build-deb.sh")
        (self.kernel / "arch/arm64/configs/fuxi_defconfig").touch()
        self.executable("kernel/scripts/config", "#!/bin/sh\nexit 0\n")
        self.executable("bin/git", '''#!/bin/sh
if [ "${FAIL_STAGE:-}" = source ]; then exit 9; fi
printf '1794000000\n'
''')
        self.executable("bin/make", '''#!/bin/bash
set -eu
printf '%s\n' "$*" >> "$TRACE"
case " $* " in
  *" fuxi_defconfig "*)
    mkdir -p "$FUXI_KOUT"
    printf '%s\n' 'CONFIG_INITRAMFS_SOURCE=""' 'CONFIG_CMDLINE=""' \
      'CONFIG_BLK_DEV_INITRD=y' 'CONFIG_RD_ZSTD=y' 'CONFIG_EXT4_FS=y' > "$FUXI_KOUT/.config" ;;
  *" olddefconfig "*) [ "${FAIL_STAGE:-}" != config ] || exit 7 ;;
  *" kernelversion "*) printf '7.4.0-rc1\n' ;;
  *" kernelrelease "*) printf '7.4.0-rc1-fuxi-test\n' ;;
  *" Image.gz "*) [ "${FAIL_STAGE:-}" != kernel ] || { echo 'link failed'; exit 8; } ;;
  *" run-command "*) echo 'package failed'; exit 6 ;;
esac
''')
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("SOURCE_DATE_EPOCH", "FAIL_STAGE", "FUXI_EXTRA_CFG")}
        self.env.update(PATH=str(self.root / "bin") + os.pathsep + os.environ["PATH"],
                        FUXI_KTREE=str(self.kernel),
                        FUXI_KOUT=str(self.root / "out"),
                        TRACE=str(self.root / "trace"))

    def executable(self, name, text):
        path = self.root / name
        path.write_text(text)
        path.chmod(0o755)

    def run_failure(self, stage):
        result = subprocess.run(
            ["bash", str(self.root / "scripts/fuxi-build-deb.sh"), "test"],
            env={**self.env, "FAIL_STAGE": stage}, capture_output=True,
            text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "work/kbuild/test").exists())
        trace = self.root / "trace"
        return result, trace.read_text() if trace.exists() else ""

    def test_source_failure_stops_before_make(self):
        result, trace = self.run_failure("source")
        self.assertEqual(trace, "")

    def test_config_failure_stops_before_kernel(self):
        result, trace = self.run_failure("config")
        self.assertNotIn("Image.gz", trace)

    def test_link_failure_stops_before_packaging(self):
        result, trace = self.run_failure("kernel")
        self.assertNotIn("run-command", trace)
        self.assertIn("link failed", result.stderr)

    def test_package_failure_does_not_collect_artifacts(self):
        result, trace = self.run_failure("package")
        self.assertIn("run-command", trace)
        self.assertIn("package failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
