"""Typed domain objects and the normalized event envelope (schema_version 1).

Every agent (codex / claude / dsh) is normalized into the same event model so the
UI and log layer stay provider-agnostic.  See tests/device/m0-report.md for the
frozen contract.
"""
from __future__ import annotations

import math
import os

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1

# Event kinds
KIND_MESSAGE = "message"
KIND_TOOL_CALL = "tool_call"
KIND_TOOL_RESULT = "tool_result"
KIND_DIFF = "diff"
KIND_APPROVAL_REQUEST = "approval_request"
KIND_USAGE = "usage"
KIND_ERROR = "error"
KIND_DONE = "done"
KIND_LIFECYCLE = "lifecycle"

KINDS = {
    KIND_MESSAGE, KIND_TOOL_CALL, KIND_TOOL_RESULT, KIND_DIFF,
    KIND_APPROVAL_REQUEST, KIND_USAGE, KIND_ERROR, KIND_DONE, KIND_LIFECYCLE,
}

# Providers
PROVIDERS = ("codex", "claude", "dsh")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Event:
    """A single normalized event.

    `seq` is a monotonically increasing per-session counter assigned by the
    session store; 0 means not yet persisted.
    """
    kind: str
    data: dict[str, Any]
    provider: str = ""
    session_id: str = ""
    turn_id: str = ""
    event_id: str = field(default_factory=new_id)
    schema_version: int = SCHEMA_VERSION
    ts: str = field(default_factory=_now_iso)
    seq: int = 0
    raw_ref: Optional[str] = None
    redactions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown event kind: {self.kind!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "provider": self.provider,
            "kind": self.kind,
            "ts": self.ts,
            "seq": self.seq,
            "data": self.data,
            "raw_ref": self.raw_ref,
            "redactions": self.redactions,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(
            kind=d["kind"], data=d.get("data", {}), provider=d.get("provider", ""),
            session_id=d.get("session_id", ""), turn_id=d.get("turn_id", ""),
            event_id=d.get("event_id") or new_id(), schema_version=d.get("schema_version", SCHEMA_VERSION),
            ts=d.get("ts", _now_iso()), seq=d.get("seq", 0), raw_ref=d.get("raw_ref"),
            redactions=list(d.get("redactions") or []),
        )


# ---- typed payload builders (keep payload shape consistent across adapters) ----

def ev_message(text: str, *, role: str = "assistant", stream_id: Optional[str] = None,
               delta: bool = False, **extra: Any) -> Event:
    data = {"role": role, "text": text}
    if stream_id:
        data["stream_id"] = stream_id
    if delta:
        data["delta"] = True
    data.update(extra)
    return Event(KIND_MESSAGE, data)


def ev_tool_call(call_id: str, name: str, arguments: Any, *, cwd: Optional[str] = None,
                 risk: str = "normal", **extra: Any) -> Event:
    data = {"call_id": call_id, "name": name, "arguments": arguments, "risk": risk}
    if cwd:
        data["cwd"] = cwd
    data.update(extra)
    return Event(KIND_TOOL_CALL, data)


def ev_tool_result(call_id: str, output: Any, *, exit_code: Optional[int] = None,
                   is_error: bool = False, **extra: Any) -> Event:
    data = {"call_id": call_id, "output": output, "is_error": is_error}
    if exit_code is not None:
        data["exit_code"] = exit_code
    data.update(extra)
    return Event(KIND_TOOL_RESULT, data)


def ev_diff(path: str, patch: str, *, status: str = "modified", **extra: Any) -> Event:
    data = {"path": path, "patch": patch, "status": status,
            "added": patch.count("\n+") if patch else 0,
            "removed": patch.count("\n-") if patch else 0}
    data.update(extra)
    return Event(KIND_DIFF, data)


def ev_error(message: str, *, fatal: bool = False, **extra: Any) -> Event:
    data = {"message": message, "fatal": fatal}
    data.update(extra)
    return Event(KIND_ERROR, data)


def ev_usage(usage: dict[str, Any]) -> Event:
    return Event(KIND_USAGE, dict(usage))


def ev_done(status: str = "completed", *, exit_code: Optional[int] = None, **extra: Any) -> Event:
    data = {"status": status}
    if exit_code is not None:
        data["exit_code"] = exit_code
    data.update(extra)
    return Event(KIND_DONE, data)


def ev_lifecycle(state: str, **extra: Any) -> Event:
    data = {"state": state}
    data.update(extra)
    return Event(KIND_LIFECYCLE, data)


def to_jsonable(ev: Event) -> dict[str, Any]:
    return ev.to_dict()


# ---- turn timeouts ----
# A turn must never hang indefinitely. This is the canonical default; the controller and all
# drivers clamp any missing/non-positive value to it, so even direct driver callers are safe.
DEFAULT_TURN_TIMEOUT_S = 900.0
# Hard ceiling so no input (including an absurd one) can effectively disable the timeout.
MAX_TURN_TIMEOUT_S = 7 * 24 * 3600.0


def _sanitize(v) -> float:
    """Return a finite, positive timeout capped at MAX_TURN_TIMEOUT_S, or DEFAULT."""
    try:
        v = float(v)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_TURN_TIMEOUT_S
    if not math.isfinite(v) or v <= 0:
        return DEFAULT_TURN_TIMEOUT_S
    return min(v, MAX_TURN_TIMEOUT_S)


def default_turn_timeout() -> float:
    """The safe default turn timeout (env-configurable at call time)."""
    return _sanitize(os.environ.get("SUPERTERM_TURN_TIMEOUT", DEFAULT_TURN_TIMEOUT_S))


def effective_timeout(timeout_s) -> float:
    """Normalize a requested timeout so the result is ALWAYS finite and bounded.

    None / 0 / negative / NaN / +inf / non-numeric -> the safe default; finite positives are
    capped at MAX_TURN_TIMEOUT_S. This guarantees no input can disable the timeout.
    """
    if timeout_s is None:
        return default_turn_timeout()
    try:
        v = float(timeout_s)
    except (TypeError, ValueError, OverflowError):
        return default_turn_timeout()
    if not math.isfinite(v) or v <= 0:
        return default_turn_timeout()
    return min(v, MAX_TURN_TIMEOUT_S)
