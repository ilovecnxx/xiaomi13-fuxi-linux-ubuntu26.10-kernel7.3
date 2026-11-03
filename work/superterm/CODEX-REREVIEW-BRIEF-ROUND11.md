# SuperTerm 第十一轮复审任务（只读）

第十轮结论「**有条件通过**」，仅剩一点：
`_sanitize` 与 `effective_timeout` 中的 `float(v)` 未捕获 **`OverflowError`**——例如 `float(10**10000)`
会抛 `OverflowError`，因此"任何输入都返回有限超时"仍有缺口（`inf/nan/超大浮点/环境变量` 已正确）。

已修复，请做**第十一轮只读复审**核实。**只读，不要修改文件。**

## 本轮修复
1. 两处转换均改为 `except (TypeError, ValueError, OverflowError)`：
   `src/superterm/models.py:176`（`_sanitize`）与 `:198`（`effective_timeout`）。
2. 实测归一化（`effective_timeout`）：
   `10**10000` → `900.0`、`-(10**10000)` → `900.0`、`None/0/inf` → `900.0`、`3` → `3.0`、`1e12` → `604800.0`。
3. 测试补全：`test_effective_timeout_normalization` 新增 `10**10000` 与 `-(10**10000)` 两例，
   断言结果为**有限正数**（`math.isfinite(got) and got > 0`）。

## 当前证据
- 主机：`python3 tests/run_tests.py` → **Ran 66 tests, OK**（61 unit + 5 integration）。
- 真机：`tests/device/acceptance-evidence.txt` → **PASS=17 FAIL=0**，内嵌单测 61 项通过。
- 真机 codex 挂死：`--timeout 25` → `rc=0`，`1 × done`。
- 真机 GUI：真实 claude 回合渲染（`E2E_POLL assistantText=SHIPPED_OK`）。

## 输出
给出总体结论（通过/有条件通过/不通过）+ 逐条核查（附文件:行）+ 仍存在的问题。只读。
