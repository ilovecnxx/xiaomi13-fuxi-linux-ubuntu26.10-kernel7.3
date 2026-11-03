from .base import BaseDriver, AgentSpec, RunResult  # noqa: F401
from .codex import CodexDriver  # noqa: F401
from .claude import ClaudeDriver  # noqa: F401
from .dsh import DshDriver  # noqa: F401


def driver_for(provider: str, redactor=None):
    # Pass the caller's redactor (carries all configured per-profile secrets) so both
    # normalized events and raw stream logs are scrubbed of real tokens.
    if provider == "codex":
        return CodexDriver(redactor=redactor)
    if provider == "claude":
        return ClaudeDriver(redactor=redactor)
    if provider == "dsh":
        return DshDriver(redactor=redactor)
    raise ValueError(f"unknown provider: {provider}")
