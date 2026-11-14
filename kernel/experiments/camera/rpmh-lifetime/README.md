# RPMh Request Lifetime and TCS Wait Candidate

This isolated candidate extends the pinned HF-clock experiment. Patch 1 keeps
active batch commands and completions alive after a caller timeout and rejects
oversized messages before sending or caching any part of the batch. Patch 2
extends ownership to synchronous single reads/writes, releases rejected async
requests, and bounds the wait for a conflict-free TCS. Neither patch resolves
the MM0 hardware timeout.

The caller and each submitted request hold a batch reference. A rejected send
drops its reference immediately; a completed send drops it after signalling its
completion. Sleep/wake batches retain their existing cache/invalidate lifetime.
Standalone heap requests also hold caller and transport references. Read results
are copied to the caller only after successful completion. Allocations retain
the existing RPMh GFP_ATOMIC policy to avoid reclaim recursion in power/clock
paths; synchronous operations can now fail with ENOMEM. Requests which never
finish retain memory because the RSC still owns pointers to them.

A TCS which stays busy or conflicts with an in-flight resource now returns
ETIMEDOUT after a ten-second availability wait, before the new request is
recorded or any hardware is programmed. This is a per-submission limit, separate
from completion waiting, not a ten-second deadline for an entire batch or an
interconnect call. No TCS cancellation, vote bypass, error rollback, or hardware
reset is added.

## Reproduce

Verify the parent manifest hash and tree against `manifest.json`, create an
isolated worktree from the parent commit, and apply the patch:

```bash
git -C build/linux-standard worktree add --detach \
  ../linux-camera-rpmh-lifetime c539e8aaf80f3ccc1ce57bfc6888007020c8ce45
git -C build/linux-camera-rpmh-lifetime am \
  "$PWD"/kernel/experiments/camera/rpmh-lifetime/000[12]-*.patch
git -C build/linux-camera-rpmh-lifetime rev-parse 'HEAD^{tree}'
```

Compare the resulting tree with the manifest. Preserve any existing worktree.
With a configured ARM64 output directory, compile the affected objects:

```bash
make -C build/linux-camera-rpmh-lifetime O="$PWD/work/kbuild/camera-rpmh-lifetime-arm64" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
  drivers/soc/qcom/rpmh.o drivers/soc/qcom/rpmh-rsc.o
```

Run the actual kernel APIs under QEMU with KASAN. Lifetime tests replace the
transport and register snapshot inside KUnit. RSC tests instead run the actual
send/wait/claim path with RAM backing its registers and the real command-address
comparison. A C compiler and qemu-system-x86_64 are required. Run from the
experiment's kernel source directory:

```bash
python3 tools/testing/kunit/kunit.py run --arch=x86_64 \
  --build_dir=../../work/kbuild/camera-rpmh-lifetime-kunit \
  --kunitconfig=../../kernel/experiments/camera/rpmh-lifetime/kunit.config \
  --jobs=6 --timeout=150 'rpmh-*'
```

Eighteen tests cover batch and single-request completion, submission errors,
deferred async completion, late read/write responses, invalid lengths, sleep/wake
caching, full TCS groups, resource conflicts despite a free TCS, release before
timeout, and borrowing a wake TCS. Six cases retain the real ten-second timeout;
the suite takes about a minute to run. Passing these tests is not device
validation.

## Remaining Gates

- Interconnect error propagation requires coordinated consumer rollback and a
  hardware recovery policy; these patches do not make that safe by themselves.
- An expired completion wait does not cancel the command, and the driver cannot
  release its memory or power resources merely because the caller stopped waiting.
- Bounded TCS waiting prevents that particular indefinite wait, but repeated
  consumer retries and shared lock contention can still delay the system.
- The MM0 clock/power-domain cause, capture restart and camera features remain
  unaccepted. No phone kernel package was built or installed for this candidate.

Keep this outside the standard recipe and APT allowlist. Device evidence and
limitations are recorded in the [report](../../../../docs/相机RPMh生命周期-20261114.md).
