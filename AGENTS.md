# Repository Guidelines

## Project Structure & Module Organization

This repository combines the Xiaomi 13 (`fuxi`) Linux port and its development harness. `kernel/` pins the maintained source archive and subsystem patches; `build/linux-standard/` is reconstructed from that recipe. `build/linux/` and `scripts/legacy/` retain historical ABL/shim work. `scripts/` holds build, device, deployment, and diagnostic tools, with host-side unit tests in `scripts/tests/`. `work/rootfs-overlay/` contains Linux userspace configuration and services. `docs/` records design, test results, and handoffs; `work/` stores generated builds, logs, device state, and experiments. `tools/` contains supporting utilities. Check `README.md` and `TASKS.md` for current project status before starting device work.

## Build, Test, and Development Commands

- `python3 -m unittest discover -s scripts/tests -v` runs the host-side regression suite.
- `python3 scripts/fuxi-kernel-source.py prepare` reconstructs and verifies `build/linux-standard/` from the pinned archive and patches.
- `scripts/fuxi-build-deb.sh <name>` builds the independently installable kernel deb, config, provenance and checksums under `work/kbuild/<name>/`.
- `FUXI_DTB=<variant> scripts/fuxi-build-abl.sh <name>` is the historical ABL image build path; it is not the normal kernel update workflow.
- For a focused kernel check, use `make -C build/linux-standard O=<output> ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 <subsystem>/` with a configured output tree.
- `scripts/fuxi-health.sh` performs read-only checks against the connected Linux device.

Routine Android/Linux system switching may be performed automatically with the documented `adb reboot recovery`, Linux `systemctl reboot`, and test scripts when the task requires it. Image deployment through `scripts/fuxi-test-recovery.sh` reboots the phone and writes `recovery_b`; verify the target image and current recovery instructions in `README.md` before running it. Do not write Android or firmware partitions; follow the explicit safety boundaries in `TASKS.md`.

## Coding Style & Naming Conventions

Use four spaces for Python indentation, descriptive `snake_case` names, and standard-library modules where practical. Shell scripts should use Bash, quote expansions, and keep safety checks explicit. Match nearby code and preserve the scripts' existing executable bits. Name tests `test_*.py`; keep test classes descriptive and use `unittest`.

## Testing Guidelines

Run the full unittest command above after changing scripts or build logic. For kernel changes, run the focused `W=1` build and report the exact target. Device checks are hardware-dependent; record the image name, relevant configuration, and observed result in the appropriate `docs/` or `work/` log. Never treat a successful build as device validation.

## Commit & Pull Request Guidelines

Recent commits use a short subsystem prefix, a colon, and a concrete summary (for example, `语音：...` or `脚本名：...`). Commit each completed update locally in a focused commit; stage only files belonging to that change. The local `main` branch is authoritative, and GitHub should keep a single `main` branch synchronized with it. Push larger updates (kernel, feature, multi-file, or operational changes) after verification; check the remote state first and resolve unexpected divergence before syncing. Include test and device-check results in the commit or project notes. Do not commit generated images, logs, or sensitive device data unless explicitly required.
