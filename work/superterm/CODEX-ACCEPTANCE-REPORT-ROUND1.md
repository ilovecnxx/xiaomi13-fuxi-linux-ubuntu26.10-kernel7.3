# SuperTerm 第一轮 Codex 只读验收报告（原始结论）

> 验收方：主机 codex-cli（只读审查）。日期：2026-10-30。
> 该轮结论为「不通过」，驱动了本项目 `CODEX-REREVIEW-BRIEF.md` 所述的修复。

---

总体结论：**不通过**。存在阻断级安全、协议和路径边界问题；`acceptance-evidence.txt` 的 `14/14 PASS`
不足以证明 7 项验收要求均满足。

## 1. 安全/脱敏：不通过

- 通用规则覆盖 `sk-*`、Bearer、JWT、`Authorization`、`x-api-key`：`src/superterm/redaction.py:9-17`。
- 但 `App` 只在启动时读取已有 token 创建 `Redactor`：`src/superterm/app.py:24-31`；桥接层之后新增/导入
  token 时只写入配置，不刷新 redactor：`src/superterm/bridge.py:123-132`。
- `Store.create_turn()` 将原始 prompt 直接写入 SQLite：`src/superterm/sessions/store.py:129-135`。
  prompt 中若包含自定义 token，会绕过脱敏。
- `Store.append_event()` 和 `raw_sink()` 本身没有强制调用 redactor，直接落盘：
  `src/superterm/sessions/store.py:147-168`、`175-188`。
- 控制器只在事件层做脱敏：`src/superterm/sessions/controller.py:40-50`。
  因此新建的非 `sk-*` 自定义 token 可能进入 turns、raw JSONL 或事件日志。

## 2. 进程管理：不通过

- 正常启动使用独立进程组：`src/superterm/drivers/base.py:83-91`。
- 终止流程确实是 SIGTERM 等待后 SIGKILL：`src/superterm/drivers/base.py:153-172`。
- 正常完成路径由 base 统一发送 done：`src/superterm/drivers/base.py:143-148`，并吞掉 provider 自带 done：
  `src/superterm/drivers/base.py:71-79`。
- 但启动失败时 `M.ev_done("error")` 会被 `_final_done_sent == False` 的逻辑吞掉：
  `src/superterm/drivers/base.py:92-98`，导致 **0 个 done**，不满足每次运行恰好一次 done。
- 控制器捕获 driver 异常时只发送 error，没有补 done：`src/superterm/sessions/controller.py:101-109`。

## 3. 结构化集成：存疑

- normalizer 明确以 JSON/JSONL 为输入，不解析 ANSI/TUI：`src/superterm/drivers/normalize.py:1-7`、`202-230`。
- Codex 使用 `exec --json`：`src/superterm/drivers/codex.py:15-25`；Claude 使用
  `-p --output-format stream-json --verbose`：`src/superterm/drivers/claude.py:14-25`。
- 但冻结的设备契约还要求 Claude 使用 `--input-format stream-json`：`tests/device/m0-report.md:39-46`，实现中缺失。
- 集成测试只运行 fake Codex driver：`tests/integration/test_driver_contract.py:20-104`，
  没有真实 Claude/DSH 进程契约测试。

## 4. 配置写入安全：不通过

- 原子写入和备份机制存在：`src/superterm/config/writers.py:26-50`；Claude settings 使用 merge：
  `src/superterm/config/writers.py:133-150`；token 通过环境变量传给子进程：
  `src/superterm/config/writers.py:53-75`，不进入 driver argv。
- Codex TOML merge 是逐行启发式处理，会删除任意表中的 `model`/`model_provider` 行，而不区分是否属于
  SuperTerm 管理区块：`src/superterm/config/writers.py:91-112`。
- 备份文件名只有秒级时间戳：`src/superterm/config/writers.py:44-50`，同一秒多次写入可能覆盖备份。
- 现有测试只覆盖简单 `[desktop]` 场景：`tests/unit/test_config.py:62-74`。

## 5. 路径限制：不通过

- 文件读取接口正确调用 `is_within`，并检查真实路径：`src/superterm/bridge.py:186-199`、`src/superterm/paths.py:64-82`。
- 但目录列表接口直接接受调用方提供的 `root`，没有 workspace 限制：`src/superterm/bridge.py:177-184`。
- 创建会话时 workspace 也未经根目录或真实路径校验：`src/superterm/bridge.py:95-99`。

## 6. 协议：不通过

- 请求版本、request id、方法和 2 MiB 请求上限有校验：`src/superterm/bridge.py:41-58`。
- 但协议契约要求 push 包含 `protocol_version`：`tests/device/m0-report.md:62-67`；控制器实际发送的 push
  只有 `kind/cursor/payload`：`src/superterm/sessions/controller.py:40-51`。
- 桥接层没有实际的 origin 校验（GUI 实际加载 `file://`）：`src/superterm/bridge.py:7-8`、`src/superterm/main.py:103-106`。
- 通用异常直接把 `str(e)` 返回前端：`src/superterm/bridge.py:62-64`、`src/superterm/main.py:150-155`。

## 7. 工程完整性：不通过

- 单测覆盖了基础 normalizer、配置、桥基础调用和 fake driver，但没有覆盖 token 落盘泄漏、turn prompt 脱敏、
  done 唯一性、push envelope、workspace_list 越界、配置复杂 merge、真实 Claude/DSH 进程等核心不变量。
- `tests/device/acceptance.sh` 主要检查安装/依赖/模块导入/单测/配置 round-trip/normalizer，没有执行上述审计。

## 阻断级问题（第一轮结论）

1. 自定义 token 可通过 `turns.prompt`、raw 日志或事件落盘明文。
2. bridge server push 不符合冻结协议，客户端按契约无法可靠消费。
3. workspace 列表可访问任意调用方指定目录，路径边界不完整。
4. driver 启动失败和异常路径不能保证恰好一个 done 事件。
