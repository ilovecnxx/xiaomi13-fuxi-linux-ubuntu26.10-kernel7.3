"""Claude Code adapter: drives `claude -p --output-format stream-json`.

Uses streaming JSON.  Auth/base_url are passed via environment
(ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN) so the token never appears on argv.
"""
from __future__ import annotations

from .base import AgentSpec, BaseDriver


class ClaudeDriver(BaseDriver):
    provider = "claude"

    def build_argv(self, spec: AgentSpec) -> list[str]:
        argv = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
        if spec.permission_mode == "bypass":
            argv.append("--dangerously-skip-permissions")
        if spec.model:
            argv += ["--model", spec.model]
        if spec.resume_id:
            argv += ["--resume", spec.resume_id]
        argv += list(spec.extra_args)
        # NOTE: do NOT use --add-dir here (it is variadic and would swallow the prompt).
        # The workspace is applied via the subprocess cwd instead.
        argv.append(spec.prompt)
        return argv
