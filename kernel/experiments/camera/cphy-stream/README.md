# SM8550 C-PHY Endpoints and Streaming

This candidate continues the host-tested `cphy-clock` source. It connects
the legacy SM8550 endpoint parser and stream callbacks to the PHY 2.1.2
C-PHY backend. Other SoCs and generic PHY endpoints remain unsupported
for this path. No phone deployment or capture is included.

C-PHY endpoints require one explicit link frequency, one calibrated
`qcom,cphy-settle-count`, and one to three unique trio indices in 0..2.
Only ABC line order is supported. The settle count is a hardware counter
at a 400 MHz timer, not nanoseconds. Both power setup and stream start
reject a sensor frequency different from the endpoint's calibrated rate.
The binding example's 1.5 GHz frequency is illustrative, not an IMX800 PLL
measurement. The experimental property is not an upstream ABI commitment.

A later pipeline startup failure now stops the subdevices that started
successfully. Tests use the actual V4L2 wrapper: it logs driver stop errors,
clears streaming state and returns success. Consequently this cleanup
cannot establish hardware halt or DMA safety after a stop failure, nor
does it fix or validate the observed MM0 failure on the phone.

## Reproduction

Verify the parent manifest and both patch hashes, apply the patches in order
to `3038e196d0839a19ba05f9f354115ef297d0bad4`, and compare the tree with
`manifest.json`. The standard kernel recipe is unchanged.

From the candidate source directory:

```bash
python3 tools/testing/kunit/kunit.py run --arch=x86_64 \
  --build_dir=../../work/kbuild/camera-cphy-stream-kunit \
  --kunitconfig=../../kernel/experiments/camera/cpas-tests/kunit.config \
  --kconfig_add CONFIG_VIDEO_QCOM_CAMSS_CSIPHY_KUNIT_TEST=y \
  --jobs=8 --timeout=90 'camss-*'
```

From the repository root, using a configured ARM64 output tree:

```bash
make -C build/linux-camera-cphy-stream \
  O="$PWD/work/kbuild/camera-cphy-stream-arm64" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
  drivers/media/platform/qcom/camss/
scripts/fuxi-build-env.sh run make -C build/linux-camera-cphy-stream \
  O=/src/work/kbuild/camera-cphy-stream-schema ARCH=arm64 \
  DT_SCHEMA_FILES=media/qcom,sm8550-camss.yaml dt_binding_check
```

125 QEMU tests passed with KASAN and DEBUG_MUTEXES, including 41 added
endpoint, C-PHY stream, D-PHY register regression and pipeline cleanup
cases. ARM64 W=1 and the binding's D-PHY/C-PHY examples passed. Checkpatch
reports zero errors and one new-file reminder covered by MAINTAINERS.
Three expected stop-error log messages come from deliberate fault injection;
there are no kernel WARNING/KASAN reports or uninitialized entity warnings.

The IMX800 module, PLL, board wiring, driver, RAW output and device lifecycle
acceptance are still pending. See [the report](../../../../docs/主摄C-PHY端点与启停-20261115.md).
