# Suspend Boundary Diagnostics

This default-off experiment adds read-only snapshots around ARM64
`cpu_suspend()` in the PSCI SYSTEM_SUSPEND path. It uses the exact cam12order
source and OV13B10 DTB already running on the phone to isolate suspend changes.
It does not exercise a camera or promote the camera experiment to acceptance.

The finisher records a raw PSCI result only if firmware returns directly.
`cpu_suspend()` returning zero through `cpu_resume` is a reset/resume path;
it does not prove system power collapse or a particular deep-sleep duration.
The snapshots record the physical counter, group-1 HPPIR, PMR, pending bits
and enable masks for SGIs, PPIs and standard SPIs. Extended SPIs, LPIs and
secure-only interrupt state are outside the snapshot. Reads are not atomic.
No IAR is read, IRQ acknowledged, mask changed or pending bit cleared.
Pending state observed after resume is not by itself a wake-cause attribution.
Counter behavior across platform power states also needs independent evidence.
Messages are printed in the platform finish callback after device resume.

Verify the parent manifest hash, apply the patches in order to its `result_tree`,
then compare this manifest's `result_tree`. Replay through a separate Git index
has reproduced the captured build tree. The maintained patch includes its
new header; package provenance includes the complete dirty source state.

Build with `scripts/fuxi-build-deb.sh <unique-name>`, `FUXI_KTREE` pointing
at the reconstructed source, and a compatible `FUXI_KOUT`. Preserve the
cam12order configuration additions from `../camera/cpas-order` and enable
`PM_DEBUG`, `PM_SLEEP_DEBUG`, `GENERIC_IRQ_DEBUGFS` and
`ARM_PSCI_SYSTEM_SUSPEND_DIAG`. IRQ debugfs also selects IRQ injection;
this experiment does not use that interface. The follow-up wake-order candidate
adds `0002-disarm-wake-irqs-before-device-irqs.patch`. It disarms the
system-sleep wake IRQs before restoring device IRQs during noirq resume, then
restores device IRQs while those wake IRQs remain masked. This changes only
Linux resume ordering; it does not mask a device IRQ by number or identify a
wake cause.

Before booting, compare the package Image/config/DTB with the build output
and verify the installed entry and recovery checksums. Select the same
`sm8550-xiaomi-fuxi-ov13b10.dtb` through the installer configuration and
restore any temporary configuration after installation. Keep automatic
suspend disabled and use one RTC-guarded test through
`scripts/fuxi-suspend-check.py` after confirming boot and network health.

Validation and device evidence are recorded in the manifest and
[the power report](../../../docs/驱动初始化与功耗验收-20261108.md).

The first wake-order build (`7.4.0-rc1-fuxi-suspend3`) booted successfully and
was tested once. It still resumed before the 20-second RTC alarm: the logind
cycle was 4.142029 seconds, the BOOTTIME compensation was 2.051776 seconds,
and 17 seconds remained on the RTC alarm. The PCIe WAKE# IRQ was IRQ225 in
this boot and had zero trace entries during the resume; IRQ224 was a separate
`dwc3` IRQ with 98 entries. This shows a changed resume interrupt pattern, not
a confirmed wake cause.

The PDC follow-up (`0003-sm8550-pdc-boundary-snapshots.patch`) publishes a
descriptor only after successful SM8550 probe and reads only mapped pins
within the hardware bounds. It samples raw configuration and enable state
around `cpu_suspend()`, with physical-counter brackets for each read. The
built-in PSCI diagnostic has no dependency on a modular or disabled PDC.
SM8550 uses passthrough mode: its GPIO status bits are not treated as latched
wake evidence. The suspend tool also saves read-only platform sleep statistics;
it deliberately avoids `ddr_stats`, whose read can send a QMP request.

`7.4.0-rc1-fuxi-suspend4` completed one 20-second RTC-guarded test but again
returned early: logind cycle 6.677162 seconds, compensation 4.596046 seconds,
RTC remaining 15 seconds. APSS count increased once with a 4.591830-second
recorded interval; AOSD/CXSD stayed at zero. PDC pins 1 (PMIC arbiter) and 109
(GPIO96 PCIe WAKE#) remained enabled without configuration changes. This
supports an APSS sleep interval but does not establish whole-platform deep
sleep, validate every firmware counter, or identify the wake cause. USB/PCIe
L2 errors persisted. The subsequent offline audit found nine sleep and nine
wake BCM commands with zero sleep valid/x/y votes. This records Linux writes,
not firmware execution or absence of hardware low-power blockers.

The IPCC follow-up (`0004-sm8550-ipcc-boundary-snapshots.patch`) samples the
SM8550 built-in controller immediately before and after `cpu_suspend()`,
before the other return-side snapshots and interrupt handlers. It rechecks
CONFIG and reads RECV_ID only when CLEAR_ON_RECV_RD is disabled. It never
writes or acknowledges IPCC state. A raw lock protects the diagnostic pointer
against removal; physical-counter brackets bound each snapshot. Modular and
disabled IPCC configurations leave PSCI independent of the driver symbol.
Only one pending ID is observed, and CONFIG/RECV_ID reads are not atomic;
the result cannot identify the earliest wake source. The new candidate is
`7.4.0-rc1-fuxi-suspend5`; prior device results remain separately recorded
under `previous_validation` in the manifest.

The single suspend5 test recorded no pending IPCC ID before entry and LPASS
client 3/signal 0 immediately after return, before the first traced IRQ13 and
LPASS glink IRQ230. CONFIG was zero on both reads. The logind cycle was
1.998857 seconds, compensation 6.730 ms, and the RTC still had 19 seconds
remaining; deep sleep and RTC wake are not accepted. APSS recorded 2.240 ms,
while AOSD/CXSD/DDR remained zero. All eight trace buffers reported no loss.
WiFi, connected-bearer HTTPS and sensor sampling passed after resume, but
ModemManager reported registered despite connected bearers. This inconsistency
and the LPASS message need further investigation; no wake source is confirmed.

The PMIC follow-up (`0005-pmic-glink-header-metadata.patch`) adds default-off
TX, transport-return and RX events. It reads only the common 12-byte header,
using unaligned little-endian access, and records device, length, header validity,
owner/type/opcode and transport result. Short headers are not decoded. It records
no payload and changes no charging or message handling. A transport return is not
firmware completion, and a header does not uniquely identify a request.

The PMIC candidate is `7.4.0-rc1-fuxi-suspend6`; the original suspend5
validation is retained under `ipcc_validation`. Use the explicit suspend-check
`--pmic-metadata` option to require all three PMIC events and the 13 glink events
before arming the RTC. The five-second baseline also precedes the alarm.
The PMIC object built with ARM64 `W=1` with event tracing enabled and disabled;
all five patches replayed to the current result tree.

One suspend6 test still returned early: logind cycle 6.355196 seconds,
compensation 4.241335 seconds, RTC remaining 14 seconds. Two 220-byte baseline
messages were BATTMGR owner 32778, type 1, opcode 0x50. No such RX message was
recorded after return; the IPCC boundary snapshot instead showed MPSS
client 2/signal 0, followed by IRQ13/IRQ231 and 92-byte IPCRTR data. The earlier
LPASS/PMIC pattern therefore did not recur, and PMIC message length is not a
general explanation for early return. The previous capture has no PMIC headers
and cannot be retroactively decoded. All eight buffers reported no loss.
WiFi, connected-bearer HTTPS and six sensor samples passed after resume;
ModemManager reported connected and key services did not restart. Whole-platform
deep sleep and RTC wake remain unaccepted, with no confirmed wake source.

The current host-validated candidate is `7.4.0-rc1-fuxi-suspend7`, adding
`0006-qrtr-receive-header-metadata.patch`. It traces RX after the existing
QRTR v1/v2 header and length checks, before socket lookup or delivery. It records
version, packet type, route, confirm flag and unpadded payload length. For DATA
on non-control ports only, it checks the seven-byte common QMI header's type and
exact payload length before recording candidate type/message ID/length. It does
not read TLVs, transaction IDs or business data. A matching header does not prove
QMI, identify a service, confirm socket delivery or establish a wake cause.
No send, receive, interrupt or charging behavior is changed when tracing is off.

Use `--qrtr-metadata` to require the RX event and all 13 glink events before the
five-second pre-alarm baseline; combine with `--pmic-metadata` to retain PMIC
coverage. The offline audit groups routes and candidate headers by trace line
order and reports missing or inconsistent metadata. It cannot decode old captures
that did not record QRTR headers.

All six patches replay to the manifest tree, the complete deb and its payloads
have been verified, and QRTR objects passed ARM64 `W=1` with event tracing on and
off. Suspend7 has not been installed or booted: the phone reported 12% battery,
SDP input about 1.8 W and negative battery current during the preflight. Restore
net charging and at least 20% before deployment, then verify boot and recovery
files before one bounded RTC experiment. The running/default version remains
suspend6; its evidence is retained under `pmic_validation`.
