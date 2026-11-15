# CSID Stop Candidate

This incremental candidate builds on `cpas-pm` and retains its shared-clock
ownership, handshake and failure containment. The VC dirty flag used to
suppress stream-off after stream-on had cleared it. Always configure a stop
and mark the VC configuration dirty for the next start.

A KUnit regression calls the production `csid_set_stream()` for 20 cycles
with a counted hardware callback. The parent source fails: one start, zero
stops. The candidate passes all 32 CAMSS tests with KASAN and mutex debugging,
and the ARM64 CAMSS W=1 build. The test does not model actual CSID registers,
frame-boundary stopping or DMA drain. It covers the single streaming path
needed for the basic OV13B10 goal; independent concurrent VC lifecycle is
not accepted by this experiment.

Apply the exported patch to the verified `cpas-pm` result tree and compare
with `manifest.json`. Use its parent's KUnit config and commands. The phone
package is `cam10stop`, using the same camera config as cam9pm. The standard
kernel recipe is unchanged. Device results are recorded in
[the report](../../../../docs/相机CSID停流修复-20261115.md).

Device result: cam10stop boots and completes 60 RGB frames, but CPAS stop
still times out at status 0x5. Runtime PM latches error; further captures
are stopped. Fixing the skipped CSID callback alone does not resolve the
hardware quiescence failure. Running/default remains cam10stop, with deb9
available for recovery.
