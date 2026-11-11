# Fuxi Camera Experiment

This series is separate from `kernel/manifest.json`. It demonstrates OV13B10
capture, but is not a daily-use or release candidate: both cam3 and cam4 stream
starts triggered RPMh interconnect timeouts and blocked storage and CPU-frequency
workers. CPU RGB passed 1200 frames at 2104x1560, but its subsequent full-size
start reproduced the fault. See the [device results](../../../docs/相机验收-20261108.md)
before device work. CPU mode is not a validated workaround.

`manifest.json` records the standard base tree, ordered patch hashes, final tree,
configuration and timestamp. These are development patches, without a claim of
upstream review or author DCO approval. Do not add this kernel to the APT kernel
allowlist until the failures have been resolved and regression tested.

2026-11-11 follow-up: an isolated [RPMh register diagnostic candidate](rpmh-diag/README.md)
adds per-command timeout evidence on top of this unchanged cam4 recipe. Its two
RPMh objects pass a focused ARM64 build. The subsequent cam5diag full package and
boot passed, but streaming reproduced the interconnect stall with MM0 awaiting
a response; see the [2026-11-12 record](../../../docs/相机MM0诊断-20261112.md).

## Reconstruct

Start with the standard recipe and a new, unused worktree path. Do not overwrite
an existing camera/voice experiment. Commands below run from the repository root:

```bash
python3 scripts/fuxi-kernel-source.py prepare
python3 scripts/fuxi-kernel-source.py check
git -C build/linux-standard rev-parse 'HEAD^{tree}'
# Must be 90afc07da83d97efc71e2a2d2776e6a88681d242.
git -C build/linux-standard worktree add --detach ../linux-camera HEAD
git -C build/linux-camera am "$PWD"/kernel/experiments/camera/000*.patch
git -C build/linux-camera rev-parse 'HEAD^{tree}'
# Must be 599970e4ba1c1cbff7d4c113c85476d6ceb2e3cb.
git -C build/linux-camera status --porcelain
# Must be empty.
```

Verify each patch's SHA-256 against the manifest before applying it. The final
tree is checked by applying the ordered patches to an isolated Git
index initialized from the standard base. Commit IDs after `git am` may differ;
the content tree must match.

## Build

The tested build used the pinned container, GCC 15.3.0 (Ubuntu 15.3.0-4ubuntu1)
and 12 jobs. The explicit Git paths below resolve the linked worktree inside the
container; the host's absolute `.git` pointer is not valid there.

```bash
scripts/fuxi-build-env.sh run env \
  GIT_DIR=/src/build/linux-standard/.git/worktrees/linux-camera \
  GIT_WORK_TREE=/src/build/linux-camera \
  FUXI_KTREE=/src/build/linux-camera \
  FUXI_KOUT=/src/work/kbuild/camera-out \
  SOURCE_DATE_EPOCH=1794070400 \
  FUXI_EXTRA_CFG='-e I2C_QCOM_CCI -e I2C_CHARDEV -e FUXI_CAMERA_POWER -e MEDIA_SUPPORT -e MEDIA_SUPPORT_FILTER -e MEDIA_CAMERA_SUPPORT -e MEDIA_PLATFORM_SUPPORT -e V4L_PLATFORM_DRIVERS -e VIDEO_DEV -e VIDEO_QCOM_CAMSS -e VIDEO_OV13B10 -e DMABUF_HEAPS -e DMABUF_HEAPS_SYSTEM -e FTRACE -e ENABLE_DEFAULT_TRACERS -d FUNCTION_TRACER' \
  scripts/fuxi-build-deb.sh cam4
```

Use a new build name for any changed source/configuration. The historical cam3 package is
`linux-image-7.4.0-rc1-fuxi-cam3_7.4.0-rc1-cam3_arm64.deb`, SHA-256
`92ecd0f10dc3369d2b4d280ced8bea43ee559b79de94cfa9183c350f52e7063f`.
The saved config SHA-256 is
`30b6dea23703dffcb87f6825ecb2d2f674c58ed6482994060572fe3cd79510c9`.

The first four patches produce the historical cam3 tree
`dddedead4e7bd190635ac518294cd9acc0aa32e0`. Patch 5 adds timeout diagnostics;
cam4 also enables the existing RPMh and runtime-PM trace events. Neither change
fixes the interconnect failure. Trace events are disabled by default; collect
`trace_pipe` on the host before starting a controlled capture test so evidence
does not depend on the device's UFS storage remaining responsive.

cam4 captured a six-command timeout batch (MC0, SH1, SH0, MM1, MM0, ACV) during
CAMSS runtime resume. This identifies the batch, not an individual failed command.
GPU RGB also repeatedly stopped producing frames; temporary CPU-latency 300 us
and no-Agc tuning controls did not prevent that. These are separate observations
until their relationship is established. Do not deploy a global CPU ISP override.

cam4 deb SHA-256:
`a8b5b2791e09bf56a5b05473510ac52acd91f128b27352c6fa78861b4c427d22`;
config SHA-256:
`c61fde8382a4ae6b509acab8027afc77ec52dcfe64228ee6b84419650df7e549`.
Its full build and focused `W=1 drivers/soc/qcom/rpmh.o` check passed.

Focused checks used `W=1` for `drivers/misc/fuxi-camera-power.o`,
`drivers/i2c/busses/i2c-qcom-cci.o`,
`drivers/media/platform/qcom/camss/`, `drivers/media/i2c/ov13b10.o`,
`drivers/dma-buf/heaps/` and the camera DTBs. The new OV13B10 binding passed
`dt_binding_check`. The DT build retains the existing unrelated DSI
`avoid_unnecessary_addr_size` warning.

## Device Boundary

Only Linux kernel deb installation is used. The capture variant is
`sm8550-xiaomi-fuxi-ov13b10`, not the manual-power diagnostic variant. Preserve
the verified std2 kernel and its boot artifacts. Do not flash recovery or Android
partitions for this experiment. Automatic boot confirmation only proves startup
health; it does not certify camera streaming or recovery from an interconnect
failure.

The deb contains multiple DTBs; building it does not select the capture variant
for installation. The tested installation temporarily set
`FUXI_DTB=sm8550-xiaomi-fuxi-ov13b10` in `/etc/fuxi/kernel.conf` (which was absent
before the test), then removed that temporary file. It retained std2 as fallback
and compared the installed boot DTB byte-for-byte with the packaged OV13B10 DTB.
Do not overwrite an existing device configuration when reproducing the test.

The system DMA heap is required for libcamera's software ISP. Device support
contains `81-fuxi-camera.rules` for the `video` group and active-seat access.
After deploying that rule, verify `/dev/dma_heap/system` permissions and refresh
WirePlumber if it had already probed the camera without access. No desktop restart
is required by the permission change.
