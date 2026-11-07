"""DSH adapter: drives `dsh headless "<task>"`.

dsh headless is one-shot text output with no structured stream / resume.  We wrap
its stdout as text events; the caller supplies a continuation prompt built from the
prior transcript when the user asks to "continue".
"""
from __future__ import annotations

import asyncio
import os

from .base import AgentSpec, BaseDriver


class DshDriver(BaseDriver):
    provider = "dsh"

    def build_argv(self, spec: AgentSpec) -> list[str]:
        argv = ["dsh", "headless"]
        if spec.model:
            argv += ["--model", spec.model]
        argv += list(spec.extra_args)
        argv.append(spec.prompt)
        return argv

    async def run(self, spec: AgentSpec, emit, raw=None):
        # dsh headless emits plain text on stdout, not JSONL.  Reuse the base loop
        # but feed text lines to the DshNormalizer as {"type":"text"} wrapper records.
        # We override _stdin_needed to False and rely on base text-capture: base treats
        # non-JSON stdout as system messages, but DshNormalizer expects wrappers. So we
        # rewrite the stream here for clarity.
        from .. import models as M
        from .normalize import DshNormalizer
        from ..redaction import Redactor

        norm = DshNormalizer()
        env = self.build_env(spec)
        argv = self.build_argv(spec)

        async def _emit(evs):
            for e in evs:
                e.provider = self.provider
                e.data = self._redactor.redact_obj(e.data)
                await emit(e)

        await _emit([M.ev_lifecycle("process_starting", argv0=argv[0])])
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv, cwd=spec.cwd or None, env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as e:  # noqa: BLE001
            await _emit([M.ev_error(f"failed to start dsh: {e}", fatal=True), M.ev_done("error")])
            from .base import RunResult
            return RunResult(status="error", error=str(e))

        async def pump(stream, label):
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip("\n")
                if raw is not None:
                    await raw(label, self._redactor.redact(text)[0])
                if label == "stdout":
                    await _emit(norm.feed({"type": "text", "text": text + "\n"}))
                else:
                    if text.strip():
                        await _emit(norm.feed({"type": "text", "text": "[stderr] " + text + "\n"}))

        t1 = asyncio.create_task(pump(self._proc.stdout, "stdout"))
        t2 = asyncio.create_task(pump(self._proc.stderr, "stderr"))
        try:
            eff_timeout = M.effective_timeout(spec.timeout_s)
            rc = await asyncio.wait_for(self._proc.wait(), timeout=eff_timeout)
        except asyncio.TimeoutError:
            await self._terminate()
            rc = self._proc.returncode if self._proc else -1
            self._cancelled = True
        await asyncio.gather(t1, t2, return_exceptions=True)
        # Flush buffered text as one message, then emit a single authoritative done.
        text = "".join(norm._buf).strip()
        if text:
            await _emit([M.ev_message(text)])
        status = "cancelled" if self._cancelled else ("completed" if rc == 0 else "error")
        await _emit([M.ev_lifecycle("process_exited", exit_code=rc)])
        await _emit([M.ev_done(status, exit_code=rc)])
        from .base import RunResult
        return RunResult(status=status, exit_code=rc)
