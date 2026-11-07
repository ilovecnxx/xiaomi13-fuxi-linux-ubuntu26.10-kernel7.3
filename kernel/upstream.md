# Upstream Patch Ownership and Review

The manifest is the complete downstream source recipe. Its subsystem patches
are generated net differences against the pinned upstream tarball; each changed
path occurs once. `history.json` preserves attribution, including earlier local
experiments that were removed from the final source. Do not invent Signed-off-by
trailers or submit these combined patches unchanged.

| Group | Submission work |
| --- | --- |
| panel | Separate debugfs prototype, review bindings and panel sequencing; update MAINTAINERS |
| touchscreen | Review extending stmfts versus a new FTM5 driver; validate bindings; update MAINTAINERS |
| dsi | Explain hs_rate handling and separate test-pattern workaround |
| audio | Separate soc-ops fix from CS35L41 board routing; supply missing sound binding |
| qrtr | Isolate pending HELLO fix with a reproducer |
| ipa | Split SMEM table, GSI halt and modem ownership changes |
| scm | Review diagnostic severity and error-path justification |
| ath12k | Separate regulatory changes; document firmware/regulatory behavior |
| ufs | Separate reset, HS-G5 clock and shutdown workarounds; justify device scope |
| charging | Local Xiaomi firmware ABI/authentication/diagnostics; review ABI before proposing upstream |
| platform | Local USB tuning/PDR diagnostics; separate SMMU fix and replace hard-coded PHY tuning with binding properties |
| camera-bringup | Local diagnostic power switch; not an upstream camera driver |
| board | Run dt_binding_check/dtbs_check after driver bindings are settled; retain only the normal board for submission |
| config | Local product configuration; not a generic arm64 defconfig change |

Suggested order: soc-ops, panel, touchscreen, UFS, ath12k, QRTR/IPA, platform
and power, then the board DTS. An author must supply their own sign-off after
checking the Developer Certificate of Origin. Run `scripts/checkpatch.pl`
on each actual email patch, validate bindings with dtschema, and include the
focused W=1 builds and device evidence. A clean checkpatch result alone does
not establish that a driver or binding is ready for submission.

This repository task organizes and preserves the patch set. Sending patches to
maintainers or obtaining upstream acceptance is a separate external action.

The 2026-11-07 net-patch audit with `checkpatch.pl --no-tree --no-signoff --terse`
reported zero errors and 71 warnings across the 14 patches (59 in the board
patch, primarily line-length/style warnings). Sign-off checking was explicitly
disabled because these are source-reconstruction diffs, not authored email
patches. Bindings/MAINTAINERS review and the splits above are still required
before upstream submission.
