"""Failed kernel builds must never proceed to package collection."""
import os
import json
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
        shutil.copy2(SOURCE.with_name("fuxi-source-state.py"), self.root / "scripts")
        (self.kernel / "arch/arm64/configs/fuxi_defconfig").touch()
        (self.kernel / "manifest.json").write_text("{}\n")
        self.executable("kernel/scripts/config", "#!/bin/sh\nexit 0\n")
        self.real_git = shutil.which("git")
        self.executable("bin/git", f'''#!/bin/sh
if [ "${{FAIL_STAGE:-}}" = source ]; then exit 9; fi
exec {self.real_git} "$@"
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
  *" Image.gz "*)
    [ "${FAIL_STAGE:-}" != kernel ] || { echo 'link failed'; exit 8; }
    if [ "${FAIL_STAGE:-}" = mutation ]; then
      printf 'changed\n' >> "$FUXI_KTREE/arch/arm64/configs/fuxi_defconfig"
    fi ;;
  *" run-command "*)
    if [ "${FAIL_STAGE:-}" = package ]; then echo 'package failed'; exit 6; fi
    package="$FUXI_KOUT/debian/linux-image-7.4.0-rc1-fuxi-test"
    mkdir -p "$package/DEBIAN" "$package/lib/modules/7.4.0-rc1-fuxi-test" \
      "$package/usr/lib/linux-image-7.4.0-rc1-fuxi-test/qcom"
    touch "$package/usr/lib/linux-image-7.4.0-rc1-fuxi-test/qcom/sm8550-xiaomi-fuxi.dtb" ;;
esac
''')
        self.executable("bin/aarch64-linux-gnu-gcc", "#!/bin/sh\necho fixture-compiler\n")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("SOURCE_DATE_EPOCH", "FAIL_STAGE", "FUXI_EXTRA_CFG")}
        self.env.update(PATH=str(self.root / "bin") + os.pathsep + os.environ["PATH"],
                        FUXI_KTREE=str(self.kernel),
                        FUXI_KOUT=str(self.root / "out"),
                        TRACE=str(self.root / "trace"))
        subprocess.run([self.real_git, "init", "-q", str(self.kernel)], check=True)
        subprocess.run([self.real_git, "-C", str(self.kernel), "add", "."], check=True)
        subprocess.run([self.real_git, "-C", str(self.kernel), "-c", "user.name=Test",
                        "-c", "user.email=test@localhost", "commit", "-qm", "fixture"],
                       check=True)

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

    def test_source_mutation_refuses_package_collection(self):
        result, trace = self.run_failure("mutation")
        self.assertIn("source changed during build", result.stderr)

    def test_success_saves_new_source_and_verifiable_patch(self):
        (self.kernel / "new-driver.c").write_text("int driver;\n")
        result = subprocess.run(
            ["bash", str(self.root / "scripts/fuxi-build-deb.sh"), "test"],
            env=self.env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.root / "work/kbuild/test"
        state = json.loads((output / "build.json").read_text())
        self.assertTrue(state["source_dirty"])
        self.assertNotEqual(state["source_tree"], state["source_base_tree"])
        self.assertIn("new-driver.c", (output / "source.patch").read_text())
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=output,
                       check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
