# SuperTerm Codex 只读验收 · 第二轮报告

> 验收方：主机 codex-cli（`--sandbox read-only`）。日期：2026-10-30。
> 针对第一轮 4 项阻断项的修复进行复审。结论：**不通过**。

**总体结论：不通过。** 第 1、3、4 项的实现已落实；第 2 项仍不能保证"恰好一个 done"，因此还有阻断项。

1. **自定义 token 落盘脱敏：实现已修复，事件测试无效。**
   - Store 在写入 turn、事件和 raw 行前调用脱敏；App 启动时加载已有 token，并在桥保存或导入配置后刷新脱敏器：
     `store.py:97`、`app.py:24`、`bridge.py:156`。prompt 和 raw 测试会检查实际落盘内容。
   - 但事件测试创建的 event 没有 `session_id`（事件工厂默认空），测试随后按新建 session ID 查询，
     结果为空，`all([])` 断言会通过，**未验证事件落盘脱敏**：`test_acceptance_fixes.py:37`、`models.py:96`。

2. **done 恰好一次：仍有阻断。**
   - BaseDriver 启动失败分支现在显式发送一次 error done，控制器异常分支也会补发 done：`base.py:92`、`controller.py:107`。
   - 但控制器没有检查异常发生前是否已持久化 done：`_emit` 先写事件再 push；若 push 在 done 上抛错，
     异常会进入 driver 崩溃分支并再次发 done，**可能重复终止事件**。若异常分支自身 push 也抛错，
     `finish_turn` 与 active-turn 清理会被跳过（`controller.py:111-114`）。
   - 现有启动失败测试只覆盖缺失可执行文件，未覆盖"done 已落盘后 push 失败"或 driver 发 done 后再抛异常：
     `test_acceptance_fixes.py:59`。

3. **push envelope 协议版本：已修复。** 控制器 push 含 `protocol_version`：`controller.py:50`；测试覆盖：`test_acceptance_fixes.py:82`。

4. **workspace 根目录限制：已修复。** `session_create` 与 `workspace_list` 均解析真实路径并限制在 home 或系统临时目录：
   `bridge.py:97`、`bridge.py:111`、`bridge.py:207`。未单独覆盖指向允许根外的符号链接，但 `resolve(strict=True)` 会先解析。

顺带修复：Codex TOML 只在首个表之前清理顶层键、备份名以微秒生成，与 brief 一致：`writers.py:44`、`writers.py:92`。

未独立复现测试结果：只读环境无可写临时目录，且 `test_acceptance_fixes` 因未设 `PYTHONPATH=src` 无法导入项目模块。

**仍存在的问题：** 部分持久化失败可能造成重复 done；事件脱敏测试空通过；测试可执行性差。
