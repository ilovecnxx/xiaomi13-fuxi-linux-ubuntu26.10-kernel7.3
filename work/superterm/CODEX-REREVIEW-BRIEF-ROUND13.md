# SuperTerm 第十三轮复审任务（只读）

第十二轮结论「有条件通过」，唯一残留：
`_sanitize` 的 `OverflowError` 分支缺少**直接**测试证据（此前 `Boom()` 只进 `effective_timeout`；
一万位环境变量字符串解析为 `inf`，走的是非有限值分支而非 `OverflowError` 分支）。

已修复，请做**第十三轮只读复审**核实。**只读，不要修改文件。**

## 本轮修复
新增 `TestTimeoutEnvSanitization.test_sanitize_directly_covers_every_branch`，**直接调用 `M._sanitize(...)`**
逐一覆盖所有分支：
- `Boom()`（`__float__` 抛 `OverflowError`）→ `DEFAULT_TURN_TIMEOUT_S`  ← 直接命中 OverflowError 分支
- `10**10000`、`-(10**10000)`（`float()` 抛 `OverflowError`）→ 默认
- `"inf"`、`"nan"`、`"abc"`、`0`、`-5`、`None` → 默认
- `5` → `5.0`；`"7"` → `7.0`；`1e12` → `MAX_TURN_TIMEOUT_S`

证据：`float(10**10000)` 实测抛 `OverflowError: int too large to convert to float`；
若该分支未捕获，测试会以异常失败。

## 当前证据
- 主机：`TMPDIR=/tmp python3 tests/run_tests.py` → **Ran 70 tests, OK**（65 unit + 5 integration）。
- 真机：`tests/device/acceptance-evidence.txt` → **PASS=17 FAIL=0**，内嵌单测 65 项通过。
- 真机 codex 挂死：`--timeout 25` → `rc=0`，`1 × done`。
- 真机 GUI：`E2E_POLL assistantText=SHIPPED_OK`。
- `run_tests.py` 预检：无临时目录时输出环境诊断并 exit 3（已实测 `TMPDIR=/proc` 与注入 `PermissionError`）。

## 输出
给出总体结论（通过/有条件通过/不通过）+ 逐条核查（附文件:行）+ 仍存在的问题。
若残留仅为"复审容器 /tmp 不可写、无法跑全套"，请明确标注这是环境限制而非代码缺陷。只读。
