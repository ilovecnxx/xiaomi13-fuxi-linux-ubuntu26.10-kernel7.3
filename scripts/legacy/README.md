# Historical Boot Tools

The maintained boot path is U-Boot + extlinux. Build kernels with
`scripts/fuxi-build-deb.sh` and prepare sources with
`python3 scripts/fuxi-kernel-source.py prepare`.

`fuxi-build-shim.sh` and `fuxi-shim/init` are retained for emergency recovery
and historical reproduction. They require the old ABL-compatible kernel tree,
not `build/linux-standard`. The local `fuxi/legacy-shim` branch preserves that
tree; the already verified shim7 image remains in `work/kbuild/`.
Select an old compatible worktree explicitly with `FUXI_KTREE=/path/legacy`
when running the historical shim builder. Do not switch an active experiment
worktree or apply legacy patches to the maintained source tree.

Do not replace the working U-Boot recovery image during normal kernel updates.
Shim7 compatibility links are retained, but booting a current kernel through
shim7 after the initrd link fix has not been tested on the phone.
