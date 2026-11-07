# SuperTerm 第五轮复审任务（只读）

第四轮结论为「不通过」，两点：
1. **finalize 仍只是尽力重试**：二次失败被吞掉，持续性 SQLite 写入故障下 turn 可能永久停在 `running`；
   且 SQLite 回归测试没模拟 finalize 写入失败。
2. **新增异步测试生命周期不可靠**：`asyncio.run(start_turn())` 创建的后台 task 与随后另一次
   `asyncio.run` 里的轮询不在同一事件循环。

开发方已修复，请做**第五轮只读复审**核查。**只读，不要修改文件。**

## 本轮修复（请核实）

### A. finalize 持久性：journal + 开机对账（不再有"永久 running"）
- `Store.record_recovery(turn_id, session_id, status, exit_code)`：把**预期最终状态**追加写入
  `state_dir()/recovery.jsonl`（best-effort，0600）。
- `Store.recover_orphans()` 重写：
  1. 先**回放 journal**，把仍在 `running` 的对应 turn 恢复为该 turn 的**精确预期状态**（completed/error）；
  2. 再清理仍残留的 `running` turn → `interrupted`；
  3. `running` 的 `process_runs` → `abandoned`；`running` 的 session → `active`；
  4. 全程 best-effort、幂等，可每次启动调用。
- `App.__init__` 现在**实际调用** `recover_orphans()`（此前该方法从未被调用，附件问题也因此放大）。
- 控制器 `finally`：`finish_turn` 重试 3 次；仍失败则 `record_recovery(...)` 记下预期状态，
  下次启动必定对账。见 `src/superterm/sessions/controller.py:135-166`、`src/superterm/sessions/store.py:242-320`、`src/superterm/app.py:30-38`。

> 语义说明：当底层存储持续不可写时，任何程序都无法当场把状态落库；这里保证的是
> **"失败被持久记录 + 下次启动必定收敛到正确终态"**（与 WAL/crash-recovery 同类语义），
> 而不是"存储故障时同步写入成功"。

### B. 异步测试生命周期（同一事件循环）
- 新增模块级 `run_turn_to_completion(ctrl, sid)`：**在同一个 `asyncio.run` 内** `start_turn` 并轮询
  直到 turn 结束；四个控制器测试全部改用它。见 `tests/unit/test_acceptance_fixes.py:24-40`。
- 已用旧行为反证测试有效：把 `_emit` 的 push 异常改回不吞（旧实现），done 数为 **2**，测试会失败。

### C. 针对 finalize 故障的新测试（直接命中第四轮诉求）
- `test_persistent_finalize_failure_is_journaled_then_healed`：让 `finish_turn` **持续抛错**跑完一个 turn，
  断言 (a) DB 中 turn 暂为 `running`；(b) `recovery.jsonl` 已写入且含预期状态 `completed`；
  (c) 调 `recover_orphans()` 后 turn 变为 `completed`、session 回到 `active`。
- `test_journal_replay_restores_intended_status`、`test_leftover_running_turn_is_swept`、
  `test_app_boot_reconciles_stuck_turn`（验证 `App()` 启动即对账）。

## 当前证据
- 主机：`python3 tests/run_tests.py` → **Ran 56 tests, OK**（51 unit + 5 integration）。
- 真机：`tests/device/acceptance-evidence.txt` → **PASS=17 FAIL=0**，内嵌单测 51 项通过，
  含第 7 节落盘泄漏逐字节扫描、第 8 节不变量、第 9 节 finalize 持久性审计（journal→对账）。
- 真机 GUI：真实 claude 回合渲染进气泡（`E2E_POLL assistantText=ROUND4_GUI_OK`）。

## 输出
给出总体结论（通过/有条件通过/不通过）+ 逐条核查（附文件:行）+ 仍存在的问题。只读，不写文件。
