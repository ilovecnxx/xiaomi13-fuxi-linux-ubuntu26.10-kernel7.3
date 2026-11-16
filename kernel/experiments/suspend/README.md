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

Verify the parent manifest hash, apply the patch to its `result_tree`, then
compare this manifest's `result_tree`. Replay through a separate Git index
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
