"""Integration tests for the subprocess driver using a fake codex-like agent.

Covers: streaming event production, exit codes, bad-JSON tolerance, and
cancellation leaving no orphan process.  Runs without any real network/CLI.
"""
import asyncio
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "src"))
sys.path.insert(0, SRC)

from superterm.drivers.base import BaseDriver, AgentSpec  # noqa: E402
from superterm.drivers.normalize import CodexNormalizer  # noqa: E402
from superterm import models as M  # noqa: E402


class FakeCodexDriver(BaseDriver):
    provider = "codex"

    def __init__(self, mode, redactor=None):
        super().__init__(redactor)
        self.mode = mode

    def build_argv(self, spec):
        return [sys.executable, os.path.join(HERE, "fake_agent.py"), self.mode]


def run(driver, spec, timeout=30):
    events = []
    raw = []

    async def emit(ev):
        events.append(ev)

    async def raw_sink(stream, line):
        raw.append((stream, line))

    res = asyncio.run(asyncio.wait_for(driver.run(spec, emit, raw_sink), timeout=timeout))
    return res, events, raw


class TestFakeDriver(unittest.TestCase):
    def test_normal_stream(self):
        d = FakeCodexDriver("normal")
        res, events, raw = run(d, AgentSpec("codex", HERE, "hi"))
        self.assertEqual(res.status, "completed")
        kinds = [e.kind for e in events]
        self.assertIn(M.KIND_TOOL_CALL, kinds)
        self.assertIn(M.KIND_TOOL_RESULT, kinds)
        self.assertIn(M.KIND_USAGE, kinds)
        self.assertEqual(kinds[-1], M.KIND_DONE)
        self.assertTrue(any(e.provider == "codex" for e in events))

    def test_slow_chunks_all_arrive(self):
        d = FakeCodexDriver("slow")
        res, events, _ = run(d, AgentSpec("codex", HERE, "hi"))
        texts = " ".join(e.data.get("text", "") for e in events if e.kind == M.KIND_MESSAGE)
        for i in range(3):
            self.assertIn(f"chunk {i}", texts)

    def test_bad_json_is_system_message(self):
        d = FakeCodexDriver("badjson")
        res, events, _ = run(d, AgentSpec("codex", HERE, "hi"))
        msgs = [e for e in events if e.kind == M.KIND_MESSAGE and e.data.get("role") == "system"]
        self.assertTrue(any("not json" in m.data.get("text", "") for m in msgs))

    def test_fail_exit_status(self):
        d = FakeCodexDriver("fail")
        res, events, _ = run(d, AgentSpec("codex", HERE, "hi"))
        self.assertEqual(res.status, "error")
        self.assertEqual(res.exit_code, 3)
        self.assertEqual([e.kind for e in events][-1], M.KIND_DONE)

    def test_cancel_no_orphan(self):
        d = FakeCodexDriver("hang")

        async def scenario():
            events = []

            async def emit(ev):
                events.append(ev)

            task = asyncio.create_task(d.run(AgentSpec("codex", HERE, "hi"), emit, None))
            await asyncio.sleep(0.6)  # let it start & emit the tool_call
            pid = d._proc.pid if d._proc else None
            await d.cancel()
            res = await asyncio.wait_for(task, timeout=10)
            # process must be gone
            orphan = False
            if pid:
                try:
                    os.kill(pid, 0)
                    orphan = True
                except ProcessLookupError:
                    orphan = False
            return res, orphan, events

        res, orphan, events = asyncio.run(scenario())
        self.assertFalse(orphan, "child process left running after cancel")
        self.assertEqual(res.status, "cancelled")


if __name__ == "__main__":
    unittest.main()
