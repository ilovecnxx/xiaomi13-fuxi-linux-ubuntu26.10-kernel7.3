# Maintained Kernel

The authoritative source recipe is `manifest.json` plus `patches/`, in the
order recorded by the manifest. `series.json` assigns every changed path to
one subsystem. `history.json` retains the original commit authors and messages.
Historical dated patches under `work/patches/` are evidence, not additional
patches to apply to this recipe.

## Reconstruct and Build

Requirements: Python 3.12+, Git, make, GCC's `aarch64-linux-gnu` toolchain,
flex, bison, libssl-dev, libelf-dev, bc, rsync, cpio, gzip, zstd, xz-utils,
dpkg-dev and util-linux. Use the compiler version recorded in a build's
`build.json` when comparing binaries.

```bash
python3 scripts/fuxi-kernel-source.py prepare
# Offline, using the exact archive checksum in manifest.json:
python3 scripts/fuxi-kernel-source.py prepare --archive /path/linux-7.4-rc3.tar.gz
python3 scripts/fuxi-kernel-source.py check
scripts/fuxi-build-deb.sh <unique-name>
```

The source is reconstructed in `build/linux-standard/`, never over an existing
modified tree. Downloads and patches are checked with SHA-256, the imported
upstream Git tree is checked, and the complete patched tree must match the
manifest. A partial extraction is removed on failure. The source preparation
step does not use any local cache worktree or its uncommitted changes.

Build output is `work/kbuild/<unique-name>/`: the kernel deb, saved config,
`source`, `build.json`, `source-state.json`, `source.patch` and `SHA256SUMS`.
The build fixes its timestamp, user,
host and build counter. Bit-for-bit comparisons also require identical names,
configuration and toolchain; that is distinct from verified source content.
An explicit `FUXI_KTREE` / `FUXI_KOUT` selects an experimental source/build pair
and records its commit, actual worktree content tree and dirty state. The binary
patch includes tracked changes and nonignored new files without altering the real
Git index. Inputs ignored by Git need separate provenance. The builder rejects
collection if source differs at the end; do not edit that tree during a build.
Recovery instructions are in [kernel operations](../docs/内核维护与独立升级.md).

Reuse the active source tree, pinned container image and compatible `FUXI_KOUT`
for daily development. Unique package names do not require fresh output trees.
Do not rerun preparation, image construction or passing checks without changed
inputs. Clean builds remain useful for integration and benchmark comparisons.

`packaging/apt/kernels.list` is the allowlist for the local apt source. Add a
build only after device validation. `scripts/fuxi-build-pkgs.sh` requires those
kernel debs to exist locally and archives other experimental kernel packages
outside the source directory before rebuilding its index. Existing package
versions for the device/desktop/modem/firmware remain available for rollback.

## Configuration Policy

`arch/arm64/configs/fuxi_defconfig` is a generated savedefconfig. UFS, ext4,
display, input, networking and the current drivers remain built in. External
initramfs-tools supplies the initrd; the bootloader supplies command-line
arguments and the DTB. No ABL-specific built-in DTB or early console remains.

`SLUB_TINY` and `ATH12K_DEBUG` are disabled; stack protection uses the kernel's
strong default. `DEBUG_FS` remains enabled for the existing display and charging
diagnostics. `PANIC_ON_OOPS`, `panic=10` and boot confirmation remain part of
failure handling. Converting drivers to modules is a separate configuration
change, not a prerequisite for independent kernel upgrades. The build script
also builds modules if an experimental configuration enables them.

The normal board is `qcom/sm8550-xiaomi-fuxi.dtb`. Camera and sleep variants
include that board and add their own fragments; they are experimental. The
camera series is recorded separately in [`experiments/camera/`](experiments/camera/README.md),
including capture results and a blocking interconnect failure. Camera/voice
experiments are intentionally not folded into this baseline.

Experimental `build/linux-camera-*` worktrees are local build caches; manifests,
patches and result-tree hashes under `kernel/experiments/` are the source of
record. Keep the active worktree while it has uncommitted changes. A clean
historical worktree may be removed after its `HEAD^{tree}` matches the recipe's
`result_tree`; recreate it from that recipe before building again. Never remove
a dirty worktree as part of routine cleanup.

## Update the Recipe

At a validated milestone, commit the source changes and export against the same imported
upstream base (not a previous fuxi kernel):

```bash
python3 scripts/fuxi-kernel-source.py export --tree /path/source --base <base-commit>
```

Export rejects dirty source trees, overlapping groups and unassigned paths.
Review every patch and the manifest. For a changed recipe, replay into an empty
directory with `prepare --tree /path/verification --archive ...` and run checks
appropriate to its changes. Build and boot validation precede device promotion;
host-validated recipes may be committed with that limitation recorded.
Do not export/replay a recipe after every source edit. For a new upstream release,
update the archive URL/checksum/root and rebase the patches first; changing a
version string alone is not an upgrade.

Upstream submission planning is in `upstream.md`. These files are reproducible
source patches, not a claim of upstream acceptance or submission readiness.

## Bootloader Boundary

The deployed bootloader is uboot7. Its U-Boot source was the clean upstream
commit `feb8913982e4462c01fd914271f92254a25dc101`; the project environment is
`scripts/fuxi-uboot/fuxi.env`. `scripts/fuxi-build-uboot.sh` takes `FUXI_UBOOT`
for that checkout and now defaults to the maintained kernel build's board DTB,
instead of depending on a historical variant tree. It also requires the local
vendor bait DTB and mkbootimg tool under `build/edk2-msm/`.

Bootloader rebuilding/deployment is a separate operation. Standard kernel
updates do not rebuild or rewrite it. This maintenance pass does not redeploy
uboot7 or claim a newly built bootloader was device tested.
