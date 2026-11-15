# CPAS Startup Ordering Candidate

Continue from `csid-reset`. A live phone trace shows successful stop and
bandwidth removal, followed by a second MM0 request timing out before the
CPAS start handshake. The candidate opens shared clocks and completes the
idle handshake before applying bandwidth. No sensor or DMA producer is
enabled until runtime resume succeeds. DTs without CPAS keep their sequence.

The common bandwidth helper preserves existing vote values. Its KUnit stub
checks live clock references and Q-channel control at the vote boundary,
over 20 cycles. Another test injects failure on the second path, checks the
first path is cleared and verifies reference cleanup and the original error.
All 38 QEMU/KASAN/mutex-debug tests and ARM64 CAMSS W=1 pass. These tests do
not model an ICC provider, in-flight BCM requests or hardware power domains.

Verify the parent manifest and patch hashes, apply to its result tree, and
compare `result_tree`. Use the existing [cpas-pm commands](../cpas-pm/README.md)
with unique package name `cam12order`. Image.gz and OV13B10 DTB in the built
package match the output tree. No standard recipe promotion has occurred.

This is not yet device-tested. The phone remains on cam11reset after its
traced restart fault; ordinary reboot timed out. Recover connectivity and
verify a fresh boot before installing the package. Never install while UFS
or ICC is stalled. Retain the new version after successful validation.

Known gaps: qcom_icc_set still discards BCM commit failures; ICC rollback
may resubmit old votes. Resume cleanup has no producer running, but a real
bandwidth timeout or failed cleanup handshake is not proven recoverable.
The startup sequence is a hypothesis supported by trace ordering, not an
established root-cause fix. See [the report](../../../../docs/相机CPAS启动顺序候选-20261115.md).
