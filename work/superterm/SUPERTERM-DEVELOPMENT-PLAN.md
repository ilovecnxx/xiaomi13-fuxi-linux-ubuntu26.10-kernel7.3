# SuperTerm development plan

## 1. Scope and outcome

Build `SuperTerm`, a normal-user desktop application for the Xiaomi 13 running Ubuntu 26.10 arm64, Phosh, and Wayland. It owns the terminal-facing experience: the user sees a mobile-first chat and development workspace instead of a black terminal or an embedded DSH Web page. The application directly starts and observes the three installed CLIs:

| Agent | Required command path | Structured transport | Resume capability |
|---|---|---|---|
| Codex | `codex` from `@openai/codex` | `codex exec --json ...` JSONL | `codex exec resume` |
| Claude Code | `claude` from `@anthropic-ai/claude-code` | `claude -p --output-format stream-json --input-format stream-json --verbose ...` | `--resume`, `--continue` |
| DSH | `dsh` 0.2.1-rc.4 | `dsh headless "<task>"`; use ACP only if headless output cannot provide an event | no guaranteed resume; persist application context and start a continuation |

The deliverable is an installable, inspectable app under `work/superterm/` with a user-level launcher and services. Runtime changes are limited to the Ubuntu userspace and require no vendor kernel, HAL, root, or privileged daemon.

The plan assumes the existing facts in `CODEX-BRIEF.md` and treats the host `dsh-phone` scripts as deployment precedent. Product behavior is independent of the DSH Web UI.

## 2. Architecture decision

### 2.1 Recommendation

Use a **Python 3 GTK4/libadwaita native shell with an embedded WebKitGTK 4.1 local UI**, plus an asyncio Python application service in the same process initially:

1. GTK4/libadwaita owns the Wayland/Phosh window, lifecycle, focus, keyboard insets, adaptive navigation, notifications, and desktop integration.
2. WebKitGTK renders a bundled local HTML/CSS/JavaScript single-page UI. It loads from `app://superterm/` or a loopback-free custom WebKit URI scheme, so the shell works offline and does not need a browser.
3. A typed WebKit user-message bridge connects the UI to the Python controller. The controller owns agent subprocesses, session state, provider configuration, logs, filesystem operations, and approvals.
4. Python `asyncio.create_subprocess_exec` reads stdout and stderr incrementally. The driver never parses terminal escape sequences for the three structured modes.
5. SQLite stores metadata and normalized events; append-only JSONL files store the raw and normalized session stream for recovery and export.
6. Node 24 remains the runtime for the installed JavaScript CLIs, not the runtime for SuperTerm itself. This avoids adding a second GUI runtime and keeps the native window small on a 10 GB phone.

The first release keeps the controller in the GTK process to reduce moving parts. Define the controller behind a local IPC-shaped interface from day one so it can move into `superterm-agentd` later if crashes or concurrent sessions justify process isolation.

### 2.2 Candidate comparison

| Candidate | Strengths | Costs and risks | Decision |
|---|---|---|---|
| GTK4/libadwaita + Python widgets only | Best Phosh integration; lowest rendering stack; simple packaging | Building a rich markdown/diff/file UI, virtualized message list, and streaming renderer in GTK is slower; syntax highlighting and responsive desktop layout need custom widgets | Fallback for a WebKit performance failure |
| WebKitGTK + local HTML/JS + Python controller | Native GTK window, strong UI ergonomics, offline bundle, fast iteration, no browser dependency, straightforward mobile/desktop responsive CSS | WebKit memory use must be measured; bridge needs strict origin and schema validation; WebKit APIs differ between distro versions | **Recommended** |
| Electron-like Node + WebKitGTK | Reuses Node ecosystem for UI/backend | Electron itself is not available or appropriate on arm64/Ubuntu phone; custom Node GTK bindings add packaging and ABI risk; duplicate runtime and weaker libadwaita integration | Reject |
| PTY daemon + local webview | Works with arbitrary terminal programs and can be reused by other clients | ANSI/PTY parsing is fragile for the required JSON contracts; adds PTY lifecycle complexity; obscures structured approvals/diffs; still needs a GUI host | Use only as an optional “raw shell” feature after core release |

### 2.3 Runtime boundaries

```text
Phosh/Wayland
  -> superterm-window (Python GTK4/libadwaita + WebKitGTK)
       -> WebKit bridge (request/response + server-push event envelopes)
       -> Session controller
            -> Agent adapters (codex / claude / dsh)
            -> Provider/config store (SQLite, 0600 files)
            -> Session/event log writer (SQLite + JSONL)
            -> Workspace/file service (path confined per session)
       -> user-level systemd launch/idle units
```

Keep secrets and subprocess handles on the Python side. The JavaScript UI receives redacted provider summaries and redacted event payloads only.

## 3. Repository and file layout

Create this layout in the repository root `work/superterm/`:

```text
work/superterm/
  SUPERTERM-DEVELOPMENT-PLAN.md
  pyproject.toml
  README.md
  LICENSE
  src/superterm/
    __init__.py
    main.py                 # GTK application entry point
    window.py               # libadwaita window, WebKit setup, lifecycle
    bridge.py               # validated UI request/response and push events
    controller.py            # session orchestration and command routing
    models.py                # typed domain objects and event envelopes
    paths.py                 # XDG paths and permission checks
    drivers/
      __init__.py
      base.py                # adapter protocol and process handle
      codex.py               # codex exec / resume / review / apply
      claude.py              # stream-json process and resume modes
      dsh.py                 # dsh headless adapter and continuation policy
      normalize.py           # CLI-specific event mapping
      redaction.py           # token/secret scrubbing before persistence
    config/
      store.py               # SQLite schema and transactions
      profiles.py            # provider/profile CRUD and agent bindings
      import_cc_switch.py    # read-only host DB/catalog importer
      writers.py             # codex/claude/dsh materialization
      secrets.py             # secret storage, masking, permission checks
    sessions/
      store.py               # session/turn/event persistence
      lifecycle.py           # start, stop, resume, cancel, recovery
      workspace.py           # workspace roots and file access checks
    logs/
      writer.py              # normalized and raw JSONL appenders
      query.py               # bounded reads, filtering, export
      rotate.py              # size/count/age rotation
    ui/
      index.html
      app.js
      bridge.js
      state.js
      markdown.js
      styles/
        tokens.css
        layout.css
        components.css
        mobile.css
        desktop.css
      components/
        message-list.js
        composer.js
        tool-card.js
        diff-viewer.js
        file-viewer.js
        approval-sheet.js
        session-drawer.js
        provider-settings.js
        log-viewer.js
  tests/
    unit/test_normalize.py
    unit/test_redaction.py
    unit/test_config_import.py
    unit/test_session_recovery.py
    integration/fake_agents.py
    integration/test_driver_contract.py
    integration/test_bridge_contract.py
    device/README.md
  packaging/
    superterm.desktop
    superterm.svg
    superterm-launch
    superterm-window
    systemd/user/superterm-agent.service
    systemd/user/superterm-idle.service
    systemd/user/superterm-idle.timer
    install-remote.sh
    uninstall-remote.sh
    manifest-arm64.txt
  tools/
    inspect-device.sh
    export-session.sh
    doctor.py
```

Do not put tokens, copied host databases, generated logs, or device-specific paths in Git. Add `.gitignore` entries for `state/`, `logs/`, `*.db`, `*.jsonl`, and local build output.

## 4. Agent driver layer

### 4.1 Adapter contract

Define one internal adapter protocol. Each adapter must expose:

| Operation | Inputs | Output/behavior |
|---|---|---|
| `start_turn` | session id, workspace path, prompt, model/profile, approval mode | process handle plus an async stream of normalized events |
| `resume_turn` | persisted provider session id, new prompt, workspace, profile | same event stream; return a typed `resume_unsupported` error when not possible |
| `cancel` | process handle, reason | SIGTERM, grace period, then SIGKILL; emit `error` and `done` with cancelled status |
| `health` | executable path and version request | executable, version, architecture, and structured-mode smoke result |
| `capabilities` | none | supports resume, review, apply, approvals, streaming, and model override |

The controller creates at most one active process per session. A configurable global limit of two active agent processes protects the 10 GB device; queue later turns and expose queue state in the UI. Permit one Codex and one non-Codex process concurrently only after M1 resource measurements.

### 4.2 Process construction

Use `execve`-style argument arrays; never invoke a shell. Set `cwd` to the session workspace. Build a minimal environment from the current user environment plus the selected provider materialization. Capture stdout and stderr separately with UTF-8 replacement decoding and line framing. Set a process group so cancellation reaches child processes. Record executable path, argument names, start time, PID, and exit status, but never record environment values.

Before the first real turn, run an adapter health check that verifies the executable is arm64-compatible, prints a version, and accepts a harmless structured request in a temporary workspace.

### 4.3 Codex mapping

Start turns with `codex exec --json --skip-git-repo-check -C <workspace> <prompt>`. Add the selected model/config flags only through the adapter. Parse one JSON object per line:

| Codex input | Normalized event |
|---|---|
| `thread.started` | `session_started` metadata with provider session/thread id |
| `turn.started` | `turn_started` |
| `item.completed` + `agent_message` | `message` with final/partial text and item id |
| `item.completed` + `command` | `tool_call` with command, cwd, and item id |
| `item.completed` + `file_change` | `diff` with path, patch or change summary, and item id |
| `item.completed` + `error` | `error` with provider code and redacted detail |
| `turn.completed` | `usage` followed by `done` with status and provider ids |
| malformed line or nonzero exit | `error` containing a bounded raw-line hash/reference, then `done` |

Persist the Codex thread id and last completed turn id. Resume with `codex exec resume` only after checking the installed CLI help/version for the exact positional syntax; keep the command builder version-gated so an upgrade cannot silently send a wrong resume command. Expose `codex review` and `codex apply` as explicit slash commands backed by separate controller operations.

### 4.4 Claude mapping

Start with the verified noninteractive stream command: `claude -p --output-format stream-json --input-format stream-json --verbose --dangerously-skip-permissions`, passing a JSON input frame for the prompt and session context. Put permission behavior behind a profile/session setting; the default product mode should surface approvals when the CLI supports them, while an explicit “device automation” mode may use the dangerous flag.

Normalize Claude stream events by inspecting `type`, `subtype`, message/content blocks, tool use/result blocks, and result/usage fields:

| Claude input shape | Normalized event |
|---|---|
| assistant text/content delta | `message` with `stream_id` and `delta` |
| tool-use block | `tool_call` with tool name, input, and call id |
| tool-result block | `tool_result` with call id, output, exit/error state |
| permission/control request | `approval_request` with action, target, and expiry |
| result/usage event | `usage`, then `done` |
| stream parse or process error | `error`, then `done` |

Persist the Claude session id and the last event sequence. Prefer `--resume <id>` for the same session; use `--continue` only for the user-visible “continue most recent” command. If a resume fails, retain the original transcript and offer a new turn with a clearly labeled continuation prompt.

### 4.5 DSH mapping

Use `dsh headless "<task>"` as the first supported path. Because the verified command is one-shot, emit a synthetic `turn_started`, stream any parseable progress/message/tool information exposed by DSH, and always end with `done`. Do not pretend that DSH supports provider-independent resume. Store the DSH run id if present; otherwise generate an application turn id and offer “continue” by including a bounded prior transcript and current workspace summary in a new `dsh headless` request.

Investigate `dsh-acp` in M0/M1. Adopt ACP only if it produces stable machine-readable tool and approval events without embedding the DSH Web UI. Keep the headless adapter as the compatibility fallback.

### 4.6 One normalized event model

Every event is an envelope with:

| Field | Required meaning |
|---|---|
| `schema_version` | integer, start at `1` |
| `event_id` | application UUID |
| `session_id`, `turn_id` | stable application identifiers |
| `provider` | `codex`, `claude`, or `dsh` |
| `kind` | `message`, `tool_call`, `tool_result`, `diff`, `approval_request`, `usage`, `error`, `done`, or lifecycle kind |
| `ts` | UTC RFC 3339 timestamp and monotonic sequence |
| `data` | typed payload, bounded in size |
| `raw_ref` | file offset/hash reference to raw stream, never raw secrets |
| `redactions` | field names/counts redacted before persistence |

Define payload requirements for each kind. For example, `tool_call` carries `call_id`, `name`, `arguments` or command, `cwd`, and `risk`; `diff` carries relative path, status, old/new line counts, patch text or an artifact reference, and truncation state; `approval_request` carries an opaque request id, human description, affected paths/commands, and allowed actions `approve`, `deny`, `always_for_session`.

The bridge sends events in order and includes a `cursor`. On reconnect, the UI requests events after the last cursor. The controller must tolerate duplicate requests and replay from SQLite/JSONL.

### 4.7 Session persistence, resume, cancellation, and recovery

Persist these entities: `sessions`, `turns`, `events`, `agent_bindings`, `workspace_roots`, and `process_runs`. Store provider session ids separately from prompts. On startup, mark any `running` process run as `abandoned`, replay the last bounded events, and show a recovery banner with actions to resume, continue as a new turn, or discard.

Cancellation sequence: set a durable `cancel_requested` flag, send SIGTERM to the process group, wait up to five seconds, send SIGKILL if needed, close pipes, append a redacted raw tail, and emit `done(status=cancelled)`. App/window close requests cancellation but does not delete sessions. An explicit force-quit action may leave a recoverable abandoned run.

### 4.8 UI bridge API

Freeze the bridge as a versioned JSON request/response protocol before writing UI components. Every request contains `protocol_version`, `request_id`, `method`, and `params`; every response contains `request_id`, `ok`, and either `result` or a typed `error`. Server-push messages contain `protocol_version`, `kind`, `cursor`, and `payload`. Reject unknown methods, oversized payloads, invalid ids, and messages from any origin other than the bundled app origin.

| Method | Parameters | Result |
|---|---|---|
| `app.get_boot_state` | none | app version, protocol version, capabilities, active sessions, redacted provider summaries |
| `session.list` | page cursor, filter | session summaries and next cursor |
| `session.create` | agent, workspace, provider/model binding, approval mode | session summary and initial cursor |
| `session.open` | session id, cursor | session metadata and replayed events |
| `turn.start` | session id, prompt, optional model override | turn id and queued/running state |
| `turn.cancel` | session id, turn id | cancellation acknowledgement |
| `turn.resume` | session id, provider session id, prompt | new turn id or typed unsupported/error result |
| `approval.respond` | session id, request id, decision | durable decision acknowledgement |
| `workspace.list` | optional root id | safe directory summaries |
| `workspace.read_file` | session id, relative path, byte/line bounds | content, hash, truncation flag |
| `config.list_profiles` | include disabled flag | masked profiles, models, agent bindings |
| `config.save_profile` | profile fields and optional token | revision id and masked profile |
| `config.import_cc_switch` | source path, selected ids | preview or import report; source remains untouched |
| `config.health_check` | provider id | status, latency, redacted diagnostic |
| `logs.query` | session id, filters, cursor, limit | redacted event rows and next cursor |
| `logs.export` | session id, selection, destination intent | export id/path and manifest summary |

Push `event.appended` whenever a normalized event is committed, `session.updated` for lifecycle changes, `approval.requested` for new approvals, and `app.notice` for recoverable diagnostics. The UI acknowledges the highest cursor it has rendered; the controller retains replayable events regardless of acknowledgement.

## 5. Chat and development UX

### 5.1 Primary shell

Use a libadwaita adaptive window with a three-region layout:

| Region | Mobile behavior | Desktop behavior |
|---|---|---|
| Session drawer | full-screen sheet opened by a menu button | persistent narrow sidebar |
| Conversation | single-column message list with composer pinned above the keyboard | central conversation column with max readable width |
| Inspector | bottom sheet for files/diffs/logs | resizable right pane |

The first screen is the most recent session or an explicit “New session” view. Show provider, model, workspace basename, connection/queue state, and a stop button in the header. Use accessible labels and keyboard navigation; do not rely on hover for actions.

### 5.2 Message and streaming behavior

Render user messages, assistant messages, system notices, and errors as distinct message types. Stream text deltas into one assistant bubble per turn, coalescing updates at approximately 30–60 ms to avoid WebKit layout churn. Render Markdown with a bounded parser: headings, paragraphs, lists, fenced code, links, and tables; sanitize HTML and never allow arbitrary script/style injection. Preserve the raw text for copying.

Tool calls appear as collapsible cards with provider, tool name, status, elapsed time, command/path summary, and an expand action. Tool results remain linked by `call_id` and show a bounded preview with “open full output” in the log viewer.

### 5.3 Diffs and files

For `diff` events, provide a unified/split toggle on desktop and unified view on narrow screens. Highlight additions/removals, show path and file status, and cap inline patch rendering to a configurable line limit with an artifact link for larger diffs. A file viewer reads only paths under the session workspace, blocks `..` traversal and symlink escapes, shows syntax-aware plain text, and displays a stale-file warning if the on-disk hash changes.

Add “open workspace” and “change workspace” actions. The workspace picker must default to user-owned directories, show free space, and require confirmation before using a directory outside the configured roots.

### 5.4 Approvals and commands

Approval requests are modal/bottom-sheet surfaces with plain-language action, exact affected paths or command, provider, and expiry. Buttons are `Approve once`, `Deny`, and, only for trusted profiles, `Allow for this session`. Store the decision and request id; never infer approval from a closed sheet.

Implement slash commands in the composer with completion:

| Command | Behavior |
|---|---|
| `/new` | create a session |
| `/workspace <path>` | choose or change workspace |
| `/model` | select provider/model binding |
| `/resume` | resume provider session when supported |
| `/continue` | start a continuation turn |
| `/review` | Codex review operation |
| `/apply` | Codex apply operation after explicit confirmation |
| `/logs` | open filtered session logs |
| `/clear` | clear composer only; never delete history |

Keep command parsing local and deterministic. Render command errors as system messages.

### 5.5 Offline and failure states

The shell, history, provider editor, file viewer, and log viewer must work without network access. A provider health check must show `offline`, `auth failed`, `unreachable`, or `ready`; the composer remains usable for queued turns when the user explicitly chooses queue mode. Never silently retry a billable request. Show reconnect, retry, cancel, and export actions.

## 6. CCSwitch-style configuration subsystem

### 6.1 Store and data model

Use SQLite at `$XDG_STATE_HOME/superterm/superterm.db` with mode `0600`; default to `$HOME/.local/state/superterm/superterm.db`. Enable WAL, foreign keys, and a schema version table. Tables:

| Table | Important columns |
|---|---|
| `provider_profiles` | id, display_name, base_url, wire_api, enabled, created_at, updated_at |
| `provider_models` | provider_id, model_id, display_name, context_window, enabled |
| `provider_secrets` | provider_id, token_ciphertext/reference, last_four, updated_at |
| `agent_bindings` | agent, provider_id, model_id, extra_json |
| `config_revisions` | revision, actor, changed_at, summary |
| `health_checks` | provider_id, status, latency_ms, error_code, checked_at |

Tokens must not be stored in ordinary profile rows or serialized into UI state. Prefer a desktop secret service if available; otherwise store an encrypted or permission-protected secret file under `$XDG_STATE_HOME/superterm/secrets/` with `0700` directory and `0600` files. The implementation must document the exact fallback keying model and refuse to claim strong encryption if only filesystem permissions are available.

### 6.2 Profile behavior

Each profile contains a display name, base URL, wire API (`openai`, `anthropic`, or DSH-compatible), token, model catalog, enabled flag, and optional request headers that are allowlisted. The UI shows token presence and last four characters only. Validate HTTPS or an explicitly confirmed loopback URL, normalize trailing slashes, reject embedded credentials, and redact the token from exceptions, subprocess diagnostics, and logs.

Each agent has an independent binding to a provider and model. Switching a binding creates a config revision, updates future turns only, and does not mutate the provider id recorded on existing sessions.

### 6.3 Materialization into CLI configuration

Implement atomic, permission-preserving writers with backup and fsync:

| Agent | Materialization target | Required behavior |
|---|---|---|
| Codex | `$HOME/.codex/config.toml` | update only the managed provider/model sections; preserve unrelated user settings; write temp file mode `0600`, rename atomically |
| Claude | `$HOME/.claude/settings.json` and per-process environment | update the managed env/base URL/model values using a JSON parser; preserve unrelated keys; pass token through the child environment where Claude expects it, never as a command argument |
| DSH | per-process environment and a managed `$HOME/.config/superterm/dsh.env` if needed | set only DSH-compatible endpoint/token/model variables; do not overwrite DSH’s own user config without an explicit migration setting |

The driver should prefer per-process environment/flags for isolation and use write-through files only where the CLI requires them. After materialization, run a redacted config summary and a no-op health check. Keep one backup per revision and expose restore in settings.

### 6.4 Import from host cc-switch

Provide a read-only import wizard for `$HOME/.cc-switch/cc-switch.db`, the JSON catalog, and available backup snapshots. Do not open or modify the host database in place. The importer:

1. Checks file ownership, mode, SQLite integrity, and schema version.
2. Introspects tables/columns rather than assuming one cc-switch release schema.
3. Maps provider names, base URLs, wire API, model ids, and token references into a preview with token values masked.
4. Imports selected profiles in one transaction, recording source path, source hash, and import time.
5. Writes a report for unmapped columns and does not delete or rewrite source files.

Add fixtures derived from redacted cc-switch DB snapshots for unit tests. Test malformed DBs, newer schemas, duplicate provider names, missing tokens, and backup restoration.

## 7. Logs subsystem

Write logs under `$XDG_STATE_HOME/superterm/logs/<session-id>/`:

| File | Contents |
|---|---|
| `events.jsonl` | normalized event envelopes, one JSON object per line |
| `raw-codex.jsonl`, `raw-claude.jsonl`, `raw-dsh.jsonl` | exact stdout/stderr lines with timestamps and stream labels, after secret redaction |
| `session.json` | non-secret metadata, workspace, provider/model ids, state, and schema version |
| `attachments/` | large diff/output artifacts referenced by hash |

Flush after each event batch and use a write queue so UI streaming is not blocked by disk I/O. Redact bearer tokens, common API-key patterns, authorization headers, configured secret values, and environment dumps before both raw and normalized persistence. If a redaction changes the visible output, mark the event with `redactions` metadata.

Rotate raw files at 10 MiB, keep at most five generations per session, and enforce a configurable global log budget (default 512 MiB). Rotate only complete lines and write a manifest. Export creates a tar-like archive or directory copy containing selected redacted logs, session metadata, and optional diffs; it must never include secret files or the full provider database.

The in-app log viewer supports session/turn/provider/kind filters, newest-first navigation, bounded pagination, copy of a selected redacted event, and “open export folder.” Add a developer-only toggle to show raw line references, not unredacted content.

## 8. Packaging, installation, and lifecycle

### 8.1 Native CLI installation

The installation script runs over SSH and performs architecture/version checks before mutation:

1. Verify `dpkg --print-architecture` is `arm64`, Node is v24.x, npm is 11.x or compatible, Python 3 and GTK/WebKit introspection are available, and at least 2 GiB free space exists.
2. Install `@openai/codex` and `@anthropic-ai/claude-code` at pinned versions selected during M0. Use a configured registry with npmjs fallback, `--omit=dev`, and capture package lock/integrity metadata in `packaging/manifest-arm64.txt`.
3. Verify `codex --version`, `claude --version`, and arm64 execution as the target user. Never run the CLIs as root for validation.
4. Leave the existing `dsh` installation untouched; verify its version and `dsh headless` smoke behavior.
5. Install SuperTerm into a versioned user prefix such as `$HOME/.local/opt/superterm/<version>/`, with `$HOME/.local/bin/superterm` as a symlink.

If npm native dependencies or CLI package postinstall scripts fail on arm64, stop before replacing an existing working version and report the exact package/log. Do not substitute an x86 binary.

### 8.2 Launcher and systemd user units

Mirror the useful `dsh-phone` conventions while keeping ownership user-level:

| Artifact | Location/behavior |
|---|---|
| Launcher | `$HOME/.local/bin/superterm-launch`; records activity, verifies runtime, starts `superterm-agent.service` if used, then executes the GTK app |
| Desktop file | `$HOME/.local/share/applications/superterm.desktop`; `Exec=%h/.local/bin/superterm-launch`, `Terminal=false`, Phosh-friendly icon |
| User service | `$HOME/.config/systemd/user/superterm-agent.service`; on-demand, `Restart=on-failure`, no root, bounded environment |
| Idle service/timer | `$HOME/.config/systemd/user/superterm-idle.service` and `.timer`; stop only the idle controller after the configured idle period, preserving SQLite/logs |
| State | `$HOME/.local/state/superterm/` |
| Config | `$HOME/.config/superterm/` |

The main window updates a last-activity timestamp on focus, input, and active stream events. Idle shutdown must not stop a running turn or hide an approval. When no active session/process exists and the window has been closed for the configured interval, stop the optional agent service. Follow the existing `dsh-web` pattern of user-level systemd and on-demand startup, but do not require `sudo` during normal operation.

### 8.3 Rollback

Before install, record current symlink target, package versions, service unit hashes, config file hashes, and database schema version. Install to a new versioned directory, run health checks, then atomically switch the symlink. On failure, restore the old symlink and user units; leave new logs under a version-tagged directory for diagnosis. `uninstall-remote.sh` disables only SuperTerm units and removes only SuperTerm-owned files after a printed manifest review; it must preserve user CLI packages, cc-switch files, DSH state, and user data unless an explicit `--purge` is used.

## 9. Phased milestones

### M0 — reconnaissance and contracts

Work:

- Confirm GTK4/libadwaita and WebKitGTK 4.1 Python introspection versions on the real phone.
- Record exact `codex`, `claude`, and `dsh` help/version output and sample structured streams in redacted fixtures.
- Inspect cc-switch DB schemas, catalog formats, and current config files read-only.
- Measure baseline idle RAM, launch time, and WebKit rendering on Phosh.
- Finalize event schema, bridge schema, SQLite migrations, and redaction rules before UI implementation.

Exit criteria:

- A checked-in compatibility matrix identifies supported CLI versions and known syntax differences.
- Redacted fixtures cover normal, tool, diff, usage, approval, malformed, and cancellation cases for each available agent.
- A 10-minute device smoke script can verify all prerequisites over SSH without changing config.

Test strategy: run read-only probes on the phone as `fuxi`; compare host and phone JSON fixtures; record results in `tests/device/m0-report.md`.

### M1 — driver core

Work:

- Implement typed models, adapter protocol, subprocess supervision, normalizers, redaction, SQLite migrations, and JSONL writer.
- Add fake-agent integration fixtures that emit partial lines, stderr, malformed JSON, slow output, tool calls, diffs, and exits.
- Implement start, cancel, crash recovery, cursor replay, Codex resume, Claude resume/continue, and DSH continuation behavior.

Acceptance:

- Each adapter produces the same normalized event contract for equivalent fixtures.
- Cancellation completes within six seconds in the normal case and cannot leave a child process running.
- Restarting the controller recovers an abandoned run and replays events without duplication.
- Logs contain no configured token or bearer-like secret in a redaction test.

Test strategy: unit tests for each mapping and redaction rule; integration tests against fake agents; one real harmless turn per CLI on the phone in a temporary workspace.

### M2 — chat and development UI

Work:

- Build the WebKit bundle, strict bridge, adaptive shell, session drawer, composer, streaming renderer, tool cards, diff/file viewers, approval sheet, workspace picker, slash commands, and offline/error states.
- Add accessibility names, keyboard focus order, touch targets, text selection, copy, and mobile keyboard/inset handling.
- Implement event cursor replay and bounded rendering so long sessions do not grow the DOM without limit.

Acceptance:

- A user can create a session, choose a workspace, send a prompt to each agent, observe streaming text/tool/diff/usage events, approve/deny an approval, cancel, and reopen the session.
- The same UI remains usable at phone portrait, phone landscape, and a desktop Phosh window size.
- A 1,000-event synthetic session stays responsive and renders only the visible/bounded window.

Test strategy: browserless JavaScript unit tests for reducers/bridge validation plus GTK/WebKit smoke tests on the device; manual touch and hardware keyboard pass.

### M3 — provider configuration and logs

Work:

- Add profile/model/binding UI, SQLite store, secret handling, atomic CLI writers, health checks, cc-switch import preview, revisions, backups, log viewer, rotation, and export.

Acceptance:

- Import from a redacted host cc-switch DB produces correct profiles without modifying the source.
- Switching a profile changes only future turns and updates the correct Codex/Claude/DSH materialization.
- Tokens are masked in UI, absent from logs/exports, and files/directories have expected modes.
- A rotated session can be filtered and exported from the app.

Test strategy: fixture-driven importer tests, file-mode tests, temp-home writer tests, secret redaction tests, and real-device provider health checks using a test provider.

### M4 — install and deploy

Work:

- Pin and install the two missing npm CLIs natively on arm64; package SuperTerm, desktop icon, launcher, user units, idle timer, doctor, rollback, and SSH installer.
- Verify integration with Phosh and the existing DSH user-service pattern.

Acceptance:

- A clean-ish test user can install, launch from the app grid, run a smoke turn, close/reopen, and uninstall without root at runtime.
- Install failure before activation leaves the prior version working.
- `systemctl --user` status, journal, process list, and file ownership are inspectable over SSH.

Test strategy: repeat install/upgrade/rollback on the real phone; test with network available and unavailable; capture `doctor` output and journal excerpts.

### M5 — hardening and acceptance

Work:

- Run memory/CPU/battery measurements, long-session tests, offline tests, malformed stream tests, permission audits, and security review.
- Fix accessibility, low-power settings, crash recovery, log budgets, and documentation gaps.
- Freeze supported CLI versions and publish the device verification report.

Acceptance:

- The checklist in Section 10 passes on the Xiaomi 13 with no root runtime dependency, and all known limitations are written in `README.md`.
- No unresolved high-severity token leak, process orphan, path traversal, or rollback issue remains.

Test strategy: two independent end-to-end passes from SSH and from the Phosh UI, plus a 24-hour idle/active soak with log rotation enabled.

## 10. Acceptance checklist

### Functional

- [ ] New and existing sessions work for Codex, Claude Code, and DSH.
- [ ] Streaming messages, tool calls/results, diffs, approvals, usage, errors, and done states render correctly.
- [ ] Workspace selection is path-confined and persists per session.
- [ ] Codex review/apply and slash commands have explicit confirmation where required.
- [ ] Cancel, close/reopen, resume, continue, and abandoned-run recovery work.

### On-device and UI

- [ ] Runs on Xiaomi 13 fuxi, Ubuntu 26.10 arm64, Phosh, and Wayland using the installed WebKitGTK.
- [ ] Portrait, landscape, desktop-sized, touch, keyboard, focus, and accessibility flows pass.
- [ ] Shell and history open offline; provider/network failure states are actionable.
- [ ] No DSH Web UI is embedded or required.

### Config and logs

- [ ] Provider profiles, model catalogs, per-agent bindings, revisions, backups, and cc-switch import work.
- [ ] Codex TOML, Claude settings/env, and DSH env updates are atomic and preserve unrelated settings.
- [ ] Config/database/secret directories have expected ownership and modes.
- [ ] Raw and normalized JSONL logs, rotation, filters, and redacted export work.
- [ ] A scan of logs, exports, journal, process arguments, and crash output finds no token.

### Runtime and operations

- [ ] Normal runtime uses `fuxi`, not root; no privileged service is required.
- [ ] At most the configured number of agent processes run concurrently; cancellation leaves no orphan.
- [ ] Idle service stops idle resources without stopping active turns or approvals.
- [ ] Install, upgrade, rollback, and uninstall are inspectable over SSH.
- [ ] Baseline and active RAM/CPU/launch-time measurements are recorded; set release thresholds from M0 and enforce them in M5.

## 11. Risks and mitigations

| Risk | Impact | Mitigation and release gate |
|---|---|---|
| npm package lacks a working arm64 binary or postinstall fails | A required agent cannot run | Pin known-good versions; install as target user; preflight on arm64; keep previous version; do not ship an x86 substitution |
| CLI JSON schema changes between versions | Lost messages, wrong approvals, or crashes | Version-gated adapters, fixture corpus, bounded unknown-event handling, health check, and compatibility matrix |
| Tokens leak through raw streams, env dumps, exceptions, or exports | Credential compromise | Never log env/argv secrets; central redaction before persistence; masked UI; file modes; leak tests over logs/journal/exports |
| PTY/ANSI parsing introduces corruption | Incorrect tool/diff/approval UI | Use structured JSON streams as the backbone; keep PTY support out of core release |
| WebKitGTK memory or rendering cost is high | Poor battery/performance on phone | Bundle local assets, cap DOM/event history, throttle stream updates, disable expensive animations, measure with and without compositing, and retain GTK-only fallback |
| Resume semantics differ or are unavailable | Duplicate or lost context | Persist provider ids and cursors; expose resume vs continuation distinctly; never claim DSH resume; keep transcript/workspace recovery |
| Provider is offline or custom base URL is incompatible | Turns fail or hang | Explicit health checks/timeouts, no silent billable retries, queued/offline state, per-provider wire API, cancel path |
| Session process survives UI crash | Battery/process leak | Process groups, startup orphan scan, user service supervision, durable cancellation, and M1 crash tests |
| Workspace path escapes through symlink or `..` | Data disclosure or unintended edits | Resolve and verify realpath under configured roots for every read/write; refuse unsafe paths; test symlink escapes |
| SQLite/log growth consumes phone storage | App becomes unusable | Per-session/global budgets, rotation, bounded artifacts, free-space warning, export/delete controls |
| User-level systemd bus is unavailable | Launcher cannot start service | Run same-process fallback, diagnose `XDG_RUNTIME_DIR`, document optional linger, and never require root at runtime |
| Distro WebKit/GTK introspection API differs | App fails to launch | M0 capability probe, small compatibility shim, clear doctor output, and GTK-only fallback plan |

## 12. Verification commands and evidence to retain

The implementation team should retain a redacted evidence bundle for each release containing:

- `packaging/manifest-arm64.txt` with package versions and checksums.
- `doctor` output showing architecture, Python GI versions, WebKitGTK, Node/npm, all three CLIs, XDG paths, and service state.
- A session export demonstrating streaming message, tool call/result, diff, approval, usage, cancellation, and recovery.
- `systemctl --user status`, `journalctl --user -u superterm-agent.service`, process list, and file-mode listings captured over SSH.
- Before/after config hashes and a cc-switch import report with tokens masked.
- Idle/active RAM, CPU, launch-time, and 24-hour soak results.

Release only after the evidence bundle can be inspected from the host without opening a graphical browser or requiring root.
