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

Device result: cam12order booted and automatically confirmed after a clean
cam11reset reboot. A single 60-frame RGB capture passed with balanced CPAS
handshakes and zero shared clock references. A two-round client completed
only its first round: CPAS start succeeded before the second bandwidth
request, but MM0 still timed out and blocked GPU/UFS/CPU consumers. Startup
ordering alone is insufficient. Ordinary recovery reboot initially timed out;
a later check confirmed a new healthy boot, boot counters zero, Phosh active,
no failed services or D-state tasks, and no RPMh/filesystem errors in that boot.
Running/default remains cam12order, fallback deb9. No more captures were run.
This is not lifecycle acceptance. Never install while ICC/UFS is stalled.

Known gaps: qcom_icc_set still discards BCM commit failures; ICC rollback
may resubmit old votes. Resume cleanup has no producer running, but a real
bandwidth timeout or failed cleanup handshake is not proven recoverable.
The startup sequence is a hypothesis supported by trace ordering, not an
established root-cause fix. See [the report](../../../../docs/相机CPAS启动顺序候选-20261115.md).
