"""Tests for session store persistence and controller turn lifecycle (fake driver)."""
import asyncio
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "src"))
sys.path.insert(0, SRC)

from superterm.sessions.store import Store  # noqa: E402
from superterm import models as M  # noqa: E402


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # isolate state dir
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "state")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "config")
        self.store = Store()

    def test_create_and_get(self):
        s = self.store.create_session("codex", "/tmp/ws", "t")
        got = self.store.get_session(s.id)
        self.assertEqual(got.provider, "codex")
        self.assertEqual(got.workspace, "/tmp/ws")

    def test_event_seq_monotonic(self):
        s = self.store.create_session("claude", "/tmp", "")
        e1 = M.ev_message("a"); e1.session_id = s.id
        e2 = M.ev_message("b"); e2.session_id = s.id
        self.store.append_event(e1)
        self.store.append_event(e2)
        self.assertEqual(e1.seq, 1)
        self.assertEqual(e2.seq, 2)
        evs = self.store.events_after(s.id, 0)
        self.assertEqual([e["seq"] for e in evs], [1, 2])
        self.assertTrue(os.path.exists(os.path.join(
            os.environ["XDG_STATE_HOME"], "superterm", "logs", s.id, "events.jsonl")))

    def test_recover_orphans(self):
        s = self.store.create_session("codex", "/tmp", "")
        t = self.store.create_turn(s.id, "hi")
        self.store.register_run("r1", s.id, t, 1234)
        n = self.store.recover_orphans()
        self.assertEqual(n, 1)


class FakeDriver:
    def __init__(self, sid, events):
        self.sid = sid
        self.events = events

    async def run(self, spec, emit, raw):
        for ev in self.events:
            await emit(ev)

        class R:
            status = "completed"
            exit_code = 0
        return R()

    async def cancel(self):
        pass


class TestController(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "state")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "config")
        self.store = Store()

    def test_turn_persists_events(self):
        from superterm.sessions.controller import Controller
        ctrl = Controller(self.store)
        pushed = []

        async def push(msg):
            pushed.append(msg)

        ctrl.set_push(push)
        s = ctrl.create_session("codex", "/tmp", "t")
        # monkeypatch driver_for by injecting a fake via start_turn's driver creation
        # Simplest: patch superterm.sessions.controller.driver_for
        import superterm.sessions.controller as C

        class AsyncFake:
            async def run(self, spec, emit, raw):
                await emit(M.ev_message("hello"))
                await emit(M.ev_done("completed"))

            async def cancel(self):
                pass

        orig = C.driver_for
        C.driver_for = lambda p, redactor=None: AsyncFake()
        try:
            # Start AND wait inside ONE event loop: the controller schedules the turn task on
            # the running loop, so a second asyncio.run() would cancel it (round-5 Codex).
            async def go():
                tid = await ctrl.start_turn(s.id, "hi")
                deadline = asyncio.get_running_loop().time() + 5.0
                while ctrl.is_running(s.id):
                    if asyncio.get_running_loop().time() > deadline:
                        raise AssertionError("turn did not finish in time")
                    await asyncio.sleep(0.01)
                return tid
            tid = asyncio.run(go())
        finally:
            C.driver_for = orig
        evs = self.store.events_after(s.id, 0)
        self.assertTrue(any(e["kind"] == M.KIND_MESSAGE for e in evs))
        self.assertTrue(any(e["kind"] == M.KIND_DONE for e in evs))
        self.assertTrue(pushed)


if __name__ == "__main__":
    unittest.main()
