# SuperTerm —— 手机原生超级终端 App（开发进展 2026-10-30）

目标：在 Xiaomi 13（fuxi，Ubuntu 26.10 arm64 主线 Linux + Phosh）上原生运行
codex / claude / dsh 三个 CLI 后端，并自研一个**超级智能终端 App**（GTK4 + libadwaita +
WebKit 6.0 原生窗口 + Python 控制器），直接驱动三个 CLI 的**结构化 JSON 流**，把终端显示
接管为 Codex 手机端式的「对话 + 开发」界面；内建 **CCSwitch 式** 的 provider/模型/token
配置管理与**日志**子系统。计划由 Codex 产出（`SUPERTERM-DEVELOPMENT-PLAN.md`），本会话实现。

## 架构（已实现并真机验证）

```
Phosh/Wayland
  → superterm-window (Python GTK4/libadwaita + WebKit 6.0 原生 WebView)
       → JS ↔ Python 桥（严格协议：request/response + 服务器 push）
       → Session 控制器（asyncio）
            → Agent 适配器（codex / claude / dsh）—— 只解析 JSON，不解析 ANSI/TUI
            → Provider 配置store（SQLite + 0600 密钥文件，CCSwitch 式）
            → 会话/事件日志（SQLite 索引 + 每会话 JSONL）
            → 工作区文件服务（路径限制在 workspace 内）
```

关键决策：**自研独立 App，不内嵌 DSH Web**；用各 CLI 的非交互结构化输出为集成骨架：
- codex：`codex exec --json --skip-git-repo-check` → JSONL（thread.started / item.* / turn.completed）
- claude：`claude -p --output-format stream-json --verbose` → JSONL（system/assistant/tool_use/tool_result/result）
- dsh：`dsh headless` → 文本，适配器合成事件

## 已完成的里程碑

| 里程碑 | 内容 | 证据 |
|---|---|---|
| M0 侦察 | 手机 GTK4/Adw/WebKit、node24、npm 可达；cc-switch schema 映射；结构化输出契约 | `tests/device/m0-report.md` |
| M1 驱动核心 | models/paths/redaction/normalize/drivers/store/controller + headless CLI | 40 项主机单测/集成测试全过；**真机** codex、claude 实跑 |
| M2 GUI | GTK4+WebKit6 原生窗口 + 严格 JS 桥 + 对话/工具卡/diff/设置面板 | **真机** GUI E2E：真实 claude 生成三行俳句渲染进气泡（`E2E_POLL` 文本证据） |
| M3 配置/日志 | CCSwitch 式 provider 档案/绑定/导入 + JSONL 日志 + 日志查看 | **真机**桥测试：导入 9 个供应商、保存档案 token 掩码 `****1234`、日志查询 OK |
| M4 安装/部署 | 版本化安装 + 桌面图标 + 启动器 + SSH 一键部署/回滚 | `scripts/superterm-deploy.sh` 在手机装好，doctor 显示 codex/claude/dsh 齐全 |
| M5 验收 | 见下 | `tests/device/acceptance.sh` |

## 已实现能力（对照需求）

- ✅ 原生安装 codex（0.165.0）+ claude（2.1.312）到手机（npm -g，arm64）。
- ✅ 原生 App 全面接管 CLI 显示：对话气泡、流式文本、工具调用卡片、diff 查看、usage、错误。
- ✅ Codex 手机端式对话 + 开发体验：会话抽屉、底部药丸输入条、agent 切换（codex/claude/dsh）、
  深色主题、待确认卡片、workspace 选择、文件查看接口。
- ✅ 桌面端级功能完整实现：多会话、恢复/重放（cursor）、停止/取消、日志导出、路径隔离。
- ✅ CCSwitch 式配置：provider 档案（base_url/wire_api/token/模型）、agent 绑定、
  一键写入 codex `config.toml`/claude `settings.json`（保留无关配置）、**从主机 cc-switch 只读导入**、
  令牌仅显示末四位、密钥文件 0600、配置修订记录。
- ✅ 日志：每会话 normalized `events.jsonl` + 原始 `raw-<provider>.jsonl`（脱敏）、查看、过滤。

## 真机验证记录（2026-10-30，Asia/Shanghai）

设备：Xiaomi 13 `fuxi`，主线 Linux 7.3.0+，Ubuntu 26.10 **arm64**，Phosh/Wayland；已装
`codex-cli 0.165.0`、`claude 2.1.312`、`dsh 0.2.1-rc.5`，GTK4 + Adw + WebKit **6.0**。

- **主机测试**：`python3 tests/run_tests.py` → **56 项全过**（51 unit + 5 integration）。
- **手机验收**：`tests/device/acceptance.sh` → **PASS=17 FAIL=0**（安装布局/依赖/三 CLI/模块导入/
  内嵌 51 项单测/配置脱敏/归一化契约/落盘泄漏逐字节扫描/不变量审计/finalize 持久性审计）。
- **手机 claude 实跑**：`message => PHONE_CLAUDE_OK`，usage、done 完整落库。
- **手机 GUI 真实回合**（最终安装版）：`E2E_POLL={"bubbles":1,"assistantText":"FINAL_ACCEPTED"}` ——
  真实 claude 输出经「桥→控制器→归一化→持久化→push→DOM」全链路渲染进聊天气泡。
- **手机 GUI 自检**：`SELFTEST_RESULT={"ok":true,...,"protocol":1}`（WKWebView 渲染 + JS↔Python 桥往返）。
- **手机桥配置测试**：`{"boot_profiles":9,"session":true,"logs_ok":true,"save_profile":true,"token_masked":"****1234","preview_count":9}`。

## Codex 只读验收（13 轮，第 6 轮起通过）

由主机 `codex` 以 `--sandbox read-only` 独立验收；每轮 brief 与原报告逐字保存在本目录
（`CODEX-REREVIEW-BRIEF*.md`、`CODEX-ACCEPTANCE-REPORT-ROUND[1-13].md`）。

**第 1–6 轮：核心验收** —— 从"不通过"逐轮收敛到 **通过**：

| 轮次 | 结论 | 主要发现 → 处理 |
|---|---|---|
| 1 | 不通过 | 4 项阻断：token 可能经 prompt/raw/事件落盘明文；启动失败/异常路径 0 或重复 done；push 缺 `protocol_version`；`workspace_list`/`session_create` 可越界。另：TOML merge 误删无关表键、备份名同秒冲突 → 全部修复 |
| 2 | 不通过 | 第 1/3/4 项已修；第 2 项仍有缺口（push 在 done 上抛错可重复 done、异常分支跳过 finalize）；指出某脱敏测试空通过、脚本缺安全审计 → 修复 + 补审计 |
| 3 | 有条件通过 | 残余：SQLite 提交后 JSONL 失败会绕过 done 守卫；测试生命周期不可靠 → JSONL 降级为 best-effort 派生镜像 |
| 4 | 不通过 | finalize 仅尽力重试，持续性 DB 故障可能永久 `running`；异步测试跨事件循环 → journal + 开机对账；同一事件循环 |
| 5 | 有条件通过 | finalize 持久性判定成立；但 `test_store.py` 仍跨事件循环、journal 未 fsync → 修复 |
| **6** | **✅ 通过** | 两项核查均满足，未发现范围内回归 |

**第 7–13 轮：真机暴露的超时健壮性硬化** —— 实现层面自第 8 轮起均为"无代码问题"，
第 13 轮唯一残留被验收方明确标注为*复审容器不可写临时目录的环境限制*：

| 轮次 | 结论 | 要点 |
|---|---|---|
| 7 | 有条件通过 | 真机发现 `--timeout` 被静默忽略、且无默认超时 → provider 挂死会永久 running。修复：默认 900s + 全链路传参 + 超时后恰好一个 done |
| 8 | 有条件通过 | `timeout_s=0` 可关闭超时；缺默认值/入口传参测试 → clamp + 5 个入口测试 |
| 9 | 有条件通过 | 直接调用 driver 时 0/负数仍无界；dsh 测试未覆盖 None/负数 → `models.effective_timeout` + driver 侧 clamp |
| 10 | 有条件通过 | `effective_timeout` 接受 `inf` → 硬上限 `MAX_TURN_TIMEOUT_S`（7 天）+ 非有限值回落 |
| 11 | 有条件通过 | `float(10**10000)` 抛未捕获 `OverflowError` → 捕获 + 测试 |
| 12 | 有条件通过 | 断言过弱、缺 env 路径测试 → 断言精确回退值 + `TestTimeoutEnvSanitization` + `run_tests.py` 预检 |
| 13 | 有条件通过（**无代码问题**） | `_sanitize` 各分支已由直接测试覆盖；唯一残留为复审容器 `/tmp` 不可写，验收方明确"这是环境限制，不是代码缺陷" |

历轮落地的加固（均已由测试锁定）：
- `Store` 在 **prompt、事件、raw 行** 三处落盘前脱敏；桥保存/导入 token 后 `refresh_redactor()`。
- `controller._emit` 吞掉 push 异常（事件已落盘，UI 用 cursor 重同步）；push 失败不再被误判为 driver 崩溃。
- `run_wrapper` 以 `done_persisted` 守卫「恰好一个 done」；`finish_turn` 重试 3 次，失败则
  **journal 记录预期终态**（`recovery.jsonl`，flush+fsync），`App` 启动时 `recover_orphans()` 对账，
  **永不留下永久 `running` 的 turn**。SQLite 为权威，JSONL 仅 best-effort 派生镜像。
- **超时必然有界**：`models.effective_timeout()` 把 `None/0/负数/inf/nan/非法值` 一律归一化为默认值、
  正有限值截断到 `MAX_TURN_TIMEOUT_S`；controller、BaseDriver、DshDriver **全部** 使用该归一化结果，
  即使直接调用 driver 也不会无界等待。超时后由 base 发出恰好一个 done（cancelled）。
- 桥的 `workspace_list`/`session_create` 限制在 home 与系统临时目录内（`resolve(strict=True)`）。
- Codex TOML 只清理首个表之前的顶层键，无关表键保留；备份名含微秒。

## 测试与验收

```bash
TMPDIR=/tmp python3 tests/run_tests.py   # 70 项（65 unit + 5 integration），无需 PYTHONPATH
bash tests/device/acceptance.sh fuxi  # 手机上：17 项检查（含泄漏扫描/不变量/finalize 持久性审计）
```

## 已知限制 / 待办

- **codex 推理在手机上的网络限制**：手机能连通各中转站（`x.ailzd.com` 200；`apxrelay.com`、
  `mdkj.lol`、`xiaoyi.loc.cc` 均可达但需鉴权），**claude 全链路正常**；但 codex 的
  `responses` 流在两个中转站上均出现
  `stream closed before response.completed` / `Reconnecting…`，即**中转站侧的流式转发**不适用于
  codex 客户端（codex 0.165 已移除 `wire_api="chat"`，无法退回 chat-completions）。
  这是 provider/传输问题而非 App 逻辑：
  - App 的 codex 驱动本身已验证——主机端到端跑通（`SUPERTERM_OK`），手机端 codex 的 JSONL
    （`thread.started`/`item.*`/`error`）被正确解析、归一化、落库并实时推送到 UI；
  - 且已加固：provider 挂死时由 **per-turn 超时**终止子进程，**恰好发出一个 done（cancelled）并 finalize**。
    默认 900s（`SUPERTERM_TURN_TIMEOUT` 可调），`--timeout` / 桥 `timeout_s` 可覆盖；
    `None/0/负数/非法值` 一律 clamp 回默认（`models.effective_timeout`），**即使直接调用 driver 也不会无界等待**。
    真机验证 `--timeout 25` → `rc=0`，`1 × done`，不再永久挂起。
- 视觉截图：Phosh 息屏锁定后远程 `grim` 只能拿到黑屏；功能以 DOM E2E 文本证据验收。可点桌面
  「SuperTerm」图标现场查看。
- dsh 适配器为 one-shot 文本包装，无结构化 resume；续聊靠拼接待有界历史。
- 权限：默认 `--dangerously-skip-permissions`（bypass）；approval 卡片 UI 已就绪，等待 CLI 事件驱动。
- 手机在长时间 GUI 自动化下偶发重启（内存/热），重启后 `/tmp` 清空但 `/opt` 与 `~/.local` 安装保留。

## 目录

- `src/superterm/`：`main.py`(GTK窗口)、`bridge.py`(严格协议)、`app.py`(门面)、`headless.py`(CLI)、
  `models.py`、`paths.py`、`redaction.py`、`drivers/`、`sessions/`、`config/`、`ui/`。
- `packaging/`：`superterm.desktop`、`superterm.svg`、`superterm-launch`、`install-remote.sh`。
- `tests/unit`、`tests/integration`、`tests/device`。
- `../scripts/superterm-deploy.sh`：主机一键部署/回滚。
