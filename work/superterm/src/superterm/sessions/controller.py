"""Session controller: orchestrates agent drivers, persistence, and event streaming.

The controller is provider-agnostic; it selects a driver per session, runs turns,
persists normalized events, and exposes a simple async API the UI bridge calls.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .. import models as M
from ..drivers import driver_for
from ..drivers.base import AgentSpec
from ..redaction import Redactor
from .store import Store

PushSink = Callable[[dict[str, Any]], Awaitable[None]]

# A turn must never hang indefinitely: if the provider stops responding, the driver is
# terminated and the turn ends with exactly one done. Overridable per turn.
DEFAULT_TURN_TIMEOUT_S = M.DEFAULT_TURN_TIMEOUT_S


@dataclass
class ActiveTurn:
    session_id: str
    turn_id: str
    driver: Any
    task: asyncio.Task


class Controller:
    def __init__(self, store: Optional[Store] = None, redactor: Optional[Redactor] = None) -> None:
        self.store = store or Store()
        self.redactor = redactor or Redactor()
        self._active: dict[str, ActiveTurn] = {}   # session_id -> active turn
        self._push: Optional[PushSink] = None
        self._lock = asyncio.Lock()

    def set_push(self, sink: PushSink) -> None:
        self._push = sink

    async def _emit(self, ev: M.Event, persist: bool = True) -> None:
        # Defense in depth: scrub secrets again at the persistence boundary, regardless
        # of which layer produced the event (driver already redacts; this catches any
        # other event source and is idempotent).
        try:
            ev.data = self.redactor.redact_obj(ev.data)
        except Exception:  # noqa: BLE001
            pass
        if persist:
            self.store.append_event(ev)
        if self._push is not None:
            # A push failure must NEVER corrupt turn lifecycle: the event is already
            # durably persisted, and the UI can re-sync by cursor (session_open /
            # logs_query). Swallow push errors so they cannot be mistaken for a driver
            # crash and trigger a second terminal done.
            try:
                await self._push({"protocol_version": M.PROTOCOL_VERSION, "kind": "event",
                                  "cursor": ev.seq, "payload": ev.to_dict()})
            except Exception:  # noqa: BLE001
                pass

    # ---------- session ops ----------
    def create_session(self, provider: str, workspace: str, title: str = "",
                       model: Optional[str] = None, approval_mode: str = "bypass"):
        return self.store.create_session(provider, workspace, title, model, approval_mode)

    def list_sessions(self, limit: int = 100, offset: int = 0):
        return self.store.list_sessions(limit, offset)

    def open_session(self, sid: str, after_seq: int = 0):
        sess = self.store.get_session(sid)
        if not sess:
            raise KeyError(f"no session {sid}")
        return sess, self.store.events_after(sid, after_seq)

    # ---------- run a turn ----------
    async def start_turn(self, sid: str, prompt: str, model: Optional[str] = None,
                         env: Optional[dict[str, str]] = None,
                         resume: bool = False,
                         timeout_s: Optional[float] = None) -> str:
        sess = self.store.get_session(sid)
        if not sess:
            raise KeyError(f"no session {sid}")
        async with self._lock:
            if sid in self._active:
                raise RuntimeError("a turn is already running for this session")
            tid = self.store.create_turn(sid, prompt)
            self.store.update_session(sid, status="running")
            driver = driver_for(sess.provider, redactor=self.redactor)
            spec = AgentSpec(
                provider=sess.provider,
                cwd=sess.workspace,
                prompt=prompt,
                model=model or sess.provider_model,
                env=env or {},
                resume_id=sess.provider_session_id if resume else None,
                permission_mode=sess.approval_mode,
                # 0/negative must NOT disable the timeout (round-7 Codex): fall back to the
                # safe default so a hung provider can never hang a turn forever.
                timeout_s=M.effective_timeout(timeout_s),
            )
            raw = self.store.raw_sink(sid)

            async def run_wrapper():
                # Exactly-once terminal-done guard for this turn.
                done_persisted = False

                # tag every event with session/turn ids
                async def emit(ev: M.Event):
                    nonlocal done_persisted
                    ev.session_id = sid
                    ev.turn_id = tid
                    await self._emit(ev)
                    if ev.kind == M.KIND_DONE:
                        done_persisted = True
                    # capture provider session id from lifecycle/meta if present
                    if ev.kind == M.KIND_LIFECYCLE and ev.data.get("thread_id"):
                        self.store.update_session(sid, provider_session_id=ev.data["thread_id"])

                from ..drivers.base import RunResult
                res = RunResult(status="error")
                try:
                    try:
                        res = await driver.run(spec, emit, raw)
                        if not isinstance(res, RunResult):
                            res = RunResult(status="completed", exit_code=0)
                    except Exception as e:  # noqa: BLE001
                        # Only inject a terminal done if the driver did not already persist
                        # one; otherwise we would emit a duplicate terminal event.
                        try:
                            await emit(M.ev_error(f"driver crashed: {e}", fatal=True))
                        except Exception:  # noqa: BLE001
                            pass
                        if not done_persisted:
                            try:
                                await emit(M.ev_done("error"))
                            except Exception:  # noqa: BLE001
                                pass
                        res = RunResult(status="error", error=str(e))
                finally:
                    # Turn lifecycle must always be finalized, even if an emitter failed.
                    # Retry for transient SQLite busy/locked errors; if the DB is genuinely
                    # unwritable, journal the intended final status so recover_orphans()
                    # restores it at next start (a turn is never permanently 'running').
                    finalized = False
                    for _attempt in range(3):
                        try:
                            self.store.finish_turn(tid, res.status, res.exit_code)
                            finalized = True
                            break
                        except Exception:  # noqa: BLE001
                            continue
                    if not finalized:
                        try:
                            self.store.record_recovery(tid, sid, res.status, res.exit_code)
                        except Exception:  # noqa: BLE001
                            pass
                    for _attempt in range(3):
                        try:
                            self.store.update_session(sid, status="active")
                            break
                        except Exception:  # noqa: BLE001
                            continue
                    async with self._lock:
                        self._active.pop(sid, None)

            task = asyncio.create_task(run_wrapper())
            self._active[sid] = ActiveTurn(sid, tid, driver, task)
        return tid

    async def cancel(self, sid: str) -> bool:
        at = self._active.get(sid)
        if not at:
            return False
        await at.driver.cancel()
        try:
            await asyncio.wait_for(at.task, timeout=10)
        except asyncio.TimeoutError:
            pass
        return True

    def is_running(self, sid: str) -> bool:
        return sid in self._active
