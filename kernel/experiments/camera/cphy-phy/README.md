# SM8550 PHY 2.1.2 C-PHY Backend

This candidate continues the host-tested `cphy-rx` source. It adds a
register-programming backend for the IMX800 main-camera path, with all
28 vendor rate bins, 16-bit settle counts, normalized trio masks, reset,
common configuration and stop. Existing D-PHY paths are unchanged.

The backend is compiled but **not connected to stream-on**. The endpoint
parser continues to reject C-PHY. Its API accepts vendor bandwidth in
bits/s per trio and an already resolved hardware settle count. Neither
argument is a V4L2 link frequency or a sensor-module encoded field.
The caller must own clocks/power and serialize configuration and stop.
IMX800 timing conversion, RX clock voting, endpoint integration and a
sensor driver are still required before deployment.

The register tables are derived from MiCode camera-kernel commit
`6c6099a85cb3154fc8a744c17b6b9f5fd361dea8`, file
`drivers/cam_sensor_module/cam_csiphy/include/cam_csiphy_2_1_2_hwreg.h`
(SHA256 `2725549447a6e2376cada08a416e47044a12afab2f191e86b8f709f458627c1d`).
They retain the original GPL-2.0-only license and Qualcomm copyright.
The vendor's rounded 2.28 bits/symbol bandwidth thresholds are preserved;
values beyond the final bin are rejected instead of silently clamped.
All three trio banks are initialized in vendor order, with the lane-enable
mask selecting the active trios. PHY IRQs remain masked, matching CAMSS.

## Reproduction

Verify the parent manifest and patch hashes, apply the patch to
`e4daeb8a8ed782e7184196551855658b85221e27`, then compare the result tree
against `manifest.json`. This does not change the standard kernel recipe.

From the candidate source directory:

```bash
python3 tools/testing/kunit/kunit.py run --arch=x86_64 \
  --build_dir=../../work/kbuild/camera-cphy-phy-kunit \
  --kunitconfig=../../kernel/experiments/camera/cpas-tests/kunit.config \
  --kconfig_add CONFIG_VIDEO_QCOM_CAMSS_CSIPHY_KUNIT_TEST=y \
  --jobs=8 --timeout=90 'camss-*'
```

From the repository root, with a configured ARM64 output tree:

```bash
make -C build/linux-camera-cphy-phy \
  O="$PWD/work/kbuild/camera-cphy-phy-arm64" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
  drivers/media/platform/qcom/camss/
```

68 QEMU/KASAN tests passed, including 23 new PHY cases. Tests use RAM
registers, not CSI traffic, power sequencing or electrical timing.
Checkpatch reports zero errors and one new-file MAINTAINERS reminder;
the existing CAMSS directory entry covers both new files.
See [the report](../../../../docs/主摄C-PHY寄存器后端-20261115.md) for limits
and the distinction between source validation and device acceptance.
