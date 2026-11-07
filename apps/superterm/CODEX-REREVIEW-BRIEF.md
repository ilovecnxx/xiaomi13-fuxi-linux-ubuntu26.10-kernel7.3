# SuperTerm 复审任务（只读，第二轮）

你此前给出的验收结论为「不通过」，并列出 4 项阻断级问题。开发方已针对性修复，请做**第二轮只读复审**，
逐条核对下列修复是否真正落实，并判断是否仍存在阻断级问题。**只读，不要修改任何文件。**

## 上一轮阻断项 → 本轮修复（请核查实现与测试）
1. **自定义 token 可能经 `turns.prompt`/raw 日志/事件落盘明文**
   - 修复：`Store` 增加 `_redactor`，`set_redactor()`；`create_turn` 对 prompt 调用 `_scrub`；
     `append_event` 落盘前再 `redact_obj`；`raw_sink` 写行前 `_scrub`。见 `src/superterm/sessions/store.py`。
   - `App.refresh_redactor()` + 桥 `config_save_profile`/`config_import_cc_switch` 后刷新脱敏器。见 `app.py`、`bridge.py`。
   - `Redactor.set_secrets/add_secret` 见 `src/superterm/redaction.py`。
   - 测试：`tests/unit/test_acceptance_fixes.py::TestRedactionAtPersistence`（prompt/event/raw 三处均验证 `customsecrettokenZZZ123456` 不落盘）。
2. **启动失败/异常路径不能保证恰好一个 done**
   - 修复：`drivers/base.py` 启动失败分支显式发出唯一 done 并置 `_final_done_sent=True`；
     控制器 driver 异常分支补发 `ev_done("error")`。见 `base.py`、`sessions/controller.py`。
   - 测试：`test_acceptance_fixes.py::TestDoneOnStartupFailure`（缺失可执行文件 → 恰好 1 个 done 且 status=error）。
3. **bridge 服务器 push 缺 `protocol_version`**
   - 修复：`controller._emit` push envelope 增加 `protocol_version`。见 `sessions/controller.py`。
   - 测试：`TestPushEnvelope`。
4. **workspace_list / session_create 可指定任意根目录**
   - 修复：桥新增 `_allowed_ws_roots()`（home + 系统临时目录）与 `_ws_ok()`；`workspace_list`、`session_create`
     均 resolve 真实路径并要求落在允许根内，否则 `forbidden`。见 `bridge.py`。
   - 测试：`test_bridge.py::test_workspace_list_outside_roots_forbidden`、`test_session_create_outside_roots_forbidden`。

## 顺带修复（上一轮非阻断项）
- Codex TOML merge 不再删除无关表中的 `model`/`model_provider`（仅清理首个 `[table]` 之前的顶层标量）。见 `config/writers.py`。
- 备份文件名加入微秒，避免同秒覆盖。见 `config/writers.py`。
- 测试：`TestCodexTomlMerge.test_preserves_unrelated_table_keys`。

## 当前证据
- 主机单测 `tests/unit` 43 项 + 集成 5 项全过。
- 真机 `tests/device/acceptance-evidence.txt`：14/14 PASS，内嵌单测 43 项通过。
- 真机 GUI 自检 `SELFTEST_RESULT={"ok":true,...}`（GTK4+WebKit6 渲染 + 桥往返）。

## 输出
在回答里直接给出：总体结论（通过/有条件通过/不通过）+ 逐条核对结果（附文件:行依据）+ 仍存在的阻断或问题。
只读，不写文件。
EOF
echo "re-review brief written"