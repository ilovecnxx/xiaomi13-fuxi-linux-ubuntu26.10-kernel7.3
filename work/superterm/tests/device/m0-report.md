# M0 report — SuperTerm prerequisites & contracts

Date: 2026-10-30 (Asia/Shanghai). Device: Xiaomi 13 `fuxi`, mainline Linux 7.3.0+ #209, Ubuntu 26.10 arm64, Phosh/Wayland.

## Device capability matrix (verified over SSH)

| Item | Value | OK |
|---|---|---|
| Python | 3.14.8 | ✅ |
| PyGObject (gi) | 3.57.1 | ✅ |
| GTK | 4.0 introspection OK | ✅ (recommended stack) |
| libadwaita | 1.10.0 (Adw 1) | ✅ |
| WebKitGTK | gir1.2-webkit2-4.1 = 2.52.6 | ✅ |
| Node | v24.19.0 | ✅ |
| npm | 11.16.0, registry npmjs.org reachable | ✅ |
| sqlite3 CLI | absent (use Python sqlite3) | — |
| Free space | 89 GB | ✅ |
| Browser | epiphany only (no chromium) | app must not require chromium |
| RAM | 10 GB total, ~9 GB free | budget: keep app < ~400 MB |

## CLI install feasibility

- `@openai/codex` resolves on npm (0.165.0), `@anthropic-ai/claude-code` resolves (2.1.312).
- `dsh` 0.2.1-rc.4 already installed at /usr/local/bin/dsh.
- Install as target user `fuxi` via `npm install -g`; arm64 binaries ship in the packages.

## Structured transports (the integration backbone — never parse ANSI TUI)

### Codex — `codex exec --json --skip-git-repo-check -C <dir> "<prompt>"`
Emits JSONL on stdout. Observed event types:
- `{"type":"thread.started","thread_id":...}`
- `{"type":"turn.started"}`
- `{"type":"item.started"|"item.completed","item":{...}}` where `item.type` ∈
  `agent_message` {text}, `command_execution` {command, aggregated_output, exit_code, status},
  `file_change` {…}, `reasoning` {text}, `error` {message}
- `{"type":"turn.completed","usage":{input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens}}`
- Resume: `codex exec resume`; also `codex review`, `codex apply`.

### Claude Code — `claude -p --output-format stream-json --input-format stream-json --verbose ...`
Emits JSONL. Observed types (every record has `session_id`, `uuid`):
- `{"type":"system","subtype":"task_started"|"task_notification"|"thinking_tokens", ...}`
- `{"type":"assistant","message":{"content":[{"type":"text","text"}|{"type":"thinking","thinking"}|
   {"type":"tool_use","id","name","input"}], "usage":{...}, "model":...}}`
- `{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id","content","is_error"}]}}`
- `{"type":"result", ...}` final.
- Flags: `--resume <id>`, `--continue`, `--permission-mode`, `--json-schema`, `--dangerously-skip-permissions`, `--bare`.
- ⚠️ Host `claude` has a `Stop` hook (`cc-continue.py`) that auto-continues — for acceptance runs use
  `--bare` and/or an isolated HOME so it never performs unbidden host work.

### DSH — `dsh headless "<task>"`
One-shot; no guaranteed JSON event stream / resume. Adapter synthesizes turn boundaries and
appends bounded prior transcript for "continue". (dsh-acp investigated but not required for v1.)

## cc-switch data model (host `~/.cc-switch/cc-switch.db`, SQLite)

Table `providers`: `id, app_type, name, settings_config(JSON), is_current, category, ...`.
- `app_type='codex'` → `settings_config = {auth:{OPENAI_API_KEY}, config:"<TOML string>", modelCatalog?}`
- `app_type='claude'` → `settings_config = {env:{ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_MODEL, ...}}`
Also: `provider_endpoints`, `provider_health`, `mcp_servers`, `model_pricing`, `proxy_request_logs`.
SuperTerm importer reads this DB **read-only**, masks tokens, maps into SuperTerm provider profiles.

## Contracts frozen for M1
- Normalized event envelope (schema_version 1): kind ∈ message|tool_call|tool_result|diff|
  approval_request|usage|error|done|lifecycle.
- Bridge protocol: request {protocol_version, request_id, method, params} → {request_id, ok, result|error};
  push {protocol_version, kind, cursor, payload}.
- Redaction runs before any persistence of raw or normalized events.

## Notes / deviations
- No chromium: the app uses WebKitGTK embedded UI (confirmed present), never `chromium --app`.
- Claude auto-continue hook requires isolated execution context for tests/acceptance.
