# SuperTerm 第九轮复审任务（只读）

第八轮结论「**有条件通过**」，仅剩一点：**直接调用 driver**（不经 controller）时 `timeout_s=0/负数`
仍可能无限等待——`BaseDriver.run` 与 `DshDriver.run` 只在正数时 `wait_for`。

已修复，请做**第九轮只读复审**核实。**只读，不要修改文件。**

## 本轮修复
1. **超时默认值集中到 `models`（消除 controller 与 driver 的循环依赖）**
   - `models.DEFAULT_TURN_TIMEOUT_S = 900.0`；`models.default_turn_timeout()` 在**调用时**读
     `SUPERTERM_TURN_TIMEOUT`（便于测试与运维覆盖）；
   - `models.effective_timeout(timeout_s)`：`None/0/负数/非法值` → `default_turn_timeout()`，正数原样返回。
     见 `src/superterm/models.py:166-186`。
2. **driver 侧防御性 clamp（关键修复）**
   - `BaseDriver.run`：改为 `eff_timeout = M.effective_timeout(spec.timeout_s)` 后无条件
     `asyncio.wait_for(self._proc.wait(), timeout=eff_timeout)`。见 `src/superterm/drivers/base.py:133-137`。
   - `DshDriver.run`：同样处理。见 `src/superterm/drivers/dsh.py:76-77`。
   - `Controller.start_turn` 改用 `M.effective_timeout(timeout_s)`，入口语义不变。
3. **新增直接调用测试**：`tests/unit/test_acceptance_fixes.py::TestDirectDriverTimeoutClamp`
   - `test_effective_timeout_normalization`：`None/0/-5` → 默认；`3` → 3。
   - `test_base_driver_direct_call_with_zero_timeout_is_bounded`：直接 `BaseDriver.run`，**不传/传 0/传 -1**
     三种情形均须在 20s 内结束且**恰好 1 个 done**（用 `sleep 60` 真实子进程）。
   - `test_dsh_driver_direct_call_with_zero_timeout_is_bounded`：`DshDriver.run` 传 0 亦须有界且 1 个 done。
   - 已反证有效性：按旧逻辑（仅正值才 `wait_for`），`timeout_s=0` 下 3s 仍在运行 → 测试会失败。
   - 测试通过 `SUPERTERM_TURN_TIMEOUT=0.8` 缩短默认值，setUp/tearDown 保证不泄漏。

## 当前证据
- 主机：`python3 tests/run_tests.py` → **Ran 66 tests, OK**（61 unit + 5 integration）。
- 真机：`tests/device/acceptance-evidence.txt` → **PASS=17 FAIL=0**，内嵌单测 61 项通过。
- 真机 codex 挂死：`--timeout 25` → `rc=0`，`1 × done`。

## 输出
给出总体结论（通过/有条件通过/不通过）+ 逐条核查（附文件:行）+ 仍存在的问题。只读。
