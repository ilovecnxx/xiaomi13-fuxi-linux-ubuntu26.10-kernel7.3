# SM8550 C-PHY Link Frequency and Clocks

Continue the host-tested `cphy-phy` source without changing the device's
running/default cam12order kernel. C-PHY now requires an explicit sensor
link frequency, from get_mbus_config or V4L2_CID_LINK_FREQ. It cannot fall
back to the D-PHY pixel-clock formula. The D-PHY fallback is retained.

The PHY helper converts V4L2 link frequency to bits/s per trio using 32/7,
rounding up and rejecting values outside the PHY table before multiplication.
SM8550 selects the RX clock above floor(data_rate / 32) + 1; the independent
timer stays at 400 MHz. Both rounded and resulting clock rates are checked.
This detects a failed provider rate change even when CCF returns success.
On power-up failure the existing caller releases regulators and runtime PM
before enabling the PHY clocks. Clock rates may remain programmed while off;
this does not imply clock-enable or power-reference leakage.

The endpoint parser still rejects C-PHY. Stream integration, settle transport,
IMX800 mode/driver and board wiring are outstanding. Neither the mathematical
test frequency nor a module outputPixelClock field establishes the sensor PLL
frequency. The module-to-CamX conversion evidence and its limits are recorded
in [the report](../../../../docs/主摄C-PHY频率与时钟-20261115.md).

## Reproduction

Verify the parent manifest and patch hashes, apply the patch to
`cd664341a4bf475c5e9a52665ba254b039c0cb13`, then compare the result tree
with `manifest.json`. This does not change the standard kernel recipe.

From the candidate source directory:

```bash
python3 tools/testing/kunit/kunit.py run --arch=x86_64 \
  --build_dir=../../work/kbuild/camera-cphy-clock-kunit \
  --kunitconfig=../../kernel/experiments/camera/cpas-tests/kunit.config \
  --kconfig_add CONFIG_VIDEO_QCOM_CAMSS_CSIPHY_KUNIT_TEST=y \
  --jobs=8 --timeout=90 'camss-*'
```

From the repository root, with a configured ARM64 output tree:

```bash
make -C build/linux-camera-cphy-clock \
  O="$PWD/work/kbuild/camera-cphy-clock-arm64" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
  drivers/media/platform/qcom/camss/
```

84 QEMU/KASAN tests passed, including 16 added tests using real V4L2 controls,
media links and the common clock framework with a mock provider. No CSI
signaling or phone power sequencing is simulated. Checkpatch: zero errors,
one new-file reminder already covered by the CAMSS MAINTAINERS entry.
