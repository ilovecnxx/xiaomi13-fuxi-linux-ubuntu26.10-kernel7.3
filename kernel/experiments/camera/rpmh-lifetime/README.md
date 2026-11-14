# RPMh Batch Lifetime Candidate

This isolated candidate extends the pinned HF-clock experiment. It keeps active
batch commands and completions alive after a caller timeout, until all submitted
requests report TX completion. It also rejects oversized messages before sending
or caching any part of the batch. It does not resolve MM0 hardware timeouts.

The caller and each submitted request hold a batch reference. A rejected send
drops its reference immediately; a completed send drops it after signalling its
completion. Sleep/wake batches retain their existing cache/invalidate lifetime.
Requests which never finish retain memory because the RSC still owns pointers to
them. No TCS cancellation, vote bypass, error rollback, or hardware reset is added.

## Reproduce

Verify the parent manifest hash and tree against `manifest.json`, create an
isolated worktree from the parent commit, and apply the patch:

```bash
git -C build/linux-standard worktree add --detach \
  ../linux-camera-rpmh-lifetime c539e8aaf80f3ccc1ce57bfc6888007020c8ce45
git -C build/linux-camera-rpmh-lifetime am \
  "$PWD"/kernel/experiments/camera/rpmh-lifetime/0001-*.patch
git -C build/linux-camera-rpmh-lifetime rev-parse 'HEAD^{tree}'
```

Compare the resulting tree with the manifest. Preserve any existing worktree.
With a configured ARM64 output directory, compile the affected objects:

```bash
make -C build/linux-camera-rpmh-lifetime O="$PWD/work/kbuild/camera-rpmh-lifetime-arm64" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
  drivers/soc/qcom/rpmh.o drivers/soc/qcom/rpmh-rsc.o
```

Run the actual kernel APIs under QEMU with KASAN; the transport and register
snapshot are replaced only inside KUnit. A C compiler and qemu-system-x86_64 are
required. Run from the experiment's kernel source directory:

```bash
python3 tools/testing/kunit/kunit.py run --arch=x86_64 \
  --build_dir=../../work/kbuild/camera-rpmh-lifetime-kunit \
  --kunitconfig=../../kernel/experiments/camera/rpmh-lifetime/kunit.config \
  --jobs=6 --timeout=90 rpmh-batch-lifetime
```

Seven tests cover reverse completion order, submission errors, late completion
after a real ten-second timeout, a partially submitted batch, invalid lengths and
sleep/wake cache invalidation. Passing these tests is not device validation.

## Remaining Gates

- The TCS availability/conflict wait is still unbounded.
- Synchronous single-request stack lifetimes and async send-error cleanup need
  separate work before a general RPMh timeout-recovery claim is possible.
- Interconnect error propagation requires coordinated consumer rollback and a
  hardware recovery policy; this patch does not make that safe by itself.
- The MM0 clock/power-domain cause, capture restart and camera features remain
  unaccepted. No phone kernel package was built or installed for this candidate.

Keep this outside the standard recipe and APT allowlist. Device evidence and
limitations are recorded in the [report](../../../../docs/相机RPMh生命周期-20261114.md).
