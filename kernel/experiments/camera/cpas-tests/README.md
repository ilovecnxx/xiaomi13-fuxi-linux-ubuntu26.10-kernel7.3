# CPAS Diagnostics and CSID Power Cleanup

This isolated candidate extends `cpas-icp`. Patch 1 exercises the existing
CPAS snapshot with KUnit. Patch 2 releases the CSID parent reference when
power-on fails after a successful parent acquisition, and adds regression
tests. Neither patch implements a Q-channel stop handshake or resolves MM0.
The standard kernel recipe and phone installation are unchanged.

## Coverage

The CPAS suite calls the actual `camss_cpas_snapshot()` with RAM-backed
registers and a registered common-clock provider. Fourteen cases cover an
absent register mapping, an absent optional clock, unknown versions, control
values other than 1, prepare and enable failures, all four diagnostic phase
labels, a real 10 ms polling timeout, and two concurrent VFE callers.
Clock callback counts, final prepared/enabled states, mutex release, and the
entire register window are checked. Only the fake clock provider changes the
status register. Recovery cases take another snapshot after the failure.

The concurrency case executes eight snapshots per caller, retaining the
production timeout. It checks serialization of the diagnostic windows, not
the lifetime of all CPAS consumers. Testing each phase label does not execute
the real VFE reset, halt or power-off call sites.

The CSID suite calls the actual `csid_set_power()` with a counted parent and
the real runtime PM core. Three cases check failed parent acquisition,
repeated power-on with PM disabled, and a failing runtime-resume callback.
They verify the original errors and balanced parent and runtime PM counts.
Regulator, clock-rate, clock-enable and reset failures use the same cleanup
labels in the fix, but are not individually fault-injected in this suite.
Restoring the original immediate return after runtime PM acquisition failure
makes two CSID tests fail on missing parent puts. The fixed tree passes all
17 cases under QEMU/KASAN, plus the ARM64 CAMSS W=1 build. Strict host CI
also passes the repository script checks without skips.

## Reproduce

Verify the parent manifest and patch SHA256 values in `manifest.json`.
Reconstruct `cpas-icp` and verify its tree before applying these patches in a
new worktree. Local commit IDs are provenance; replayed tree IDs must match.
The exported commit descriptions are line-wrapped for checkpatch. Both
patches have zero errors with `--no-signoff`; each has one new-file
MAINTAINERS warning, covered by the existing CAMSS directory entry.

```bash
git -C build/linux-standard worktree add --detach \
  ../linux-camera-cpas-tests 37328f87d
git -C build/linux-camera-cpas-tests am \
  "$PWD"/kernel/experiments/camera/cpas-tests/000[12]-*.patch
git -C build/linux-camera-cpas-tests rev-parse 'HEAD^{tree}'
```

From the candidate kernel tree, run the real kernel under QEMU/KASAN:

```bash
python3 tools/testing/kunit/kunit.py run --arch=x86_64 \
  --build_dir=../../work/kbuild/camera-cpas-tests-kunit \
  --kunitconfig=../../kernel/experiments/camera/cpas-tests/kunit.config \
  --jobs=8 --timeout=90 'camss-*'
```

The test configuration also enables mutex debugging. KUnit code is excluded
from the ARM64 device configuration. With a configured ARM64 output, run
from the project root:

```bash
make -C build/linux-camera-cpas-tests \
  O="$PWD/work/kbuild/camera-cpas-tests-arm64" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
  drivers/media/platform/qcom/camss/
```

Do not promote this candidate based on host results. Optional-resource
lookup/probe deferral, actual device failures, power-domain ordering, late
RPMh completion and repeated capture remain outside this validation. See the
[report](../../../../docs/相机CPAS回归与CSID引用修复-20261115.md) for evidence
and the next shared-resource design requirements.
