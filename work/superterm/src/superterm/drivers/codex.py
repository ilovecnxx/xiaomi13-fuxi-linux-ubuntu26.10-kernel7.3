"""Codex adapter: drives `codex exec --json` and `codex exec resume`.

Uses structured JSONL, never the interactive TUI.  Provider/env materialization
(API key, base_url) is written by the config writer into ~/.codex/config.toml and
passed here as process env to keep tokens off argv.
"""
from __future__ import annotations

from .base import AgentSpec, BaseDriver


class CodexDriver(BaseDriver):
    provider = "codex"

    def build_argv(self, spec: AgentSpec) -> list[str]:
        argv = ["codex", "exec", "--json", "--skip-git-repo-check"]
        if spec.model:
            argv += ["-m", spec.model]
        if spec.cwd:
            argv += ["-C", spec.cwd]
        if spec.resume_id:
            argv += ["resume", spec.resume_id]
        argv += list(spec.extra_args)
        # Prompt last; read from stdin (DEVNULL unless we pass as arg). Use arg form.
        argv.append(spec.prompt)
        return argv

    def build_env(self, spec: AgentSpec) -> dict[str, str]:
        env = super().build_env(spec)
        # Codex reads provider config from ~/.codex/config.toml; key from env var
        # OPENAI_API_KEY if the config uses requires_openai_auth, else embedded.
        return env
