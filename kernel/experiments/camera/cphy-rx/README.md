# SM8550 C-PHY Receiver Preparation

This isolated candidate continues `cpas-order` for the IMX800 main camera.
It carries the CSI-2 bus type from the parsed endpoint through CSIPHY to
CSID, validates one to three distinct normalized C-PHY trios at link setup,
and sets CSID780 RX_CFG0 bit 24 for an SM8550 C-PHY sensor input. A D-PHY
input clears the bit; the test generator uses its existing mux path and
does not inherit the preceding sensor's bus type. Invalid C-PHY links
leave the previous receiver configuration intact.

The endpoint parser still rejects C-PHY. This is deliberate: SM8550 PHY
programming, trio mapping, symbol rate and settle timing are not yet
implemented. Do not remove that rejection or deploy this as a main-camera
capture package until the PHY and sensor stages are ready. The candidate
does not add an IMX800 driver, enable a sensor, change a DTB, or fix MM0.

The register definition is checked against MiCode camera-kernel commit
`6c6099a85cb3154fc8a744c17b6b9f5fd361dea8`, CSID780 `phy_type_shift = 24`,
the ver2 RX configuration path, and `CAM_ISP_LANE_TYPE_CPHY = 1`.
No proprietary mode tables are included.

## Verification and Reproduction

Verify the parent manifest and patch hashes, apply the patch to
`cd5ca4eecfdf2a119f1e38f1dd067f167ac84b72`, and compare the resulting tree
with `manifest.json`. The standard kernel recipe is unchanged.

The added KUnit cases call actual CSID link setup and register-writing
functions. They cover trio remapping, invalid counts, duplicate/out-of-range
trios, unsupported SoCs, C-PHY to D-PHY/TPG transitions, and RX register
values. RAM-backed registers do not emulate CSI traffic or electrical
timing. Existing CPAS/CSID tests remain part of the run.

From the candidate source directory, with a configured build environment:

```bash
python3 tools/testing/kunit/kunit.py run --arch=x86_64 \
  --build_dir=../../work/kbuild/camera-cphy-rx-kunit \
  --kunitconfig=../../kernel/experiments/camera/cpas-tests/kunit.config \
  --jobs=8 --timeout=90 'camss-*'
```

From the project root, with a configured ARM64 output:

```bash
make -C build/linux-camera-cphy-rx \
  O="$PWD/work/kbuild/camera-cphy-rx-arm64" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
  drivers/media/platform/qcom/camss/
```

Validation and the independently confirmed CSIPHY2 / PHY 2.1.2 hardware
source are recorded in [the main-camera report](../../../../docs/主摄C-PHY接收配置-20261115.md).
