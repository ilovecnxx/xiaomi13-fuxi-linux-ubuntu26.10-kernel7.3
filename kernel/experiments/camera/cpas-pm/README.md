# CPAS Runtime Power Handshake Candidate

This isolated candidate continues the verified `cpas-tests` source. It gives
SM8550 CAMSS runtime PM ownership of CPAS AHB and CAMNOC clocks, so the last
VFE can release its local clock references before the global stop handshake.
The optional CPAS register window selects this path; when present, ICP and
HF clocks are required. Older DTBs without that window keep their old path.
The standard kernel recipe is not changed.

Resume enables HF, applies the existing bandwidth requests, sets the shared
AHB/CAMNOC clocks to the existing VFE minimum rates (80/300 MHz), enables
them, and requests Q-channel start with ICP temporarily enabled. Suspend
requests stop with ICP enabled, waits for QACCEPTN and QDENY to clear, then
removes bandwidth and disables the shared clocks followed by HF. Register
writes are limited to the measured Titan 780 / CPAS 1.0.0 layout and known
control bits. Polling has a 10 ms budget, not a complete clock/RPMh time bound.

HF is managed explicitly because `pm_clk_resume()` does not propagate each
clock-enable error. Failure to acquire any clock reaches runtime PM. Failed
start unwinds acquired resources before producers can start. Failed stop
returns `-EIO`, retaining shared clocks and preventing a later runtime get;
reboot is required. This is containment, not automatic hardware recovery.
If an ICC suspend request fails after earlier requests succeeded, clocks
remain on but those earlier votes may already be cleared.

The Qualcomm ICC provider still discards BCM commit errors, and the ICC core
may retry a previous vote after an error. This candidate does not resolve
those mechanisms, prove late RPMh completion safe, or establish MM0 recovery.
Hardware acceptance must reject any timeout even if the consumer returned 0.

Device result (2026-11-15): cam9pm boots and two separate five-frame RAW
captures stop cleanly. A 60-frame GPU RGB capture completes, but CPAS stop
times out with status 0x5, latching runtime PM error and retaining shared
clocks. Further captures are stopped. This candidate has not passed RGB
lifecycle acceptance or established an MM0 fix; see the manifest report.

## Verification and Reproduction

The KUnit suite calls the actual runtime callbacks through the runtime PM
core, with counted common-clock providers and RAM registers. It covers two
users sharing 20 start/stop cycles, HF/AHB/CAMNOC/ICP acquisition failures,
rate failures, unknown register layouts, start timeout, stop timeout/deny,
and failed ICP acquisition at stop. It checks the last-user boundary, clock
ordering, reference cleanup, and rejection of new users after a failed stop.
Status responses are supplied by the fixture; these tests do not emulate
hardware Q-channel transitions, genpd, or an ICC provider.

Verify the parent manifest and patch hashes, then apply the exported patch
to a fresh worktree at `bb569ab46ebf23ca3b82c33b00f4dc57d8443008` and compare
the resulting tree with `manifest.json`. Local commit IDs are provenance;
replayed commit IDs may differ.

```bash
git -C build/linux-standard worktree add --detach \
  ../linux-camera-cpas-pm bb569ab46ebf23ca3b82c33b00f4dc57d8443008
git -C build/linux-camera-cpas-pm am \
  "$PWD"/kernel/experiments/camera/cpas-pm/0001-*.patch
```

From the candidate source directory:

```bash
python3 tools/testing/kunit/kunit.py run --arch=x86_64 \
  --build_dir=../../work/kbuild/camera-cpas-pm-kunit \
  --kunitconfig=../../kernel/experiments/camera/cpas-tests/kunit.config \
  --jobs=8 --timeout=90 'camss-*'
```

From the project root, with a configured ARM64 output:

```bash
make -C build/linux-camera-cpas-pm \
  O="$PWD/work/kbuild/camera-cpas-pm-arm64" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
  drivers/media/platform/qcom/camss/

FUXI_KTREE="$PWD/build/linux-camera-cpas-pm" \
FUXI_KOUT="$PWD/work/kbuild/cam9pm-out" FUXI_BUILD_JOBS=8 \
FUXI_EXTRA_CFG='-e I2C_QCOM_CCI -e I2C_CHARDEV -e FUXI_CAMERA_POWER -e MEDIA_SUPPORT -e MEDIA_SUPPORT_FILTER -e MEDIA_CAMERA_SUPPORT -e MEDIA_PLATFORM_SUPPORT -e V4L_PLATFORM_DRIVERS -e VIDEO_DEV -e VIDEO_QCOM_CAMSS -e VIDEO_OV13B10 -e DMABUF_HEAPS -e DMABUF_HEAPS_SYSTEM -e FTRACE -e ENABLE_DEFAULT_TRACERS -d FUNCTION_TRACER' \
  bash scripts/fuxi-build-deb.sh cam9pm
```

Use a new package name if the output already exists. Keep the latest passed
device version running/default after validation. An older verified kernel
is a recovery option; do not automatically restore it after successful tests.
The [basic camera goal](../../../../docs/基础相机目标.md) remains active until
the real preview, photo, review, restart and reboot criteria pass.
