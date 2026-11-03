# SuperTerm 第十轮复审任务（只读）

第九轮结论「**有条件通过**」，两点：
1. `effective_timeout()` 仍接受 `float("inf")`（`inf > 0` 成立），`wait_for(timeout=inf)` 无实际上限；
   环境变量配 `inf` 亦然 → "始终有界"不成立。
2. `DshDriver` 的直接调用测试只覆盖 `0`，未覆盖 `None` 与负数。

已修复，请做**第十轮只读复审**核实。**只读，不要修改文件。**

## 本轮修复
1. **所有输入都归一化为有限且有上限的超时**
   - 新增 `MAX_TURN_TIMEOUT_S = 7*24*3600`（硬上限）。
   - 新增 `_sanitize(v)`：非有限（`inf`/`-inf`/`nan`）、`<=0`、非数值 → `DEFAULT_TURN_TIMEOUT_S`；
     正有限值 `min(v, MAX_TURN_TIMEOUT_S)`。
   - `effective_timeout()`：`None` → 默认；其余走 `_sanitize`；因此返回值**必然有限且有界**。
   - `default_turn_timeout()` 亦经 `_sanitize`，所以 `SUPERTERM_TURN_TIMEOUT=inf` 也回落到 900。
     见 `src/superterm/models.py:166-200`。
   - 实测归一化：`None/0/-1/inf/-inf/nan/"abc"` → `900.0`；`3` → `3.0`；`1e12` → `604800.0`。
2. **测试补全**（`tests/unit/test_acceptance_fixes.py::TestDirectDriverTimeoutClamp`）
   - `test_effective_timeout_normalization`：新增 `inf`/`-inf`/`nan`/`"abc"` → 默认；
     `1e12` → `MAX_TURN_TIMEOUT_S`；并断言 `math.isfinite(effective_timeout(inf))`。
   - dsh 测试改名为 `test_dsh_driver_direct_call_is_bounded_for_bad_timeouts`，**循环覆盖 `0`、`None`、`-1`**
     三种输入，每种都断言 20s 内有界结束且**恰好 1 个 done**（用 `sleep 60` 真实子进程）。

## 当前证据
- 主机：`python3 tests/run_tests.py` → **Ran 66 tests, OK**（61 unit + 5 integration）。
- 真机：`tests/device/acceptance-evidence.txt` → **PASS=17 FAIL=0**，内嵌单测 61 项通过。
- 真机 codex 挂死：`--timeout 25` → `rc=0`，`1 × done`。

## 输出
给出总体结论（通过/有条件通过/不通过）+ 逐条核查（附文件:行）+ 仍存在的问题。只读。
