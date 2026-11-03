"""Base adapter: spawn a CLI subprocess, stream stdout/stderr lines, normalize.

The base handles: process group lifecycle, line streaming, raw capture, cancellation
(SIGTERM then SIGKILL), and event emission via an async callback.  Provider
subclasses supply the command and a normalizer.
"""
from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .. import models as M
from ..redaction import Redactor
from .normalize import JsonlReader, normalizer_for

EventSink = Callable[[M.Event], Awaitable[None]]
RawSink = Callable[[str, str], Awaitable[None]]  # (stream, line)


@dataclass
class RunResult:
    status: str = "completed"       # completed | cancelled | error
    exit_code: Optional[int] = None
    error: Optional[str] = None


@dataclass
class AgentSpec:
    provider: str
    cwd: str
    prompt: str
    model: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)
    resume_id: Optional[str] = None
    permission_mode: str = "bypass"   # bypass | ask
    timeout_s: Optional[float] = None


class BaseDriver:
    provider = "base"

    def __init__(self, redactor: Optional[Redactor] = None) -> None:
        self._redactor = redactor or Redactor()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._cancelled = False
        self._final_done_sent = False

    # ---- to be provided by subclasses ----
    def build_argv(self, spec: AgentSpec) -> list[str]:
        raise NotImplementedError

    def build_env(self, spec: AgentSpec) -> dict[str, str]:
        env = dict(os.environ)
        env.update(spec.env)
        return env

    # ---- main entry ----
    async def run(self, spec: AgentSpec, emit: EventSink, raw: Optional[RawSink] = None) -> RunResult:
        argv = self.build_argv(spec)
        env = self.build_env(spec)
        norm = normalizer_for(self.provider)
        reader = JsonlReader()

        emitted: list[M.Event] = []
        self._final_done_sent = False

        async def _emit(evs: list[M.Event]) -> None:
            for e in evs:
                # swallow provider-side done events; base emits the single authoritative one
                if e.kind == M.KIND_DONE and not self._final_done_sent:
                    continue
                e.provider = self.provider
                e.data = self._redactor.redact_obj(e.data)
                emitted.append(e)
                await emit(e)

        await _emit([M.ev_lifecycle("process_starting", argv0=argv[0] if argv else "")])
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=spec.cwd if spec.cwd else None,
                env=env,
                stdin=asyncio.subprocess.DEVNULL if not self._stdin_needed() else asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            await _emit([M.ev_error(f"executable not found: {argv[0] if argv else '?'}", fatal=True)])
            done = M.ev_done("error")
            done.provider = self.provider
            done.data = self._redactor.redact_obj(done.data)
            self._final_done_sent = True
            await emit(done)
            return RunResult(status="error", error=str(e))
        except Exception as e:  # noqa: BLE001
            await _emit([M.ev_error(f"failed to start: {e}", fatal=True)])
            done = M.ev_done("error")
            done.provider = self.provider
            done.data = self._redactor.redact_obj(done.data)
            self._final_done_sent = True
            await emit(done)
            return RunResult(status="error", error=str(e))

        await _emit([M.ev_lifecycle("process_started", pid=self._proc.pid)])

        async def handle_stream(stream, stream_name: str) -> None:
            assert stream is not None
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip("\n")
                if raw is not None:
                    red, _ = self._redactor.redact(text)
                    await raw(stream_name, red)
                for kind, payload in reader.feed_text(text + "\n"):
                    if kind == "json":
                        await _emit(norm.feed(payload))
                    else:
                        # non-JSON line: capture as system message only on stdout
                        if stream_name == "stdout" and payload.strip():
                            await _emit([M.ev_message(payload, role="system")])

        out_task = asyncio.create_task(handle_stream(self._proc.stdout, "stdout"))
        err_task = asyncio.create_task(handle_stream(self._proc.stderr, "stderr"))

        try:
            # Clamp defensively: a direct caller could pass 0/None; the turn must still be
            # bounded so a hung provider can never hang forever.
            eff_timeout = M.effective_timeout(spec.timeout_s)
            rc = await asyncio.wait_for(self._proc.wait(), timeout=eff_timeout)
        except asyncio.TimeoutError:
            await self._terminate()
            rc = self._proc.returncode if self._proc else -1
            self._cancelled = True
        finally:
            for kind, payload in reader.flush():
                if kind == "json":
                    await _emit(norm.feed(payload))
            await asyncio.gather(out_task, err_task, return_exceptions=True)

        # For dsh (text-based), flush a done record.
        if self.provider == "dsh":
            await _emit(norm.feed({"type": "done", "status": "cancelled" if self._cancelled else "completed",
                                   "exit_code": rc}))

        status = "cancelled" if self._cancelled else ("completed" if rc == 0 else "error")
        # The base is the single authority for the terminal `done`.
        await _emit([M.ev_lifecycle("process_exited", exit_code=rc)])
        self._final_done_sent = True
        await _emit([M.ev_done(status, exit_code=rc)])
        return RunResult(status=status, exit_code=rc)

    def _stdin_needed(self) -> bool:
        return False

    async def _terminate(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(50):  # up to ~5s
            if proc.returncode is not None:
                return
            await asyncio.sleep(0.1)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass

    async def cancel(self) -> None:
        self._cancelled = True
        await self._terminate()
