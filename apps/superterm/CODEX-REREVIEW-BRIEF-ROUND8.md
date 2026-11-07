# SuperTerm 第八轮复审任务（只读）

第七轮结论「**有条件通过**」，两点剩余：
1. 显式 `timeout_s=0` 会关闭驱动超时（`if spec.timeout_s:` 对 0 为假）；
2. 新增测试未覆盖**默认值**与**各入口传参**（headless / App / bridge）。

已修复，请做**第八轮只读复审**核实。**只读，不要修改文件。**

## 本轮修复
1. **0/负数不得关闭超时**
   - `Controller.start_turn`：仅当 `timeout_s is not None and timeout_s > 0` 才采用，否则回落到
     `DEFAULT_TURN_TIMEOUT_S`。见 `src/superterm/sessions/controller.py:99-104`。
   - `BaseDriver.run` 防御性改为 `if spec.timeout_s and spec.timeout_s > 0:`。见 `src/superterm/drivers/base.py:133`。
2. **补默认值与入口传参测试**：`tests/unit/test_acceptance_fixes.py::TestTimeoutPlumbing`
   - `test_default_timeout_applied`：不传超时 → `AgentSpec.timeout_s == DEFAULT_TURN_TIMEOUT_S`；
   - `test_zero_and_negative_cannot_disable_timeout`：`0` 与 `-1` 均回落到默认；
   - `test_app_start_turn_forwards_timeout`：`App.start_turn(..., timeout_s=7)` → spec 为 7；
   - `test_bridge_turn_start_forwards_timeout`：桥 `turn_start{timeout_s:9}` → spec 为 9；
   - `test_headless_run_forwards_timeout`：`headless.cmd_run(..., timeout=11.0)` → spec 为 11。
   实现方式：以 fake driver 捕获实际生成的 `AgentSpec`，逐入口断言，非空通过。

## 当前证据
- 主机：`python3 tests/run_tests.py` → **Ran 62 tests, OK**（57 unit + 5 integration）。
- 真机：`tests/device/acceptance-evidence.txt` → **PASS=17 FAIL=0**，内嵌单测 57 项通过。
- 真机 codex 挂死场景：`--timeout 25` → `rc=0`，`1 × done`，不再永久挂起。

## 输出
给出总体结论（通过/有条件通过/不通过）+ 逐条核查（附文件:行）+ 仍存在的问题。只读。
