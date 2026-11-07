我会按第四轮简报逐项核对实现与测试，并检查相关调用路径是否覆盖了第三轮残余问题。全程只读，不会改动文件。
简报把重点限定在两个故障边界：JSONL 镜像异常是否会越过 SQLite 成功写入，以及 finalize 重试是否真正保证状态收尾。我正在对照 Store、controller 和新增回归测试的实际代码；随后会跑目标测试及完整测试集，判断简报里的证据是否能复现。
实现上，Store 对 JSONL 异常的吞并确实闭合了“SQLite 已提交、镜像失败、done 守卫未置位”的重复路径。Finalize 则是两次立即尝试后吞掉最终异常；新增的 SQLite 测试只让 `append_event` 第一次抛错，实际 `finish_turn` 和 `update_session` 都在正常数据库上成功，因此没有证明持久存储故障时“必定 finalize”。我再核对测试执行证据和现有生命周期测试的运行方式。
**总体结论：不通过。** JSONL 镜像失败导致重复 `done` 的路径已修复；但 finalize 仍只是尽力重试，第二次失败会被吞掉，不能保证 turn 和 session 状态落库。新增测试也不足以证明 finalize 故障场景已解决。

- **JSONL 镜像失败：实现修复成立。** [store.py](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/sessions/store.py:172) 先提交 SQLite，再调用镜像写入；[_write_jsonl](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/sessions/store.py:189) 捕获镜像写入中的异常并返回。因此镜像失败不会使 `append_event` 抛错，[controller.py](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/controller.py:108) 的 `done_persisted` 可以正常置位。引用路径中的 controller 实际位于 `src/superterm/sessions/controller.py`。

- **Finalize 保证：残余问题仍在。** [controller.py](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/sessions/controller.py:139) 对 `finish_turn` 和 `update_session` 各尝试两次，但第二次失败仍被忽略（第 143–152 行），随后清理 active 状态（第 153–154 行）。如果数据库故障持续存在，turn 仍可能停在 `running`，session 也可能保持 `running`；清理内存中的 active 标记不等于持久化 finalize 成功。

- **新增测试：断言非空，但无法证明所声称的保证。** [test_acceptance_fixes.py](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_acceptance_fixes.py:132) 的镜像失败测试检查了 done 数量和 turn 状态，针对镜像故障的断言有意义。SQLite 测试只让 `append_event` 的第一次调用抛异常（第 182–190 行），`finish_turn` 与 `update_session` 并未遭遇数据库故障，因此它验证的是事件持久化调用失败后、数据库可正常写入时的收尾。

- **测试运行与回归：本轮无法独立确认。** 新增测试通过 `asyncio.run(start_turn(...))` 启动后台任务，之后又在另一次 `asyncio.run` 中轮询（第 153–157、194–198 行）；任务和轮询不在同一个事件循环内，测试生命周期不可靠。本轮尝试运行 47 个 unit tests，因只读环境无法创建 `tempfile` 临时目录，31 项报错，不能视为代码回归，也不能作为通过证据。简报中的主机和真机结果属于已有记录；设备证据只列出总通过数，未提供新增用例的逐项结果。

仍存在的问题：持续性 SQLite 写入故障下没有成功 finalize 的保证；SQLite 回归测试没有模拟 finalize 写入失败；新增异步生命周期测试需要在同一事件循环内等待后台任务。本轮无法完整验证是否无回归。
[TURN COMPLETE]