# SuperTerm 验收任务（只读）

你是验收方（Codex）。目标：对 `work/superterm/` 这个已实现的「手机原生超级终端 App」做**只读**代码与证据审查，给出验收结论。**不要修改任何文件**，只输出审查报告。

## 背景
在 Xiaomi 13（fuxi，Ubuntu 26.10 arm64 主线 Linux + Phosh）上原生运行 codex/claude/dsh 三个 CLI，
并自研 GTK4+WebKit6 原生 App，直接驱动三者的 JSON 结构化流，把终端接管为对话式开发界面；
内建 CCSwitch 式 provider/模型/token 配置与日志子系统。开发计划见 `SUPERTERM-DEVELOPMENT-PLAN.md`。

## 可审查材料（均在 work/superterm/）
- `README.md`：进展、架构、真机验证记录、已知限制。
- `src/superterm/`：`models.py paths.py redaction.py`、`drivers/{normalize,base,codex,claude,dsh}.py`、
  `sessions/{store,controller}.py`、`config/{profiles,writers,import_cc_switch}.py`、
  `app.py bridge.py headless.py main.py`、`ui/{index.html,styles.css,app.js}`。
- `tests/unit`, `tests/integration`, `tests/device/acceptance.sh`, `tests/device/acceptance-evidence.txt`。
- `packaging/`：desktop/icon/launch/installer。

## 审查要点（请逐条给结论：通过/存疑/不通过 + 依据文件行）
1. **安全/脱敏**：日志与事件是否可能落盘明文 token？（看 redaction.py 是否覆盖 sk-*/Bearer/JWT/自定义密钥；store 落盘前是否调用 redactor）。
2. **进程管理**：driver 是否正确子进程隔离（start_new_session）、终止（SIGTERM→SIGKILL）、且**只发一次** done 事件（base.py 权威 done）。
3. **结构化集成**：是否避免解析 ANSI/TUI，只解析 JSONL（normalize.py）；codex `exec --json`、claude `-p --output-format stream-json` 是否正确。
4. **配置写入安全**：写 codex TOML / claude settings.json 是否**保留既有无关配置**（merge），是否备份，token 是否不落 argv。
5. **路径限制**：workspace 文件接口是否限制在 workspace 内（paths.is_within）。
6. **协议**：bridge.py 是否严格 request/response + push、请求体上限、错误不外泄敏感信息。
7. **工程完整性**：单测覆盖核心不变量；真机证据是否真实可核对（acceptance-evidence.txt 14/14 PASS）。

## 输出
在 `work/superterm/CODEX-ACCEPTANCE-REPORT.md` 之外**不要写文件**；直接在回答里给出：
- 总体结论（通过 / 有条件通过 / 不通过）
- 逐条要点结论与依据
- 阻断级问题（若有）与建议的非阻断改进
