# Repository Guidelines

## Project Structure & Module Organization

This repository combines the Xiaomi 13 (`fuxi`) Linux port and its development harness. `kernel/` pins the maintained source archive and subsystem patches; `build/linux-standard/` is reconstructed from that recipe. `packaging/` owns installed device support, dependency recipes and the build environment; `apps/` owns application sources. `build/linux/`, `scripts/legacy/` and `work/rootfs-overlay/` retain historical work. `scripts/` holds build, device, deployment, and diagnostic tools, with host-side unit tests in `scripts/tests/`. `docs/` records design, test results, and handoffs; `work/` stores generated builds, logs, device state, and experiments. `tools/` contains supporting utilities. Check `README.md` and `TASKS.md` for current project status before starting device work.

## Build, Test, and Development Commands

- `python3 -m unittest discover -s scripts/tests -v` runs the host-side regression suite.
- `python3 scripts/fuxi-kernel-source.py prepare` reconstructs and verifies `build/linux-standard/` from the pinned archive and patches.
- `scripts/fuxi-build-deb.sh <name>` builds the independently installable kernel deb, config, provenance and checksums under `work/kbuild/<name>/`.
- `FUXI_DTB=<variant> scripts/fuxi-build-abl.sh <name>` is the historical ABL image build path; it is not the normal kernel update workflow.
- For a focused kernel check, use `make -C build/linux-standard O=<output> ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 <subsystem>/` with a configured output tree.
- `scripts/fuxi-health.sh` performs read-only checks against the connected Linux device.

Routine Android/Linux system switching may be performed automatically with the documented `adb reboot recovery`, Linux `systemctl reboot`, and test scripts when the task requires it. Image deployment through `scripts/fuxi-test-recovery.sh` reboots the phone and writes `recovery_b`; verify the target image and current recovery instructions in `README.md` before running it. Do not write Android or firmware partitions; follow the explicit safety boundaries in `TASKS.md`.

User direction (2026-11-15): continue from the latest version that passed the relevant validation. After successful device validation, retain that version as the running/default development baseline; do not routinely switch back to deb9, deb8, or another older version at the end of an experiment. Keep a verified older kernel installed as a recovery option. Roll back only for a concrete boot/runtime regression or an explicit user request, and record the reason. Distinguish host-tested source, device-tested scope, and the actual running kernel in TASKS.md; host tests alone do not establish device validation. Historical reports of restoring deb9 are past actions, not instructions for future work.

Latest camera scope (2026-11-15): finish the IMX800 main rear camera first. This supersedes the earlier OV13B10-first basic-camera goal. Defer ultrawide, front and telephoto feature work, lens switching, video and Leica integration. Retain existing shared CAMSS/CPAS/libcamera fixes and use them for the main-camera work. OV13B10 captures do not count as IMX800 acceptance. Read docs/基础相机目标.md and docs/主摄接通计划-20261115.md for the current milestones. Linux/SSH/desktop availability and camera lifecycle health must be reported separately; do not infer that the phone has not returned to Linux from old RPMh errors.

## Coding Style & Naming Conventions

Use four spaces for Python indentation, descriptive `snake_case` names, and standard-library modules where practical. Shell scripts should use Bash, quote expansions, and keep safety checks explicit. Match nearby code and preserve the scripts' existing executable bits. Name tests `test_*.py`; keep test classes descriptive and use `unittest`.

## Testing Guidelines

User direction (2026-11-16): use rapid iteration. This supersedes earlier per-stage process requirements in project plans and handoffs. The camera task remains paused until the user resumes it; changing this workflow does not resume device work.

- Work toward one observable IMX800 result per iteration: binding/identity, first RAW, clean stop, successful reopen, RGB, then preview and JPEG. Combine related changes in the existing candidate worktree. Preserve the latest passing baseline and shared fixes.
- Briefly state the question, expected observation and stop condition, implement, run relevant checks, then obtain device evidence when ready. Unknown hardware parameters may be explicit hypotheses in a bounded experiment; do not require a complete theoretical explanation before first capture. Fix known correctness defects before deployment. After a hardware stall, stop additional captures; repeat only with a changed hypothesis or implementation and a recovered device.
- During development, run affected script tests, focused kernel `W=1`, changed binding/DTB checks, and relevant lifecycle tests as applicable. Run the full script suite when shared script/build behavior changes or before deploying a candidate that changes those paths. Documentation-only changes need diff/link review, not builds. Do not repeat passing checks without a relevant change or unresolved concern.
- Keep one active camera candidate. Do not add a worktree, manifest layer or standalone report for every small edit. Save the source diff/commit, config and artifact provenance before deployment; consolidate a reproducible recipe at meaningful validated milestones. Full ancestry replay is for recipe changes and consolidation, not every local source edit.
- TASKS.md is the current-state entry: record candidate, result, next action and any blocker once. Keep raw logs in work/. Update the camera plan only when the plan changes; historical reports remain evidence. Signing, redistribution and upstream submission gates apply at their respective publication stages, not to local camera experiments.
- Final stability acceptance remains required, but its loop counts and durations do not gate early experiments. Never treat a successful build or an OV13B10 capture as IMX800 device validation. Retain partition/data safety boundaries and a bootable recovery option; do not routinely revert a passing version.

Device checks are hardware-dependent; record the image name, relevant configuration and observed result in TASKS.md with links to local evidence when needed.

## Commit & Pull Request Guidelines

Recent commits use a short subsystem prefix, a colon, and a concrete summary (for example, `语音：...` or `脚本名：...`). Commit each completed update locally in a focused commit; stage only files belonging to that change. The local `main` branch is authoritative, and GitHub should keep a single `main` branch synchronized with it. Push larger updates (kernel, feature, multi-file, or operational changes) after verification; check the remote state first and resolve unexpected divergence before syncing. Include test and device-check results in the commit or project notes. Do not commit generated images, logs, or sensitive device data unless explicitly required.
