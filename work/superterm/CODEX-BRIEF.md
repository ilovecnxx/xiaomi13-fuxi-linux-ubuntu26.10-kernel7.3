# Task brief for Codex: author a detailed development plan doc

You are Codex acting as architect/reviewer. Produce a DETAILED, concrete, step-by-step
development plan as Markdown, saved by the caller. Do not write code; only the plan.

## Product
A native "super-smart terminal" mobile APP on Xiaomi 13 running mainline Linux (Ubuntu 26.10 arm64 + Phosh).
The APP fully takes over the terminal/CLI display: instead of a black TUI, the user gets a
Codex-mobile-like chat + development experience (chat bubbles, streaming tool-call cards,
diffs, approvals), plus desktop-class features. It must ALSO host CCSwitch-style model
provider config management + logs, and drive THREE agent CLIs natively installed on the phone:
`codex` (OpenAI Codex CLI), `claude` (Claude Code), `dsh` (DeepSeek Harness).

## Hard environment facts (verified)
- Phone: Xiaomi 13 fuxi, mainline Linux 7.3.0+, Ubuntu 26.10 arm64, 10GB RAM, 8 cores, 89GB free, Phosh + WebKitGTK, only `epiphany` as browser, no chromium.
- Node v24.19.0, npm 11.16.0 already installed on phone.
- `dsh` 0.2.1-rc.4 already installed on phone (node CLI, has web profile).
- `codex` (Codex CLI) and `claude` (Claude Code) NOT yet on phone; must be installed natively
  (npm: @openai/codex, @anthropic-ai/claude-code; arm64 binaries).
- Host has codex 0.164.0 + claude 2.1.312, both configured to use custom base_url providers
  managed by `cc-switch` (/usr/bin/cc-switch, SQLite DB ~/.cc-switch/cc-switch.db, JSON
  catalog, logs, backups). Auth = provider base_url + bearer token (NOT official OAuth login).
- Verified non-interactive structured output (the integration backbone):
  - Codex: `codex exec --json --skip-git-repo-check -C <dir> "<prompt>"` emits JSONL events
    (thread.started, turn.started, item.completed with agent_message / command / file_change /
    error, turn.completed with usage). Also `codex exec resume`, `codex review`, `codex apply`.
  - Claude: `claude -p --output-format stream-json --input-format stream-json --verbose
    --dangerously-skip-permissions` emits/accepts streaming JSON events; supports `--resume`,
    `--continue`, `--permission-mode`, `--json-schema`, `--agents`.
  - DSH: `dsh headless "<task>"` one-shot; `dsh web`; ACP layer present (dsh-acp).
- User decision: build the APP **independent/self-developed** (does NOT embed DSH Web UI);
  it directly drives the CLI subprocesses and renders their output natively.
- User decision: **reproduce cc-switch functionality inside the APP** (provider/model profiles,
  switching, per-provider token, logs) rather than depending on host cc-switch.

## Constraints (must be respected)
- Only modify Linux partitions; the APP runs entirely in userspace on Ubuntu. No vendor kernel/HAL.
- Must run as normal user (fuxi), no root needed at runtime.
- Mobile-first UI, offline-capable shell, low memory (10GB shared with desktop).
- Must be installable/inspectable over SSH from the host for verification.

## What the plan must cover (be concrete, with phases, file layout, APIs, risks)
1. **Tech stack choice** for the self-built app given: Phosh(GTK/Wayland), WebKitGTK available,
   epiphany-only, Node 24 present, Python3 present. Compare candidates (e.g. GTK4/libadwaita
   Python; WebKitGTK + local HTML/JS + Node backend; Electron-like via Node+WebKitGTK; a PTY
   daemon + local webview). Recommend ONE with justification and a fallback.
2. **Agent driver layer**: how to spawn and stream codex exec --json / claude stream-json / dsh
   headless, normalize their different JSON schemas into ONE internal event model
   (message / tool_call / tool_result / diff / approval_request / usage / error / done).
   Session persistence, resume, cancellation, concurrency.
3. **Chat + dev UX**: message list, streaming markdown, tool-call cards, diff viewer, file
   viewer, approval prompts, slash commands, per-session workspace dir selection.
4. **CCSwitch-style config subsystem**: provider profiles (name, base_url, wire_api, token,
   models), which provider each agent CLI uses, write-through to ~/.codex/config.toml,
   ~/.claude/settings.json env, dsh env; SQLite or JSON store; import from host cc-switch DB;
   secret handling (never printed to logs; 0600).
5. **Logs subsystem**: structured per-session JSONL logs, raw CLI stream capture, log viewer,
   rotation, export.
6. **Install/packaging**: npm global installs for codex/claude on arm64, .desktop icon, launch
   entry, systemd --user unit, idle handling (mirror existing dsh-web patterns in
   work/dsh-phone/), rollback.
7. **Phased milestones** (M0 recon→M1 driver core→M2 chat UI→M3 config/logs→M4 install+deploy
   →M5 harden/accept), each with acceptance criteria and test strategy on real device.
8. **Acceptance checklist** Codex will use: functional, on-device, logs/config, no-root,
   resource use, rollback.
9. **Risks & mitigations** (arm64 binary availability, token secrecy, PTY/ANSI fragility avoided
   by JSON streaming, WebKitGTK perf, session resume semantics differences, offline).

Output: a single Markdown document. Keep it actionable for an implementer; prefer tables and
explicit file paths under a repo dir named `work/superterm/`. Aim for thorough but not padded.
