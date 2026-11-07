# SuperTerm 第三轮复审任务（只读）

第二轮结论为「不通过」，理由：
- 第 1、3、4 项已修复；
- **第 2 项仍有阻断**：done 恰好一次在「push 在 done 上抛错」时可能重复；且异常分支自身 push 抛错会跳过
  `finish_turn` 与 active-turn 清理；
- 另指出 `test_event_payload_redacted` **测试无效**（event 无 session_id → 查询为空 → `all([])` 空通过）；
- 并说明只读沙箱无临时目录、且未设 `PYTHONPATH=src` 导致无法独立跑测试。

开发方已修复以上全部问题，请做**第三轮只读复审**，核查修复是否落实、是否引入回归。**只读，不要修改文件。**

## 本轮修复（请逐条核对源码与测试）

1. **done 恰好一次（阻断项）**
   - `controller._emit`：push 放到 try/except 中吞掉异常（事件已持久化，UI 可用 cursor 重同步），
     因此 push 失败不会再被误判为 driver 崩溃、也不会触发第二个 done。见 `sessions/controller.py:40-58`。
   - `run_wrapper`：新增 `done_persisted` 守卫——driver 已落盘 done 后再抛异常，不再补发；
     未落盘才补发一次 error done。见 `sessions/controller.py:96-146`。
   - `finish_turn`/`update_session`/active-turn 清理移入 `finally`，即使 emitter 失败也必定执行。
   - 新增测试：`tests/unit/test_acceptance_fixes.py::TestExactlyOnceUnderFailure`
     - `test_driver_emits_done_then_raises_no_duplicate`（done 后抛异常 → 仍恰好 1 个 done）
     - `test_push_failure_does_not_duplicate_done_or_skip_finalize`（push 全程抛错 → 1 个 done、turn 已 finalize、active 已清理）

2. **失效的脱敏测试（有效性问题）**
   - `test_event_payload_redacted` 现在设置 `ev.session_id = s.id`、`ev.turn_id`，并先 `assertTrue(got)` 再断言不含密钥。
     见 `tests/unit/test_acceptance_fixes.py:37-46`。

3. **验收脚本缺少安全/协议审计（第一轮第 7 点）**
   - `tests/device/acceptance.sh` 新增第 7、8 节：
     - **第 7 节 落盘泄漏审计**：用含自定义密钥的 prompt 跑一次真实 turn（fake driver 把密钥写进 raw
       stream 与 assistant 文本），`wal_checkpoint(TRUNCATE)` 后**逐字节扫描**状态目录下所有文件
       （SQLite/WAL/events.jsonl/raw-*.jsonl），任一文件含明文密钥即 FAIL。
     - **第 8 节 不变量审计**：push 全程抛错场景下 done 恰好 1 个 + turn 已 finalize + active 已清理；
       push envelope 含 `protocol_version`；`/etc`、`/root`、`/` 的 `workspace_list` 与 `session_create` 均返回 `forbidden`。
   - 真机结果：`tests/device/acceptance-evidence.txt` → **PASS=16 FAIL=0, ACCEPTANCE_OK**（含新增两节）。

4. **测试可执行性**
   - 新增 `tests/run_tests.py`：自动把 `src` 加入 `sys.path`，无需 `PYTHONPATH` 即可
     `python3 tests/run_tests.py` 跑全部单测+集成测试（当前 **50 项全过**）。
   - `pyproject.toml` 增加 `[tool.pytest.ini_options] pythonpath = ["src"]`。
   - 注：只读沙箱若无可写临时目录，测试无法运行属环境限制；可在可写环境执行 `python3 tests/run_tests.py`。

## 当前证据
- 主机：`python3 tests/run_tests.py` → **Ran 50 tests, OK**（45 unit + 5 integration）。
- 真机：`acceptance-evidence.txt` → 16/16 PASS，内嵌单测 45 项通过。
- 真机 GUI：`SELFTEST_RESULT={"ok":true,...}`（GTK4+WebKit6 渲染 + JS↔Python 桥往返）。

## 输出
直接在回答里给出：总体结论（通过/有条件通过/不通过）+ 逐条核查（附文件:行）+ 仍存在的问题或阻断项。
只读，不要写任何文件。
