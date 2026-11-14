# CPAS Status With Temporary ICP Clock

This isolated candidate extends `cpas-diag`. It compares Q-channel status
before, during, and after temporarily enabling ICP on measured Titan 780 /
CPAS 1.0.0 hardware with control equal to 1. It does not write Q-channel
requests or change clock rates. QACCEPTN polling has a 10 ms diagnostic
budget; this is not a complete time bound on clock framework operations.

Successful clock acquisition is balanced on both poll success and timeout.
Enable failures are logged without releasing an unacquired reference.
Unknown versions, absent optional clocks, and other control values skip the
clock experiment. Missing clocks retain the original powered snapshots;
probe deferral and other lookup errors propagate. Register accesses occur
while the calling VFE still holds its clocks and runtime power reference.

The mutex serializes diagnostic windows across VFEs, not every CPAS client.
A final VFE reference is not proof that the shared camera subsystem is idle.
Poll failures are diagnostic output, not stream errors or recovered hardware.
No second STREAMON or RPMh fault injection is part of this experiment.

## Reproduce

Verify the parent manifest hash and all patch hashes in `manifest.json`.
Reconstruct the parent recipe and verify `base_tree` before applying these
patches in a new worktree. The recorded commit is local provenance; commit
IDs can change when replayed, but the resulting tree must match.

```bash
git -C build/linux-standard worktree add --detach \
  ../linux-camera-cpas-icp 69c709d250e7a6f9c0ddb8dfb6ab96b5838db1b1
git -C build/linux-camera-cpas-icp am \
  "$PWD"/kernel/experiments/camera/cpas-icp/000[123]-*.patch
git -C build/linux-camera-cpas-icp rev-parse 'HEAD^{tree}'
```

With an existing configured ARM64 output, run:

```bash
make -C build/linux-camera-cpas-icp O="$PWD/work/kbuild/cam8icp-out" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LOCALVERSION=-fuxi-cam8icp \
  W=1 -j4 drivers/media/platform/qcom/camss/ \
  qcom/sm8550-xiaomi-fuxi-ov13b10.dtb
bash scripts/fuxi-build-env.sh run make -C build/linux-camera-cpas-icp \
  O=/src/work/kbuild/camera-cpas-icp-schema ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- -j4 dt_binding_check \
  DT_SCHEMA_FILES=media/qcom,sm8550-camss.yaml
bash scripts/fuxi-build-env.sh run dt-validate \
  -s work/kbuild/camera-cpas-icp-schema/Documentation/devicetree/bindings/processed-schema.json \
  -l qcom,sm8550-camss \
  work/kbuild/cam8icp-out/arch/arm64/boot/dts/qcom/sm8550-xiaomi-fuxi-ov13b10.dtb
```

The example validates the original 36-clock interface; the real candidate
DTB exercises 37 clocks. The filter validates CAMSS, not every board binding.

Build a fresh package name if `cam8icp` already exists:

```bash
FUXI_KTREE="$PWD/build/linux-camera-cpas-icp" \
FUXI_KOUT="$PWD/work/kbuild/cam8icp-out" \
SOURCE_DATE_EPOCH=1794677633 FUXI_BUILD_JOBS=8 \
FUXI_EXTRA_CFG='-e I2C_QCOM_CCI -e I2C_CHARDEV -e FUXI_CAMERA_POWER -e MEDIA_SUPPORT -e MEDIA_SUPPORT_FILTER -e MEDIA_CAMERA_SUPPORT -e MEDIA_PLATFORM_SUPPORT -e V4L_PLATFORM_DRIVERS -e VIDEO_DEV -e VIDEO_QCOM_CAMSS -e VIDEO_OV13B10 -e DMABUF_HEAPS -e DMABUF_HEAPS_SYSTEM -e FTRACE -e ENABLE_DEFAULT_TRACERS -d FUNCTION_TRACER' \
  bash scripts/fuxi-build-deb.sh cam8icp
```

The package is built on the host, without container build attestation;
binding checks use the pinned build environment. Package `build.json` lists
the standard manifest as background provenance; this recipe pins the actual
experimental source tree. Keep the candidate outside the standard recipe
and APT allowlist. Results are recorded in the
[report](../../../../docs/相机CPAS-ICP诊断-20261115.md).

## Device Result

The candidate booted and delivered one five-frame RAW stream. At reset-done,
status changed from 0 to 1 after enabling ICP at its existing 19.2 MHz rate.
The later before-halt and before-clock-off samples remained 1. All three
polls succeeded, and control remained 1 throughout. A later snapshot showed
CAMSS suspended, TITAN_TOP off, and ICP/HF/CAMNOC/CPAS AHB enable counts zero.

This establishes a clock-dependent observation, not a validated stop
handshake or MM0 fix. Status remaining 1 after clock release may be a held
value; this test does not establish unclocked status freshness. No second
stream was attempted, and no image was saved or visually assessed.
The phone was restored to deb9 with deb8 fallback, zero boot counters, and
zero failures in the wireless/sensor health check. Sensor calibration and
installation-matrix warnings remain outside this diagnostic's acceptance.
