# Powered CPAS State Diagnostic

This isolated experiment extends the RPMh lifetime candidate. It maps the
optional SM8550 CPAS top resource and logs camera/CPAS versions, Q-channel
control, and two status samples while VFE clocks and the CAMSS domain are on.
It neither implements a Q-channel handshake nor resolves the MM0 timeout.

The sample points are successful VFE power-on reset, reset-error cleanup,
before final VFE halt, and before final VFE clock removal. A missing resource
keeps older DTBs on their existing path. No registers are read at probe,
after clock disable, or after a failed runtime resume. Status reads are
sequential, not an atomic snapshot, and logging can alter timing. Multiple
VFEs share CPAS, so one VFE's final reference is not necessarily global idle.

## Reproduce

Verify the parent manifest hash and tree in `manifest.json`. Reconstruct the
RPMh parent first, then use a new worktree without replacing an existing one:

```bash
git -C build/linux-standard worktree add --detach \
  ../linux-camera-cpas-diag de9363f69d693c46fbeff60fdbc242057d218d93
git -C build/linux-camera-cpas-diag am \
  "$PWD"/kernel/experiments/camera/cpas-diag/000[123]-*.patch
git -C build/linux-camera-cpas-diag rev-parse 'HEAD^{tree}'
```

The recorded parent commit is local provenance. A reconstructed parent's commit
ID may differ after `git am`; verify its tree against `base_tree` and use that
verified commit when creating the worktree. Verify every patch hash before
applying it. Compare the result tree with the manifest.

With a configured ARM64 output:

```bash
make -C build/linux-camera-cpas-diag \
  O="$PWD/work/kbuild/camera-cpas-diag-arm64" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
  drivers/media/platform/qcom/camss/ qcom/sm8550-xiaomi-fuxi-ov13b10.dtb
bash scripts/fuxi-build-env.sh run make -C build/linux-camera-cpas-diag \
  O=/src/work/kbuild/camera-cpas-diag-schema \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j4 dt_binding_check \
  DT_SCHEMA_FILES=media/qcom,sm8550-camss.yaml
bash scripts/fuxi-build-env.sh run dt-validate \
  -s work/kbuild/camera-cpas-diag-schema/Documentation/devicetree/bindings/processed-schema.json \
  -l qcom,sm8550-camss \
  work/kbuild/camera-cpas-diag-arm64/arch/arm64/boot/dts/qcom/sm8550-xiaomi-fuxi-ov13b10.dtb
```

The final command validates the CAMSS node, not every board binding. The
binding example retains the older nineteen-region layout, while the actual
candidate DTB exercises the new twenty-region layout. These checks do not
establish hardware accessibility or capture stability.

## Full Package

The host build completed as `cam7cpas`, with package/config/DTB hashes recorded
in the manifest. The extracted kernel image and capture DTB match the build
outputs byte for byte. This package has not been installed or device-tested.
From the repository root, use a fresh package name if `cam7cpas` already exists:

```bash
FUXI_KTREE="$PWD/build/linux-camera-cpas-diag" \
FUXI_KOUT="$PWD/work/kbuild/cam7cpas-out" \
SOURCE_DATE_EPOCH=1794662197 FUXI_BUILD_JOBS=4 \
FUXI_EXTRA_CFG='-e I2C_QCOM_CCI -e I2C_CHARDEV -e FUXI_CAMERA_POWER -e MEDIA_SUPPORT -e MEDIA_SUPPORT_FILTER -e MEDIA_CAMERA_SUPPORT -e MEDIA_PLATFORM_SUPPORT -e V4L_PLATFORM_DRIVERS -e VIDEO_DEV -e VIDEO_QCOM_CAMSS -e VIDEO_OV13B10 -e DMABUF_HEAPS -e DMABUF_HEAPS_SYSTEM -e FTRACE -e ENABLE_DEFAULT_TRACERS -d FUNCTION_TRACER' \
  bash scripts/fuxi-build-deb.sh cam7cpas
```

The build's `build.json` records the standard manifest hash as background
provenance; this experiment's manifest pins the actual candidate recipe and
result tree. The host build has no container environment-lock attestation.

Keep the candidate outside the standard recipe and APT allowlist. Do not
repeat the known second-STREAMON deadlock merely to collect these logs.
Evidence, public source references, and hardware test scope are recorded in
the [report](../../../../docs/相机CPAS状态采样-20261114.md).
