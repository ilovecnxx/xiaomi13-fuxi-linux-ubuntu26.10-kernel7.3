# SuperTerm Codex 只读验收 · 第三轮报告

> 验收方：主机 codex-cli（`--sandbox read-only`）。日期：2026-10-30。
> 针对第二轮两项缺陷的修复进行复审。结论：**有条件通过**。

**总体结论：有条件通过。** 第二轮指出的两项核心缺陷已在目标场景中修复，脱敏测试也不再空通过。
仍有一个更窄的持久化异常边界会绕过 `done_persisted` 守卫；此外本环境无可写临时目录，无法复现说明中的测试结果。

1. **`done` 恰好一次：目标场景已修复。**
   - `controller.py:48`：事件先持久化再推送，push 异常被捕获，不会被误当 driver 异常。
   - `controller.py:104`：emit 成功返回后才置 `done_persisted`；driver 发并持久化 done 后再抛异常时只补发 error，不再补发第二个 done。
   - `controller.py:135` 的 `finally` 会尝试结束 turn、更新 session、清理 active turn。
   - 测试覆盖 driver 发 done 后抛错、push 持续失败两种情形：`test_acceptance_fixes.py:103`、`:132`。
   - **残余边界**：`done_persisted` 只在 `_emit` 完整返回后设置，而 Store 在 SQLite 提交后才写 JSONL（`store.py:172`）。
     若数据库提交成功、JSONL 写入失败，`append_event` 抛错，守卫仍为 false，异常路径可能再写一个 done。
     不影响本轮两条目标路径，但"任何持久化部分失败时都恰好一次"尚未成立。

2. **事件脱敏测试：修复有效。** 测试已设置 `session_id`/`turn_id`，查询后先断言非空再检查不含密钥，不再空通过：`test_acceptance_fixes.py:37`。

3. **验收脚本新增审计：覆盖到位，但无法在本环境复跑。**
   第 7 节用自定义密钥经 prompt/raw/assistant 事件后逐字节扫描状态目录；第 8 节检查 push 失败下 done 数量、
   turn/active 状态、协议版本、越界 workspace 拒绝：`acceptance.sh:72`、`:135`。证据记录 `PASS=16 FAIL=0`：`acceptance-evidence.txt:23`。

4. **测试可执行性与回归检查：** `tests/run_tests.py` 把 `src` 加入 `sys.path`，`pyproject.toml` 增加 pytest `pythonpath`：`run_tests.py:13`、`pyproject.toml:22`。
   实际运行发现 50 项，其中 29 项因无可写临时目录报错（环境限制，非代码回归），无法独立确认"50 项全过"。

**仍存在的问题：** 部分持久化失败可能造成重复 `done`；`finish_turn` 异常被捕获忽略，保证的是"尝试 finalize"而非存储故障下必定成功写入。
