# SuperTerm 第七轮复审任务（只读）

第六轮结论为「**通过**」。之后开发方在真机上做端到端健壮性验证时新发现并修复了一个**超时未被强制执行**
的问题（属健壮性增强，非第六轮范围内的回归）。请做**第七轮只读复审**，核实该修复。**只读，不要修改文件。**

## 背景：发现的真实缺陷
- `headless.cmd_run()` 接收 `--timeout` 参数但**从未传给 turn**，因此 `--timeout` 被静默忽略。
- 更严重：`AgentSpec.timeout_s` 默认 `None`，即**没有任何默认 per-turn 超时**。当 provider 流挂死时（真机上
  codex 连某些中转站长期 `Reconnecting…`），turn 会**永久挂起**，UI 一直显示 running。
- 真机复现：`--timeout 120` 下 turn 150s 仍未结束（被外层 timeout 杀掉）。

## 本轮修复（请核实）
1. **默认 per-turn 超时**：`controller.DEFAULT_TURN_TIMEOUT_S`（默认 900s，可用 `SUPERTERM_TURN_TIMEOUT` 覆盖）。
   `Controller.start_turn(..., timeout_s=...)` 把 `timeout_s or DEFAULT` 写入 `AgentSpec.timeout_s`。
   见 `src/superterm/sessions/controller.py:21-23`、`:99-102`。
2. **全链路打通**：`App.start_turn(..., timeout_s=)` → `Controller`；`headless.cmd_run` 真正传入 `--timeout`；
   桥 `turn_start` 支持可选 `timeout_s` 覆盖。见 `app.py`、`headless.py:95`、`bridge.py`（`m_turn_start`）。
3. **驱动侧已具备终止语义**（原有）：`base.py:132-140` 超时后 `_terminate()`（SIGTERM→SIGKILL），
   并仍由 base 发出**恰好一个** done（status=cancelled）。
4. **新增测试**：`tests/unit/test_acceptance_fixes.py::TestTurnTimeout`
   —— 用 `sleep 60` 的假驱动的真实子进程，`turn_timeout_s=0.8`，断言：turn 在超时内结束（<15s）、
   **恰好 1 个 done**、`status=cancelled`、turn 已 finalize、active 已清理。

## 真机验证（修复后）
```
$ superterm-headless run codex "reply OK" --workspace /tmp/cxws --timeout 25
rc=0
normalized events: 1 × done, 4 × error(Reconnecting…), 5 × lifecycle
final: {"session_id": "...", "done": true}
```
即：provider 流挂死时，turn 在超时内干净结束，只发出一个 done（此前会永久挂起）。

## 当前证据
- 主机：`python3 tests/run_tests.py` → **Ran 57 tests, OK**（52 unit + 5 integration）。
- 真机：`tests/device/acceptance-evidence.txt` → **PASS=17 FAIL=0**，内嵌单测 52 项通过。

## 输出
给出总体结论（通过/有条件通过/不通过）+ 逐条核查（附文件:行）+ 仍存在的问题。只读，不写文件。
