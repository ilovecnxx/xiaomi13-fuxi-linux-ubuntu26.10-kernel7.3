"""Normalize provider-specific JSONL/JSON event streams into the shared Event model.

Each normalizer is a stateful object: feed it one parsed record at a time via
`.feed(record)` and it returns a list of Event objects (usually 0 or 1).  Some
records (e.g. codex item.started/complete) map to multiple events.

Design rule: never parse ANSI/TUI.  These operate only on structured JSON.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .. import models as M


class CodexNormalizer:
    """Maps `codex exec --json` JSONL records.

    Observed records:
      {"type":"thread.started","thread_id":...}
      {"type":"turn.started"}
      {"type":"item.started"|"item.completed","item":{id,type,...}}
      {"type":"turn.completed","usage":{...}}
      {"type":"error","message":...}  (some versions) / item type error
    item.type ∈ agent_message{text}, command_execution{command,aggregated_output,exit_code,status},
                file_change{...}, reasoning{text}, error{message}
    """
    provider = "codex"

    def __init__(self) -> None:
        self.thread_id: Optional[str] = None
        self._started_items: set[str] = set()

    def feed(self, rec: dict[str, Any]) -> list[M.Event]:
        t = rec.get("type")
        if t == "thread.started":
            self.thread_id = rec.get("thread_id")
            return [M.ev_lifecycle("thread_started", thread_id=self.thread_id)]
        if t == "turn.started":
            return [M.ev_lifecycle("turn_started")]
        if t in ("item.started", "item.completed"):
            item = rec.get("item") or {}
            return self._item(item, completed=(t == "item.completed"))
        if t == "turn.completed":
            usage = rec.get("usage") or {}
            return [M.ev_usage(usage), M.ev_done("completed")]
        if t == "error":
            return [M.ev_error(rec.get("message", "codex error"))]
        return []

    def _item(self, item: dict[str, Any], completed: bool) -> list[M.Event]:
        itype = item.get("type")
        iid = item.get("id", "")
        if itype == "agent_message":
            return [M.ev_message(item.get("text", ""))]
        if itype == "reasoning":
            return [M.ev_message(item.get("text", ""), role="reasoning")]
        if itype == "command_execution":
            cmd = item.get("command", "")
            if not completed:
                return [M.ev_tool_call(iid, "shell", {"command": cmd}, risk="shell")]
            out = item.get("aggregated_output", "")
            ec = item.get("exit_code")
            return [M.ev_tool_result(iid, out, exit_code=ec, is_error=(ec not in (0, None)))]
        if itype == "file_change":
            path = item.get("path") or item.get("file") or ""
            patch = item.get("diff") or item.get("patch") or item.get("unified_diff") or ""
            return [M.ev_diff(path, patch, status=item.get("status", "modified"))]
        if itype == "error":
            return [M.ev_error(item.get("message", "codex item error"), fatal=True)]
        # unknown item type -> surface as message so nothing is silently lost
        return [M.ev_message(json.dumps(item, ensure_ascii=False), role="system")]


class ClaudeNormalizer:
    """Maps `claude -p --output-format stream-json` records.

    Observed records (each carries session_id, uuid):
      {"type":"system","subtype":...}
      {"type":"assistant","message":{model,content:[blocks],usage:{...}}}
      {"type":"user","message":{content:[{type:"tool_result",tool_use_id,content,is_error}]}}
      {"type":"result", ...}
    assistant content block types: text{text}, thinking{thinking}, tool_use{id,name,input}
    """
    provider = "claude"

    def __init__(self) -> None:
        self.session_id: Optional[str] = None
        self._seen_tool_calls: set[str] = set()

    def feed(self, rec: dict[str, Any]) -> list[M.Event]:
        if rec.get("session_id"):
            self.session_id = rec["session_id"]
        t = rec.get("type")
        if t == "system":
            sub = rec.get("subtype", "")
            if sub == "task_started":
                return [M.ev_lifecycle("turn_started", task_id=rec.get("task_id"))]
            if sub == "task_notification":
                return [M.ev_lifecycle("task_notification", status=rec.get("status"),
                                       summary=rec.get("summary"))]
            if sub == "thinking_tokens":
                return [M.ev_usage({"thinking_tokens": rec.get("estimated_tokens")})] if rec.get("estimated_tokens") else []
            return []
        if t == "assistant":
            return self._assistant(rec.get("message") or {})
        if t == "user":
            return self._user(rec.get("message") or {})
        if t == "result":
            usage = rec.get("usage") or {}
            evs: list[M.Event] = []
            if usage:
                evs.append(M.ev_usage(usage))
            status = "completed"
            if rec.get("is_error") or rec.get("subtype") == "error":
                status = "error"
                evs.append(M.ev_error(str(rec.get("result") or rec.get("error") or "claude error"), fatal=True))
            evs.append(M.ev_done(status))
            return evs
        return []

    def _assistant(self, msg: dict[str, Any]) -> list[M.Event]:
        evs: list[M.Event] = []
        for block in msg.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                evs.append(M.ev_message(block.get("text", "")))
            elif btype == "thinking":
                txt = block.get("thinking", "")
                if txt:
                    evs.append(M.ev_message(txt, role="reasoning"))
            elif btype == "tool_use":
                cid = block.get("id", "")
                self._seen_tool_calls.add(cid)
                name = block.get("name", "tool")
                evs.append(M.ev_tool_call(cid, name, block.get("input", {}),
                                         risk="shell" if name in ("Bash", "shell") else "normal"))
        usage = msg.get("usage")
        if usage:
            evs.append(M.ev_usage(usage))
        return evs

    def _user(self, msg: dict[str, Any]) -> list[M.Event]:
        evs: list[M.Event] = []
        for block in msg.get("content") or []:
            if block.get("type") == "tool_result":
                cid = block.get("tool_use_id", "")
                content = block.get("content", "")
                if isinstance(content, list):
                    # content blocks -> flatten text
                    parts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(c.get("text", ""))
                        elif isinstance(c, str):
                            parts.append(c)
                    content = "\n".join(parts)
                evs.append(M.ev_tool_result(cid, content, is_error=bool(block.get("is_error"))))
        return evs


class DshNormalizer:
    """Maps `dsh headless` plain-text output to a minimal event stream.

    dsh headless is one-shot text (no structured JSON stream), so we synthesize a
    lifecycle, a single message, usage(unknown), and done.  Continuation is handled
    by the driver by appending prior transcript to the next prompt.
    """
    provider = "dsh"

    def __init__(self) -> None:
        self._buf: list[str] = []

    def feed(self, rec: dict[str, Any]) -> list[M.Event]:
        # records here are wrapper dicts produced by the dsh driver
        if rec.get("type") == "text":
            self._buf.append(rec.get("text", ""))
            return []
        if rec.get("type") == "done":
            text = "".join(self._buf).strip()
            evs = [M.ev_lifecycle("turn_started")]
            if text:
                evs.append(M.ev_message(text))
            evs.append(M.ev_done(rec.get("status", "completed"), exit_code=rec.get("exit_code")))
            return evs
        if rec.get("type") == "error":
            return [M.ev_error(rec.get("message", "dsh error"), fatal=True)]
        return []


def normalizer_for(provider: str):
    if provider == "codex":
        return CodexNormalizer()
    if provider == "claude":
        return ClaudeNormalizer()
    if provider == "dsh":
        return DshNormalizer()
    raise ValueError(f"unknown provider: {provider}")


class JsonlReader:
    """Incrementally split a byte/str stream into parsed JSON records.

    Tolerates partial lines and non-JSON garbage lines (yields ('bad', line)).
    """
    def __init__(self) -> None:
        self._buf = ""

    def feed_text(self, chunk: str):
        self._buf += chunk
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            yield self._parse(line)

    def flush(self):
        line = self._buf.strip()
        self._buf = ""
        if line:
            yield self._parse(line)

    @staticmethod
    def _parse(line: str):
        try:
            return ("json", json.loads(line))
        except json.JSONDecodeError:
            return ("raw", line)
