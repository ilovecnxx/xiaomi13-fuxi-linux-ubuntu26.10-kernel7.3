"""Secret redaction applied before ANY persistence (raw stream, normalized events,
logs, exports). Never let a configured token or bearer-like secret reach disk.
"""
from __future__ import annotations

import re
from typing import Iterable

# Generic API-key-ish patterns (defensive; must not over-match ordinary text).
_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),                 # OpenAI/DeepSeek style
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}\b"),             # Anthropic
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),        # Authorization: Bearer xxx
    re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+"),  # auth headers
    re.compile(r"(?i)(x-api-key[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+"),
]

REPLACEMENT = "<REDACTED>"


class Redactor:
    def __init__(self, extra_secrets: Iterable[str] = ()):
        self._extra = [s for s in extra_secrets if s and len(s) >= 6]

    def set_secrets(self, extra_secrets: Iterable[str]) -> None:
        """Replace the configured-secret set (e.g. after a token is added/imported)."""
        self._extra = [s for s in extra_secrets if s and len(s) >= 6]

    def add_secret(self, secret: str) -> None:
        if secret and len(secret) >= 6 and secret not in self._extra:
            self._extra.append(secret)

    def redact(self, text: str) -> tuple[str, bool]:
        """Return (redacted_text, changed)."""
        if not text:
            return text, False
        changed = False
        out = text
        # Explicit configured secrets first (longest first to avoid partial overlap).
        for s in sorted(self._extra, key=len, reverse=True):
            if s in out:
                out = out.replace(s, REPLACEMENT)
                changed = True
        for pat in _PATTERNS:
            new = pat.sub(lambda m: (m.group(1) + REPLACEMENT) if pat.groups else REPLACEMENT, out)
            if new != out:
                changed = True
                out = new
        return out, changed

    def redact_obj(self, obj):
        """Recursively redact strings in dict/list/scalar structures."""
        if isinstance(obj, str):
            return self.redact(obj)[0]
        if isinstance(obj, dict):
            return {k: self.redact_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.redact_obj(v) for v in obj]
        return obj


def mask_token(token: str) -> str:
    """For display: show only last four characters."""
    if not token:
        return ""
    if len(token) <= 4:
        return "****"
    return "****" + token[-4:]
