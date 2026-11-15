# CSID Gen3 Path Reset Candidate

Continue from the verified `csid-stop` result tree. Titan 780 uses CSID to
reset the IFE path: the vendor VFE reset is unsupported. Add an optional
acknowledged stop operation before disabling the CSID path configuration.
Gen3 writes immediate PATH reset (config 1, command 1), then waits for the
existing reset-complete IRQ. Startup keeps config 0x11 and command 5.
Other generations retain their existing operations.

The 36 QEMU/KASAN/mutex-debug tests include 20 common stream cycles, stop
error propagation, actual gen3 reset/ISR functions with simulated registers,
and missing-IRQ timeout despite a stale completion. This does not emulate
DMA draining or prove cleanup after a real hardware reset timeout. The
existing video stop error path still needs hardware fault validation.

Verify the parent manifest and patch SHA256, apply the patch to its result
tree, then compare with `result_tree` in this manifest. Reuse the KUnit
configuration, ARM64 W=1 target and build commands in
[cpas-pm](../cpas-pm/README.md), with package name `cam11reset` (or a fresh
name if already built). The standard recipe remains unchanged.

Vendor reference: MiCode camera-kernel, fuxi-t-oss commit
`6c6099a85cb3154fc8a744c17b6b9f5fd361dea8`, CSID ver2 stop/reset and Titan780
reset bit definitions. Mainline's IRQ-reset command is retained at startup;
stop follows the vendor hardware-only path reset command.

The package boots and a single 60-frame RGB capture gets a successful CPAS
stop, followed by suspended runtime PM and zero shared-clock references.
The initial immediate health check returned failure for an unrecorded reason;
its later read-only checks passed. A subsequent two-round client completed
only its first round, then stalled with ICC/RSC waiters affecting CPU, GPU,
touch and UFS. A later reboot retained cam11reset as running/default, with
deb9 as recovery. Repeated startup and the basic camera goal remain unaccepted.
See [the device report](../../../../docs/相机CSID路径复位-20261115.md).
