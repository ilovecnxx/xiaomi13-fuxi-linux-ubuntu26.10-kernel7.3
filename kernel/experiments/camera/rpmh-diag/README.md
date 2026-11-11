# RPMh Timeout Diagnostic Candidate

2026-11-11: this candidate adds register evidence for the
[reproduced cam4 stall](../../../../docs/相机实机复现-20261111.md).
It is an additional series on the unchanged cam4 content tree, not a fix or
a device-validated kernel. No deb was built or installed in this session.
SSH failed locally with `socket: Operation not permitted`, so the device's
recovery state remains unknown.

## Evidence and Scope

The captured failure has six commands on apps_rsc TCS 0 without a completion,
while TCS 1 still completes requests. The same MC0/MM0 values completed earlier
in that boot. The existing log identifies the batch but cannot distinguish a
command awaiting a resource response from a completed TCS awaiting IRQ handling.

On the existing `rpmh_write_batch()` timeout path, the new helper:

- Matches the still-in-use request by identity under the controller lock.
- Uses trylock and reports an unavailable snapshot if the lock is busy.
- Copies TCS control/status, wait mask, IRQ enable/status and command enable.
- Copies each command's address, data, message ID and raw status; reports the
  existing issued/completed bit definitions and whether a response is required.
- Prints after unlocking. It does not allocate memory, write registers,
  acknowledge interrupts, change votes or reclaim a TCS.

The lock prevents TCS reuse during the copy. IRQ cleanup on another CPU can
still change registers before taking that lock, so the output explicitly labels
the reads as sequential and retains before/after IRQ and command-enable values.
Equal endpoint values do not prove the hardware was stable throughout the reads.
The dump covers only the timed-out request's TCS, not all controller activity.

## Reconstruct and Check

Follow the parent README to reconstruct cam4 in a new worktree. For the commands
below, use `build/linux-camera-diag` as that worktree. Verify all patch SHA-256s
against the parent manifest and this directory's manifest before applying.

```bash
git -C build/linux-camera-diag rev-parse 'HEAD^{tree}'
# Must be 599970e4ba1c1cbff7d4c113c85476d6ceb2e3cb before this series.
git -C build/linux-camera-diag am "$PWD"/kernel/experiments/camera/rpmh-diag/000*.patch
git -C build/linux-camera-diag rev-parse 'HEAD^{tree}'
# Must be 4307ddbda85cb6880309ffd09fdfb755d213d668 after this series.
```

The focused build used a separate `work/kbuild/camera-diag-out` directory,
initialized from the restored host CI `.config`, then enabled the parent
manifest's `extra_config` options and ran `olddefconfig`. Compiler, final config
hash and targets are recorded in this manifest. Both objects compiled with no
compiler warnings:

```bash
make -C build/linux-camera-diag O="$PWD/work/kbuild/camera-diag-out" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
  drivers/soc/qcom/rpmh.o drivers/soc/qcom/rpmh-rsc.o
```

`checkpatch.pl --no-signoff` reported zero errors and warnings. This is a local
development patch with no claim of author DCO approval. The standard source
check still matches `90afc07da83d97efc71e2a2d2776e6a88681d242`.
Compilation does not exercise MMIO, timeout or IRQ concurrency on hardware.

## Next Device Investigation

First establish recovery to the verified deb9/deb8 entries and check filesystem,
package audit and blocked threads. Those were the latest actual fallback entries;
older std2/chg3 documentation is not the current device baseline. A full isolated
kernel build and boot-artifact verification are still needed before testing this
candidate. Keep both verified fallback entries and collect logs on the host.

On a controlled reproduction, correlate the new TCS/command index and address
with RPMh send/done trace events and CAMSS runtime-PM events. Inspect only enabled
commands, and consider `response_required` before interpreting `complete=0`.
An issued command lacking completion can narrow the unresolved resource path;
it does not prove that resource caused the stall or establish which failed first.
Pending IRQ and enable transitions may indicate concurrent completion instead.
A missing snapshot is an evidence gap, not proof that the TCS was healthy.

The existing failure mechanisms remain: resource-address conflicts can make
`rpmh_rsc_send_data()` wait indefinitely, `qcom_icc_set()` drops commit errors,
and `rpmh_write_batch()` frees its request/completion allocation after timeout
despite possible late IRQ access. The existing source warns about this lifetime
hazard. This diagnostic runs before that release; it does not repair it. Do not
claim safe timeout recovery, promote this series into the standard recipe or
interpret a successful boot as camera acceptance.
