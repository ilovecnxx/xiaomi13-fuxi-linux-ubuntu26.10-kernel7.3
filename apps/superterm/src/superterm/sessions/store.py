"""Session / turn / event persistence in SQLite + JSONL raw and normalized logs.

Design:
- SQLite `superterm.db` holds metadata (sessions, turns, events index) for fast
  querying and recovery.  Events store the normalized envelope as JSON.
- Per-session JSONL files under logs/<session>/events.jsonl (normalized) and
  raw-<provider>.jsonl (exact redacted stream lines) are the durable transcript.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from .. import models as M
from .. import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  title TEXT,
  workspace TEXT,
  provider_session_id TEXT,
  provider_model TEXT,
  approval_mode TEXT DEFAULT 'bypass',
  status TEXT DEFAULT 'active',
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  prompt TEXT,
  status TEXT DEFAULT 'running',
  created_at REAL,
  updated_at REAL,
  exit_code INTEGER
);
CREATE TABLE IF NOT EXISTS events (
  session_id TEXT NOT NULL,
  turn_id TEXT,
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  event_id TEXT,
  ts TEXT,
  payload TEXT,
  PRIMARY KEY (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
CREATE TABLE IF NOT EXISTS process_runs (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  turn_id TEXT,
  pid INTEGER,
  status TEXT,
  started_at REAL,
  ended_at REAL
);
"""


@dataclass
class SessionRow:
    id: str
    provider: str
    title: str
    workspace: str
    provider_session_id: Optional[str]
    provider_model: Optional[str]
    approval_mode: str
    status: str
    created_at: float
    updated_at: float


class Store:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        paths.ensure_dirs()
        self.db_path = str(db_path or paths.db_path())
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','1')")
        self._conn.commit()
        paths.restrict_file(self.db_path, 0o600)
        # Redactor for defense-in-depth: ensure prompts/events/raw never hit disk with
        # a plaintext token, even if an upstream layer forgot to scrub.
        self._redactor = None

    def set_redactor(self, redactor) -> None:
        self._redactor = redactor

    def _scrub(self, s: str) -> str:
        if not s or self._redactor is None:
            return s
        try:
            return self._redactor.redact(s)[0]
        except Exception:  # noqa: BLE001
            return s

    # ---------- sessions ----------
    def create_session(self, provider: str, workspace: str, title: str = "",
                       model: Optional[str] = None, approval_mode: str = "bypass") -> SessionRow:
        sid = M.new_id()
        now = time.time()
        self._conn.execute(
            "INSERT INTO sessions(id,provider,title,workspace,provider_session_id,provider_model,"
            "approval_mode,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, provider, title or f"New {provider} session", workspace, None, model,
             approval_mode, "active", now, now))
        self._conn.commit()
        return self.get_session(sid)  # type: ignore[return-value]

    def get_session(self, sid: str) -> Optional[SessionRow]:
        row = self._conn.execute(
            "SELECT id,provider,title,workspace,provider_session_id,provider_model,"
            "approval_mode,status,created_at,updated_at FROM sessions WHERE id=?", (sid,)).fetchone()
        return SessionRow(*row) if row else None

    def list_sessions(self, limit: int = 100, offset: int = 0) -> list[SessionRow]:
        rows = self._conn.execute(
            "SELECT id,provider,title,workspace,provider_session_id,provider_model,"
            "approval_mode,status,created_at,updated_at FROM sessions "
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [SessionRow(*r) for r in rows]

    def update_session(self, sid: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ",".join(f"{k}=?" for k in fields)
        self._conn.execute(f"UPDATE sessions SET {cols} WHERE id=?", (*fields.values(), sid))
        self._conn.commit()

    # ---------- turns ----------
    def create_turn(self, sid: str, prompt: str) -> str:
        tid = M.new_id()
        now = time.time()
        safe_prompt = self._scrub(prompt)
        self._conn.execute("INSERT INTO turns(id,session_id,prompt,status,created_at,updated_at) "
                           "VALUES(?,?,?,?,?,?)", (tid, sid, safe_prompt, "running", now, now))
        self._conn.commit()
        return tid

    def finish_turn(self, tid: str, status: str, exit_code: Optional[int] = None) -> None:
        self._conn.execute("UPDATE turns SET status=?,updated_at=?,exit_code=? WHERE id=?",
                           (status, time.time(), exit_code, tid))
        self._conn.commit()

    # ---------- events ----------
    def next_seq(self, sid: str) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE session_id=?", (sid,)).fetchone()
        return (row[0] or 0) + 1

    def append_event(self, ev: M.Event) -> M.Event:
        if ev.seq == 0:
            ev.seq = self.next_seq(ev.session_id)
        # Scrub again at the persistence boundary (idempotent; protects any event source).
        if self._redactor is not None:
            try:
                ev.data = self._redactor.redact_obj(ev.data)
            except Exception:  # noqa: BLE001
                pass
        d = ev.to_dict()
        self._conn.execute(
            "INSERT OR REPLACE INTO events(session_id,turn_id,seq,kind,event_id,ts,payload) "
            "VALUES(?,?,?,?,?,?,?)",
            (ev.session_id, ev.turn_id, ev.seq, ev.kind, ev.event_id, ev.ts,
             json.dumps(d, ensure_ascii=False)))
        self._conn.commit()
        self._write_jsonl(ev.session_id, d)
        return ev

    def _write_jsonl(self, sid: str, d: dict[str, Any]) -> None:
        """Best-effort derived transcript.

        The SQLite row is the authoritative record (the UI re-syncs from it by cursor), so a
        failure writing the human-readable JSONL mirror must NOT fail the event write — that
        would break the exactly-once-done invariant (round-3 Codex finding) and could skip
        turn finalization.
        """
        try:
            d = dict(d)
            d["_ts"] = time.time()
            line = json.dumps(d, ensure_ascii=False)
            with self._session_log_path(sid).open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass

    def _session_log_path(self, sid: str) -> Path:
        d = paths.logs_dir() / sid
        d.mkdir(parents=True, exist_ok=True)
        return d / "events.jsonl"

    def raw_sink(self, sid: str):
        """Return an async raw-line sink writing to raw-<provider>.jsonl."""
        sess = self.get_session(sid)
        prov = sess.provider if sess else "agent"
        d = paths.logs_dir() / sid
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"raw-{prov}.jsonl"
        paths.restrict_file(path, 0o600)

        async def sink(stream: str, line: str) -> None:
            rec = {"ts": time.time(), "stream": stream, "line": self._scrub(line)}
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        return sink

    def events_after(self, sid: str, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
            (sid, after_seq, limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def all_events(self, sid: str) -> Iterator[dict[str, Any]]:
        for (payload,) in self._conn.execute(
                "SELECT payload FROM events WHERE session_id=? ORDER BY seq", (sid,)):
            yield json.loads(payload)

    # ---------- process runs / recovery ----------
    def register_run(self, run_id: str, sid: str, tid: str, pid: int) -> None:
        self._conn.execute("INSERT OR REPLACE INTO process_runs(id,session_id,turn_id,pid,status,"
                           "started_at) VALUES(?,?,?,?,?,?)",
                           (run_id, sid, tid, pid, "running", time.time()))
        self._conn.commit()

    def end_run(self, run_id: str, status: str) -> None:
        self._conn.execute("UPDATE process_runs SET status=?,ended_at=? WHERE id=?",
                           (status, time.time(), run_id))
        self._conn.commit()

    # ---------- crash / failed-finalize recovery ----------
    def recovery_journal_path(self) -> Path:
        return paths.state_dir() / "recovery.jsonl"

    def record_recovery(self, turn_id: str, session_id: str, status: str,
                        exit_code: Optional[int] = None) -> None:
        """Journal the *intended* final status of a turn whose DB finalize failed.

        Best-effort append (never raises). Replayed by recover_orphans() on next start, so
        even a persistent write failure can never leave a turn permanently 'running'.
        """
        try:
            rec = {"ts": time.time(), "turn_id": turn_id, "session_id": session_id,
                   "status": status, "exit_code": exit_code}
            p = self.recovery_journal_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())   # journal must survive a crash/reboot
                except OSError:
                    pass
            try:
                paths.restrict_file(p, 0o600)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

    def _read_recovery_journal(self) -> list[dict[str, Any]]:
        try:
            p = self.recovery_journal_path()
            if not p.exists():
                return []
            out = []
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return out
        except Exception:  # noqa: BLE001
            return []

    def recover_orphans(self) -> int:
        """Reconcile turns/sessions left running by a previous (or crashed) process.

        Guarantee: a turn is never *permanently* stuck in 'running'. First replay the
        recovery journal to restore each turn's intended exact final status, then sweep any
        remaining 'running' rows (turns -> 'interrupted', sessions -> 'active').

        Idempotent and safe to call at every start.
        """
        n = 0
        try:
            for rec in self._read_recovery_journal():
                tid = rec.get("turn_id")
                if not tid:
                    continue
                status = rec.get("status") or "interrupted"
                cur = self._conn.execute(
                    "UPDATE turns SET status=?,updated_at=?,exit_code=? "
                    "WHERE id=? AND status='running'",
                    (status, time.time(), rec.get("exit_code"), tid))
                n += max(cur.rowcount, 0)
            cur = self._conn.execute(
                "UPDATE turns SET status='interrupted',updated_at=? WHERE status='running'",
                (time.time(),))
            n += max(cur.rowcount, 0)
            cur = self._conn.execute(
                "UPDATE process_runs SET status='abandoned',ended_at=? WHERE status='running'",
                (time.time(),))
            self._conn.execute("UPDATE sessions SET status='active' WHERE status='running'")
            self._conn.commit()
        except Exception:  # noqa: BLE001
            try:
                self._conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        return n

    def close(self) -> None:
        self._conn.close()
