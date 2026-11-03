"""Regression tests for the acceptance (Codex) blocking findings."""
from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import unittest
from pathlib import Path

from superterm import models as M
from superterm.redaction import Redactor
from superterm.sessions.store import Store
from superterm.drivers.base import BaseDriver, AgentSpec, RunResult
from superterm.config import writers


class TestRedactionAtPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "state")
        self.store = Store(db_path=Path(self.tmp) / "t.db")
        # secret that does NOT match generic sk-* patterns
        self.tok = "customsecrettokenZZZ123456"
        self.store.set_redactor(Redactor([self.tok]))

    def tearDown(self):
        self.store.close()

    def test_prompt_redacted_in_turns(self):
        s = self.store.create_session("codex", "/tmp")
        self.store.create_turn(s.id, f"please use {self.tok} now")
        row = self.store._conn.execute("SELECT prompt FROM turns WHERE session_id=?", (s.id,)).fetchone()
        self.assertNotIn(self.tok, row[0])
        self.assertIn("<REDACTED>", row[0])

    def test_event_payload_redacted(self):
        s = self.store.create_session("codex", "/tmp")
        ev = M.ev_message(f"token={self.tok}", role="assistant")
        ev.session_id = s.id   # REQUIRED: otherwise events_after() returns [] and the
        ev.turn_id = "t1"      # assertion below would pass vacuously (Codex round 2).
        self.store.append_event(ev)
        got = self.store.events_after(s.id, 0)
        self.assertTrue(got, "expected the event to be persisted for this session")
        self.assertTrue(all(self.tok not in json.dumps(e) for e in got),
                        "token leaked into persisted event payload")

    def test_raw_sink_redacted(self):
        s = self.store.create_session("codex", "/tmp")
        sink = self.store.raw_sink(s.id)
        asyncio.run(sink("stdout", json.dumps({"leak": self.tok})))
        d = Path(self.tmp) / "state" / "superterm" / "logs" / s.id / "raw-codex.jsonl"
        self.assertNotIn(self.tok, d.read_text())


class _FailDriver(BaseDriver):
    provider = "codex"

    async def _run(self, spec, emit, raw):  # not used; base.run is called
        pass


class TestDoneOnStartupFailure(unittest.TestCase):
    def test_missing_executable_emits_one_done(self):
        drv = _FailDriver()
        events = []

        async def emit(e):
            events.append(e)

        spec = AgentSpec(provider="codex", cwd="/tmp", prompt="hi",
                         model=None, env={}, resume_id=None, permission_mode="bypass")
        # Force argv to a non-existent binary by overriding _build_argv if present;
        # else patch _argv. BaseDriver.run builds argv via self._build_argv/_argv.
        orig = getattr(drv, "build_argv", None)

        def boom(s):
            return ["/nonexistent/binary/xyz", "exec"]
        drv.build_argv = boom  # type: ignore[attr-defined]
        asyncio.run(drv.run(spec, emit))
        dones = [e for e in events if e.kind == M.KIND_DONE]
        self.assertEqual(len(dones), 1, f"expected exactly 1 done, got {len(dones)}")
        self.assertEqual(dones[0].data.get("status"), "error")


def run_turn_to_completion(ctrl, sid, prompt="hi", timeout_s=5.0, turn_timeout_s=None):
    """Drive one turn to completion INSIDE a single event loop.

    Round-4 Codex: the controller creates the turn task on the running loop, so start_turn
    and the polling MUST share one loop -- never asyncio.run(start_turn()) then poll in a
    second asyncio.run().
    """
    async def go():
        await ctrl.start_turn(sid, prompt, timeout_s=turn_timeout_s)
        deadline = asyncio.get_running_loop().time() + timeout_s
        while ctrl.is_running(sid):
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("turn did not finish in time")
            await asyncio.sleep(0.01)
    asyncio.run(go())


class TestExactlyOnceUnderFailure(unittest.TestCase):
    """Codex round 2: a push failure on the done event, or a driver that raises AFTER
    emitting done, must never produce two terminal done events, and the turn must always
    be finalized (finish_turn + active-turn cleanup)."""

    def _setup(self):
        tmp = tempfile.mkdtemp()
        os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
        store = Store(db_path=Path(tmp) / "t.db")
        from superterm.sessions.controller import Controller
        ctrl = Controller(store)
        sess = store.create_session("codex", "/tmp")
        return store, ctrl, sess

    def _count_dones(self, store, sid):
        return sum(1 for e in store.all_events(sid) if e.get("kind") == M.KIND_DONE)


    def test_driver_emits_done_then_raises_no_duplicate(self):
        import superterm.sessions.controller as C

        class Drv:
            provider = "codex"

            async def run(self, spec, emit, raw):
                await emit(M.ev_done("completed"))
                raise RuntimeError("boom after done")

            async def cancel(self):
                return True

        store, ctrl, sess = self._setup()
        orig = C.driver_for
        C.driver_for = lambda p, redactor=None: Drv()
        try:
            run_turn_to_completion(ctrl, sess.id)
            self.assertEqual(self._count_dones(store, sess.id), 1,
                             "driver emitted done then raised: must not duplicate done")
            self.assertFalse(ctrl.is_running(sess.id), "active turn must be cleaned up")
        finally:
            C.driver_for = orig
            store.close()

    def test_jsonl_mirror_failure_does_not_duplicate_done(self):
        """Round-3 Codex: SQLite is authoritative; a failure writing the derived JSONL
        transcript must not fail the event write nor duplicate the terminal done."""
        import superterm.sessions.controller as C

        class Drv:
            provider = "codex"

            async def run(self, spec, emit, raw):
                await emit(M.ev_done("completed"))
                return RunResult(status="completed", exit_code=0)

            async def cancel(self):
                return True

        store, ctrl, sess = self._setup()
        # Break the JSONL mirror at its source: _write_jsonl is best-effort and must absorb.
        store._session_log_path = lambda sid: (_ for _ in ()).throw(OSError("disk full"))
        orig = C.driver_for
        C.driver_for = lambda p, redactor=None: Drv()
        try:
            run_turn_to_completion(ctrl, sess.id)
            self.assertEqual(self._count_dones(store, sess.id), 1,
                             "JSONL mirror failure must not duplicate done")
            row = store._conn.execute("SELECT status FROM turns WHERE session_id=?",
                                      (sess.id,)).fetchone()
            self.assertNotEqual(row[0], "running", "turn must still be finalized")
        finally:
            C.driver_for = orig
            store.close()

    def test_sqlite_failure_still_finalizes_turn(self):
        """Even if event persistence fails hard, the turn must not stay 'running'."""
        import superterm.sessions.controller as C

        class Drv:
            provider = "codex"

            async def run(self, spec, emit, raw):
                await emit(M.ev_message("hi", role="assistant"))
                return RunResult(status="completed", exit_code=0)

            async def cancel(self):
                return True

        store, ctrl, sess = self._setup()
        real_append = store.append_event
        calls = {"n": 0}

        def flaky_append(ev):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("sqlite locked")
            return real_append(ev)
        store.append_event = flaky_append
        orig = C.driver_for
        C.driver_for = lambda p, redactor=None: Drv()
        try:
            run_turn_to_completion(ctrl, sess.id)
            self.assertFalse(ctrl.is_running(sess.id))
            row = store._conn.execute("SELECT status FROM turns WHERE session_id=?",
                                      (sess.id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertNotEqual(row[0], "running", "turn must be finalized despite SQLite error")
            self.assertEqual(store.get_session(sess.id).status, "active")
        finally:
            C.driver_for = orig
            store.close()

    def test_push_failure_does_not_duplicate_done_or_skip_finalize(self):
        import superterm.sessions.controller as C

        class Drv:
            provider = "codex"

            async def run(self, spec, emit, raw):
                await emit(M.ev_message("hello", role="assistant"))
                await emit(M.ev_done("completed"))
                return RunResult(status="completed", exit_code=0)

            async def cancel(self):
                return True

        store, ctrl, sess = self._setup()

        async def bad_push(msg):
            raise RuntimeError("push channel down")

        ctrl.set_push(bad_push)
        orig = C.driver_for
        C.driver_for = lambda p, redactor=None: Drv()
        try:
            run_turn_to_completion(ctrl, sess.id)
            self.assertEqual(self._count_dones(store, sess.id), 1,
                             "push failure must not duplicate the done event")
            self.assertFalse(ctrl.is_running(sess.id),
                             "push failure must not skip active-turn cleanup")
            row = store._conn.execute("SELECT status FROM turns WHERE session_id=?", (sess.id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertNotEqual(row[0], "running", "turn must be finalized despite push failure")
            self.assertEqual(store.get_session(sess.id).status, "active")
        finally:
            C.driver_for = orig
            store.close()


class TestFinalizeDurabilityAndBootRecovery(unittest.TestCase):
    """Round-4 Codex: a persistently unwritable DB must not leave a turn permanently
    'running'. Failed finalize journals the intended status; recover_orphans() (run at
    App boot) restores it, and any leftover 'running' row is swept."""

    def _setup(self):
        tmp = tempfile.mkdtemp()
        os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
        store = Store(db_path=Path(tmp) / "t.db")
        sess = store.create_session("codex", "/tmp")
        tid = store.create_turn(sess.id, "hi")
        store.update_session(sess.id, status="running")
        return store, sess, tid

    def test_journal_replay_restores_intended_status(self):
        store, sess, tid = self._setup()
        # simulate: DB finalize failed permanently -> journal the intended outcome only
        store.record_recovery(tid, sess.id, "completed", 0)
        store.recover_orphans()
        row = store._conn.execute("SELECT status,exit_code FROM turns WHERE id=?", (tid,)).fetchone()
        self.assertEqual(row[0], "completed", "journal must restore the intended final status")
        self.assertEqual(row[1], 0)
        self.assertEqual(store.get_session(sess.id).status, "active")
        store.close()

    def test_leftover_running_turn_is_swept(self):
        store, sess, tid = self._setup()   # no journal entry at all
        store.recover_orphans()
        row = store._conn.execute("SELECT status FROM turns WHERE id=?", (tid,)).fetchone()
        self.assertNotEqual(row[0], "running", "leftover running turn must be swept")
        self.assertEqual(store.get_session(sess.id).status, "active")
        store.close()

    def test_persistent_finalize_failure_is_journaled_then_healed(self):
        """End-to-end: finish_turn always fails -> controller journals the intended status
        -> the turn is healed at next start (never permanently 'running')."""
        import superterm.sessions.controller as C
        from superterm.drivers.base import RunResult

        tmp = tempfile.mkdtemp()
        os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
        store = Store(db_path=Path(tmp) / "t.db")
        sess = store.create_session("codex", "/tmp")

        class Drv:
            provider = "codex"

            async def run(self, spec, emit, raw):
                await emit(M.ev_message("hi", role="assistant"))
                return RunResult(status="completed", exit_code=0)

            async def cancel(self):
                return True

        real_finish = store.finish_turn
        def broken_finish(tid, status, exit_code=None):
            raise RuntimeError("disk read-only")
        store.finish_turn = broken_finish
        ctrl = C.Controller(store)
        orig = C.driver_for
        C.driver_for = lambda p, redactor=None: Drv()
        try:
            run_turn_to_completion(ctrl, sess.id)
        finally:
            C.driver_for = orig

        # turn is still 'running' in the DB (finalize impossible) ...
        row = store._conn.execute("SELECT status FROM turns WHERE session_id=?", (sess.id,)).fetchone()
        self.assertEqual(row[0], "running")
        # ... but the intended outcome was durably journaled
        jpath = store.recovery_journal_path()
        self.assertTrue(jpath.exists(), "failed finalize must be journaled for recovery")
        journal = jpath.read_text(encoding="utf-8")
        self.assertIn("completed", journal)

        # next boot reconciles it to the intended final status
        store.finish_turn = real_finish
        store.recover_orphans()
        row = store._conn.execute("SELECT status FROM turns WHERE session_id=?", (sess.id,)).fetchone()
        self.assertEqual(row[0], "completed", "boot recovery must restore intended status")
        self.assertEqual(store.get_session(sess.id).status, "active")
        store.close()

    def test_app_boot_reconciles_stuck_turn(self):
        store, sess, tid = self._setup()
        store.record_recovery(tid, sess.id, "error", 1)
        from superterm.app import App
        app = App(store=store)            # App.__init__ must run recovery
        try:
            row = store._conn.execute("SELECT status FROM turns WHERE id=?", (tid,)).fetchone()
            self.assertNotEqual(row[0], "running", "App boot must reconcile stuck turns")
            self.assertEqual(row[0], "error")
        finally:
            app.config.close()


class TestTurnTimeout(unittest.TestCase):
    """A provider that never responds must be terminated by the per-turn timeout, emitting
    exactly one terminal done and leaving the turn finalized (no indefinite hang)."""

    def test_hung_provider_is_terminated_with_one_done(self):
        import time as _time
        import superterm.sessions.controller as C
        from superterm.drivers.base import BaseDriver

        class HungDriver(BaseDriver):
            provider = "codex"

            def build_argv(self, spec):
                return ["sleep", "60"]

        tmp = tempfile.mkdtemp()
        os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
        store = Store(db_path=Path(tmp) / "t.db")
        sess = store.create_session("codex", "/tmp")
        ctrl = C.Controller(store)
        orig = C.driver_for
        C.driver_for = lambda p, redactor=None: HungDriver(redactor=redactor)
        t0 = _time.time()
        try:
            run_turn_to_completion(ctrl, sess.id, timeout_s=30, turn_timeout_s=0.8)
        finally:
            C.driver_for = orig
        elapsed = _time.time() - t0
        self.assertLess(elapsed, 15, "hung provider must be terminated by the turn timeout")
        dones = [e for e in store.all_events(sess.id) if e.get("kind") == M.KIND_DONE]
        self.assertEqual(len(dones), 1, "terminated turn must emit exactly one done")
        self.assertEqual(dones[0]["data"].get("status"), "cancelled")
        row = store._conn.execute("SELECT status FROM turns WHERE session_id=?", (sess.id,)).fetchone()
        self.assertNotEqual(row[0], "running", "turn must be finalized after timeout")
        self.assertFalse(ctrl.is_running(sess.id))
        store.close()


class TestTimeoutPlumbing(unittest.TestCase):
    """Round-7 Codex: cover the DEFAULT timeout and every entry point's plumbing
    (controller / App / headless / bridge), and ensure 0 or negative cannot disable it."""

    def _capture_specs(self):
        """Patch driver_for to record the AgentSpec it receives, returning fast."""
        import superterm.sessions.controller as C
        import superterm.drivers as D

        captured = []

        class FastDrv:
            provider = "codex"

            async def run(self, spec, emit, raw):
                captured.append(spec)
                await emit(M.ev_message("ok", role="assistant"))
                await emit(M.ev_done("completed"))
                return RunResult(status="completed", exit_code=0)

            async def cancel(self):
                return True

        orig = C.driver_for
        C.driver_for = lambda p, redactor=None: FastDrv()
        return captured, (lambda: setattr(C, "driver_for", orig))

    def _mk_app(self):
        import tempfile as _tf
        tmp = _tf.mkdtemp()
        os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")
        from superterm.app import App
        return App()

    def test_default_timeout_applied(self):
        from superterm.sessions.controller import DEFAULT_TURN_TIMEOUT_S
        captured, restore = self._capture_specs()
        app = self._mk_app()
        try:
            sess = app.create_session("codex", "/tmp")
            run_turn_to_completion(app.controller, sess.id)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].timeout_s, DEFAULT_TURN_TIMEOUT_S,
                             "no explicit timeout must use the safe default")
        finally:
            restore(); app.close()

    def test_zero_and_negative_cannot_disable_timeout(self):
        from superterm.sessions.controller import DEFAULT_TURN_TIMEOUT_S
        for bad in (0, -1):
            captured, restore = self._capture_specs()
            app = self._mk_app()
            try:
                sess = app.create_session("codex", "/tmp")
                run_turn_to_completion(app.controller, sess.id, turn_timeout_s=bad)
                self.assertEqual(captured[0].timeout_s, DEFAULT_TURN_TIMEOUT_S,
                                 f"timeout_s={bad} must not disable the timeout")
            finally:
                restore(); app.close()

    def test_app_start_turn_forwards_timeout(self):
        captured, restore = self._capture_specs()
        app = self._mk_app()
        try:
            sess = app.create_session("codex", "/tmp")

            async def go():
                await app.start_turn(sess.id, "hi", timeout_s=7)
                for _ in range(500):
                    if not app.controller.is_running(sess.id):
                        return
                    await asyncio.sleep(0.01)
            asyncio.run(go())
            self.assertEqual(captured[0].timeout_s, 7)
        finally:
            restore(); app.close()

    def test_bridge_turn_start_forwards_timeout(self):
        import json as _json
        from superterm.bridge import Bridge
        captured, restore = self._capture_specs()
        app = self._mk_app()
        try:
            br = Bridge(app)
            sess = app.create_session("codex", "/tmp")

            async def go():
                r = await br.handle(_json.dumps({"protocol_version": 1, "request_id": "1",
                                                 "method": "turn_start",
                                                 "params": {"session_id": sess.id, "prompt": "hi",
                                                            "timeout_s": 9}}))
                body = _json.loads(r)
                self.assertTrue(body["ok"], body)
                for _ in range(500):
                    if not app.controller.is_running(sess.id):
                        return
                    await asyncio.sleep(0.01)
            asyncio.run(go())
            self.assertEqual(captured[0].timeout_s, 9)
        finally:
            restore(); app.close()

    def test_headless_run_forwards_timeout(self):
        captured, restore = self._capture_specs()
        app = self._mk_app()
        try:
            import contextlib
            import io
            from superterm import headless
            with contextlib.redirect_stdout(io.StringIO()):
                rc = headless.cmd_run(app, "codex", "hi", "/tmp", None, 11.0)
            self.assertEqual(rc, 0)
            self.assertEqual(captured[0].timeout_s, 11.0)
        finally:
            restore(); app.close()


class TestDirectDriverTimeoutClamp(unittest.TestCase):
    """Round-8 Codex: even a DIRECT driver caller passing timeout_s=0/None/negative must get a
    bounded turn (the clamp lives in the driver, not only in the controller)."""

    def setUp(self):
        self._old = os.environ.get("SUPERTERM_TURN_TIMEOUT")
        os.environ["SUPERTERM_TURN_TIMEOUT"] = "0.8"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SUPERTERM_TURN_TIMEOUT", None)
        else:
            os.environ["SUPERTERM_TURN_TIMEOUT"] = self._old

    def _spec(self, timeout_s):
        return AgentSpec(provider="codex", cwd="/tmp", prompt="hi", model=None,
                         env={}, resume_id=None, permission_mode="bypass",
                         timeout_s=timeout_s)

    def test_effective_timeout_normalization(self):
        self.assertEqual(M.effective_timeout(None), 0.8)
        self.assertEqual(M.effective_timeout(0), 0.8)
        self.assertEqual(M.effective_timeout(-5), 0.8)
        self.assertEqual(M.effective_timeout(3), 3)
        # round-9 Codex: non-finite / absurd inputs must never yield an unbounded timeout
        for bad in (float("inf"), float("-inf"), float("nan"), "abc"):
            self.assertEqual(M.effective_timeout(bad), 0.8, f"{bad!r} must fall back to default")
        # round-10 Codex: huge int literals raise OverflowError from float(); must be caught
        # and fall back to the *default* (here 0.8 via SUPERTERM_TURN_TIMEOUT), not merely to
        # "some finite value" -- asserting the exact value proves the OverflowError path ran.
        for huge in (10 ** 10000, -(10 ** 10000)):
            self.assertEqual(M.effective_timeout(huge), 0.8,
                             "huge int must fall back to the default timeout")
        self.assertEqual(M.effective_timeout(1e12), M.MAX_TURN_TIMEOUT_S,
                         "absurd finite values must be capped")
        self.assertTrue(math.isfinite(M.effective_timeout(float("inf"))))

    def test_base_driver_direct_call_with_zero_timeout_is_bounded(self):
        import time as _time
        from superterm.drivers.base import BaseDriver

        class HungDriver(BaseDriver):
            provider = "codex"

            def build_argv(self, spec):
                return ["sleep", "60"]

        for bad in (0, None, -1):
            drv = HungDriver()
            events = []

            async def emit(e):
                events.append(e)

            t0 = _time.time()
            asyncio.run(drv.run(self._spec(bad), emit))
            elapsed = _time.time() - t0
            self.assertLess(elapsed, 20, f"timeout_s={bad} must still bound the turn")
            dones = [e for e in events if e.kind == M.KIND_DONE]
            self.assertEqual(len(dones), 1, f"timeout_s={bad}: exactly one done")

    def test_dsh_driver_direct_call_is_bounded_for_bad_timeouts(self):
        import time as _time
        import superterm.drivers.dsh as dshmod

        # Minimal fake dsh: a subprocess that never finishes.
        for bad in (0, None, -1):
            drv = dshmod.DshDriver()
            drv.build_argv = lambda spec: ["sleep", "60"]  # type: ignore[attr-defined]
            events = []

            async def emit(e):
                events.append(e)

            t0 = _time.time()
            asyncio.run(drv.run(self._spec(bad), emit))
            elapsed = _time.time() - t0
            self.assertLess(elapsed, 20, f"dsh driver must bound timeout_s={bad}")
            dones = [e for e in events if e.kind == M.KIND_DONE]
            self.assertEqual(len(dones), 1, f"dsh timeout_s={bad}: exactly one done")

    def test_controller_still_uses_default(self):
        self.assertEqual(M.effective_timeout(None), 0.8)


class TestTimeoutEnvSanitization(unittest.TestCase):
    """Round-11 Codex: the ENV path (`_sanitize` via default_turn_timeout) must also be
    covered directly, including huge/non-finite values and objects whose __float__ overflows."""

    def setUp(self):
        self._old = os.environ.get("SUPERTERM_TURN_TIMEOUT")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SUPERTERM_TURN_TIMEOUT", None)
        else:
            os.environ["SUPERTERM_TURN_TIMEOUT"] = self._old

    def test_env_values_are_sanitized(self):
        for bad in ("inf", "-inf", "nan", "abc", "0", "-5", "1" * 10000):
            os.environ["SUPERTERM_TURN_TIMEOUT"] = bad
            got = M.default_turn_timeout()
            self.assertEqual(got, M.DEFAULT_TURN_TIMEOUT_S,
                             f"env={bad[:10]}... must fall back to the default")
            self.assertTrue(math.isfinite(got) and got > 0)

    def test_env_finite_values_are_capped(self):
        os.environ["SUPERTERM_TURN_TIMEOUT"] = "1e12"
        self.assertEqual(M.default_turn_timeout(), M.MAX_TURN_TIMEOUT_S)
        os.environ["SUPERTERM_TURN_TIMEOUT"] = "12"
        self.assertEqual(M.default_turn_timeout(), 12.0)

    def test_sanitize_directly_covers_every_branch(self):
        """Round-12 Codex: `_sanitize` is the shared normalizer (used by the env path); its
        OverflowError branch must be exercised DIRECTLY, not only via effective_timeout."""
        class Boom:
            def __float__(self):
                raise OverflowError("boom")

        self.assertEqual(M._sanitize(Boom()), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M._sanitize(10 ** 10000), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M._sanitize(-(10 ** 10000)), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M._sanitize("inf"), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M._sanitize("nan"), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M._sanitize("abc"), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M._sanitize(0), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M._sanitize(-5), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M._sanitize(None), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M._sanitize(5), 5.0)
        self.assertEqual(M._sanitize("7"), 7.0)
        self.assertEqual(M._sanitize(1e12), M.MAX_TURN_TIMEOUT_S)

    def test_object_raising_overflow_is_handled(self):
        class Boom:
            def __float__(self):
                raise OverflowError("boom")

        class BoomValue:
            def __float__(self):
                raise ValueError("bad")

        os.environ.pop("SUPERTERM_TURN_TIMEOUT", None)
        self.assertEqual(M.effective_timeout(Boom()), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M.effective_timeout(BoomValue()), M.DEFAULT_TURN_TIMEOUT_S)
        self.assertEqual(M.default_turn_timeout(), M.DEFAULT_TURN_TIMEOUT_S)


class TestPushEnvelope(unittest.TestCase):
    def test_push_has_protocol_version(self):
        from superterm.sessions.controller import Controller
        tmp = tempfile.mkdtemp()
        os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
        store = Store(db_path=Path(tmp) / "t.db")
        ctrl = Controller(store)
        pushes = []

        async def push(msg):
            pushes.append(msg)
        ctrl.set_push(push)
        asyncio.run(ctrl._emit(M.ev_message("x", role="assistant")))
        self.assertTrue(pushes)
        self.assertEqual(pushes[0].get("protocol_version"), M.PROTOCOL_VERSION)
        store.close()


class TestCodexTomlMerge(unittest.TestCase):
    def test_preserves_unrelated_table_keys(self):
        tmp = tempfile.mkdtemp()
        p = Path(tmp) / "config.toml"
        p.write_text(
            'model = "old"\nmodel_provider = "old"\n\n[other]\nmodel = "keep-me"\nfoo = 1\n\n'
            '[model_providers.custom]\nname="x"\nbase_url="https://old/v1"\n')
        writers.write_codex_config("P", "https://new/v1", "responses", "newmodel", config_path=p)
        txt = p.read_text()
        self.assertIn('model = "keep-me"', txt, "unrelated [other].model must be preserved")
        self.assertIn("https://new/v1", txt)
        self.assertIn('model = "newmodel"', txt)
        self.assertNotIn("https://old/v1", txt)


if __name__ == "__main__":
    unittest.main()
