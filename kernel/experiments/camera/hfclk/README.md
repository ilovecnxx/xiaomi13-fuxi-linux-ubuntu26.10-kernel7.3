# SM8550 HF Clock Experiment

This isolated candidate extends cam5diag with one SM8550 resource-table change:
add `gcc_axi_hf` to the existing CAMSS PM clock list. It is a timing hypothesis,
not a verified fix, and must not enter the standard recipe or APT allowlist.

The full package and CAMSS W=1 build passed, along with 342 host tests. Device
testing completed RAW and four half/full CPU pairs without an RPMh timeout.
The fifth full-size run aborted on a libcamera array assertion; its kernel
cleanup completed. Static traces verify HF coverage of the ICC transitions.
Runtime aggregate MM0 votes differ from the prior failure, so these results
do not establish that the candidate fixes that failure. See the report below.

The saved cam5diag trace contains two completed six-command enable batches and
one stalled batch with identical addresses, values and response flags. MM0 is
the only response-required command still incomplete in the timeout snapshot.
The successful batches took 51 and 44 microseconds. These observations do not
identify a bad bandwidth value or prove a clock dependency.

`vfe_get()` resumes CAMSS before enabling its clocks, while `vfe_put()` disables
those clocks before suspending CAMSS. The runtime callbacks raise and drop ICC
votes. The PM clock list enables the HF gate before raising votes and disables
it after dropping votes. VFE retains its own balanced reference to the same
clock. The GCC gate uses `BRANCH_HALT_SKIP`, has no rate-setting parent, and the
candidate does not change clock rates, DT, bandwidth, power-domain order, or
RPMh request handling. `CONFIG_PM_CLK=y` is required for this experiment.

## Reconstruct and Build

Verify the parent RPMh diagnostic manifest and tree first, then create a fresh
worktree from that tree's commit and apply this directory's patch. The manifest
pins the patch hash and resulting tree. Preserve existing experiment worktrees.

```bash
git -C build/linux-standard worktree add --detach ../linux-camera-hfclk <cam5diag-commit>
git -C build/linux-camera-hfclk am "$PWD"/kernel/experiments/camera/hfclk/0001-*.patch
git -C build/linux-camera-hfclk rev-parse 'HEAD^{tree}'
# 6fce8068ea12f794d28ee05b6883c4a478ad1604

scripts/fuxi-build-env.sh run env \
  GIT_DIR=/src/build/linux-standard/.git/worktrees/linux-camera-hfclk \
  GIT_WORK_TREE=/src/build/linux-camera-hfclk \
  FUXI_KTREE=/src/build/linux-camera-hfclk \
  FUXI_KOUT=/src/work/kbuild/camera-hfclk-out \
  SOURCE_DATE_EPOCH=1794413041 FUXI_BUILD_JOBS=6 \
  FUXI_EXTRA_CFG='-e I2C_QCOM_CCI -e I2C_CHARDEV -e FUXI_CAMERA_POWER -e MEDIA_SUPPORT -e MEDIA_SUPPORT_FILTER -e MEDIA_CAMERA_SUPPORT -e MEDIA_PLATFORM_SUPPORT -e V4L_PLATFORM_DRIVERS -e VIDEO_DEV -e VIDEO_QCOM_CAMSS -e VIDEO_OV13B10 -e DMABUF_HEAPS -e DMABUF_HEAPS_SYSTEM -e FTRACE -e ENABLE_DEFAULT_TRACERS -d FUNCTION_TRACER' \
  scripts/fuxi-build-deb.sh cam6hfclk
```

Use a fresh package name if an output already exists. Install only the Linux deb
with the OV13B10 DTB, keep deb9/default and deb8/fallback available, and verify
the boot artifacts before testing. Record clock enable/disable, ICC, RPMh and
runtime-PM events to the host. Confirm actual HF gate coverage and suspended
clock reference counts; a successful boot is not camera acceptance. Stop on
the first RPMh timeout, blocked task or failed capture cleanup. Do not clear
TCS state, bypass responses or change firmware/Android partitions.

The prior BCB clear was a one-time authorized recovery action, not permission
to clear Android boot-control data in future tests. Current results are in
[the HF clock report](../../../../docs/相机HF时钟实验-20261112.md).
