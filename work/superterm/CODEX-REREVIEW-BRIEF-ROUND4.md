# SuperTerm 第四轮复审任务（只读）

第三轮结论为「**有条件通过**」，指定残余问题：

> `done_persisted` 只在 `_emit` 完整返回后设置，但 Store 在 SQLite 提交后才写 JSONL。
> 若数据库提交成功、JSONL 写入失败，`append_event` 会抛错，守卫仍为 false；异常路径可能再写一个 `done`。
> 另：`finish_turn` 异常被捕获忽略，保证的是"尝试 finalize"而非存储故障下必定成功。

开发方已修复，请做**第四轮只读复审**核查。**只读，不要修改文件。**

## 本轮修复（请核实）

1. **JSONL 镜像是派生产物，失败不得影响事件写入**
   - `Store._write_jsonl` 现在整体 try/except 吸收异常；SQLite 行为权威记录，JSONL 仅人类可读镜像。
     因此「提交成功 + JSONL 失败」不再抛错，`done_persisted` 正常置位。见 `src/superterm/sessions/store.py:181-198`。
   - 语义说明：SQLite 是权威（UI 用 cursor 从它重同步），JSONL 失败仅丢失一份可读镜像，不影响一致性。

2. **finalize 加重试**
   - `run_wrapper` 的 `finally` 对 `finish_turn` 与 `update_session` 各重试一次（应对 SQLite busy/locked 瞬时错误），
     active-turn 清理仍在 `finally` 内。见 `src/superterm/sessions/controller.py:135-160`。

3. **新增回归测试（均已通过）**
   - `test_jsonl_mirror_failure_does_not_duplicate_done`：把 `_session_log_path` 打成抛 `OSError("disk full")`，
     断言仍恰好 1 个 done 且 turn 已 finalize。
   - `test_sqlite_failure_still_finalizes_turn`：让第一次 `append_event` 抛 `RuntimeError("sqlite locked")`，
     断言 active 已清理、turn 状态不为 `running`、session 回到 `active`。
   - 已用旧行为反证：若 JSONL 失败仍向上抛，done 数为 2，该测试会失败（证明测试非空通过）。

## 当前证据
- 主机：`python3 tests/run_tests.py` → **Ran 52 tests, OK**（47 unit + 5 integration）。
- 真机：`tests/device/acceptance-evidence.txt` → **PASS=16 FAIL=0，内嵌单测 47 项通过**。
- 真机 GUI 自检：`SELFTEST_RESULT={"ok":true,...}`。

## 输出
给出总体结论（通过/有条件通过/不通过）+ 逐条核查（附文件:行）+ 仍存在的问题。只读。
