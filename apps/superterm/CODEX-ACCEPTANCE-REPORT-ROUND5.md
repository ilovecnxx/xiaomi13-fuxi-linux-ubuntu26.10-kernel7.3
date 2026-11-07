我会按第五轮复审范围只读检查：先读取复审简报和仓库状态，再定位 orphan recovery、journal、启动调用及异步测试，最后给出带文件行号的结论，不修改文件。
简报确认本轮只需验证两类修复及回归。我已定位到实现和新增测试；接下来会逐段核对状态转换、journal 写入/回放、App 启动路径，以及测试是否真的让 `finish_turn` 持续失败并在同一事件循环完成。
实现层面已看到：控制器会对 `finish_turn` 重试 3 次，失败后记录预期终态；`recover_orphans()` 先回放 journal 再清扫残留；`App.__init__` 确实调用恢复。测试中新增的持续失败用例把 `finish_turn` 替换为每次抛错，并在同一 `asyncio.run(go())` 内启动与轮询。另发现一个旧的 `tests/unit/test_store.py` 仍采用跨两个事件循环的写法，我会把它作为是否影响本轮结论的重点核实项。
核查结果趋向“有条件通过”：两项本轮功能修复和新增持久性测试都与简报一致；但仓库仍保留 `tests/unit/test_store.py` 中跨两个 `asyncio.run` 的控制器测试（启动与等待分属不同 loop），所以“异步测试已全部改为同一事件循环”不能无条件成立。我还会做最后的状态/行号复核，并把本地测试执行受环境临时目录限制的事实单独说明。
总体结论：**有条件通过**。

A. 持续性 finalize 故障修复：

- `record_recovery()` 将预期终态追加到 `recovery.jsonl`，并尝试设为 `0600`：[store.py:243-265](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/sessions/store.py:243)。
- `recover_orphans()` 先回放 journal，将仍为 `running` 的 turn 恢复为精确状态；随后将残留 turn 标为 `interrupted`、process run 标为 `abandoned`、session 标为 `active`：[store.py:282-317](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/sessions/store.py:282)。
- 控制器对 `finish_turn` 重试 3 次，仍失败则记录预期状态，并始终清理 active turn：[controller.py:135-160](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/sessions/controller.py:135)。
- `App.__init__` 确实调用 `recover_orphans()`：[app.py:20-38](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/app.py:20)。
- 新测试确实持续替换 `finish_turn` 为始终抛错，验证 DB 暂为 `running`、journal 含 `completed`，再经恢复变为 `completed`：[test_acceptance_fixes.py:285-333](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_acceptance_fixes.py:285)。

该项修复成立。其保证依赖 journal 本身可写；代码未调用 `fsync`，底层存储整体不可写时仍无法保证 journal 落盘。

B. 异步测试生命周期：

- 新增 `run_turn_to_completion()` 在同一个 `asyncio.run()` 内完成启动和轮询：[test_acceptance_fixes.py:86-100](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_acceptance_fixes.py:86)。
- 相关控制器测试均使用该 helper，例如：[test_acceptance_fixes.py:138](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_acceptance_fixes.py:138)、[test_acceptance_fixes.py:314](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_acceptance_fixes.py:314)。

但仓库仍有一处未修复的跨事件循环测试：

- `test_store.py` 在第一次 `asyncio.run()` 中调用 `start_turn()`，随后用另一个 `asyncio.run()` 等待：[test_store.py:101-105](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_store.py:101)。这仍可能导致后台 task 随 loop 关闭而取消，测试生命周期不可靠。

因此，新增第五轮测试已正确改造，但全仓库异步测试尚未完全统一，不能判定为无条件通过。

我尝试运行完整测试；当前沙箱没有可用临时目录，测试因 `tempfile` 无法创建目录而出现环境错误，无法独立确认全套运行结果。未修改任何文件。
[TURN COMPLETE]