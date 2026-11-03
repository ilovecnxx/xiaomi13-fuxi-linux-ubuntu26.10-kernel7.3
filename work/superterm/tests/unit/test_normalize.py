"""Unit tests for provider normalizers against real-shaped fixtures."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "src"))
sys.path.insert(0, SRC)

from superterm.drivers.normalize import (  # noqa: E402
    CodexNormalizer, ClaudeNormalizer, DshNormalizer, JsonlReader,
)
from superterm import models as M  # noqa: E402

FIX = os.path.join(HERE, "..", "integration")


def load_jsonl(name):
    out = []
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


class TestCodex(unittest.TestCase):
    def test_basic_flow(self):
        norm = CodexNormalizer()
        events = []
        for rec in load_jsonl("fixture_codex_basic.jsonl"):
            events.extend(norm.feed(rec))
        kinds = [e.kind for e in events]
        self.assertIn(M.KIND_TOOL_CALL, kinds)
        self.assertIn(M.KIND_TOOL_RESULT, kinds)
        self.assertIn(M.KIND_MESSAGE, kinds)
        self.assertIn(M.KIND_USAGE, kinds)
        self.assertEqual(kinds[-1], M.KIND_DONE)
        # tool result carries exit code + output
        tr = [e for e in events if e.kind == M.KIND_TOOL_RESULT][0]
        self.assertEqual(tr.data["exit_code"], 0)
        self.assertIn("hi", tr.data["output"])
        self.assertFalse(tr.data["is_error"])

    def test_command_failure_is_error(self):
        norm = CodexNormalizer()
        norm.feed({"type": "thread.started", "thread_id": "t"})
        evs = norm.feed({"type": "item.completed", "item": {
            "id": "c1", "type": "command_execution", "command": "false",
            "aggregated_output": "boom", "exit_code": 1, "status": "completed"}})
        tr = [e for e in evs if e.kind == M.KIND_TOOL_RESULT][0]
        self.assertTrue(tr.data["is_error"])

    def test_unknown_item_not_lost(self):
        norm = CodexNormalizer()
        evs = norm.feed({"type": "item.completed", "item": {"id": "x", "type": "mystery", "foo": 1}})
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].kind, M.KIND_MESSAGE)


class TestClaude(unittest.TestCase):
    def test_assistant_text_and_tool(self):
        norm = ClaudeNormalizer()
        events = []
        events.extend(norm.feed({"type": "system", "subtype": "task_started", "session_id": "s1", "task_id": "k1"}))
        events.extend(norm.feed({"type": "assistant", "session_id": "s1", "message": {
            "content": [{"type": "text", "text": "Running it"},
                        {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "echo hi"}}],
            "usage": {"input_tokens": 5, "output_tokens": 2}}}))
        events.extend(norm.feed({"type": "user", "session_id": "s1", "message": {
            "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "hi", "is_error": False}]}}))
        events.extend(norm.feed({"type": "result", "session_id": "s1", "result": "ok",
                                 "usage": {"output_tokens": 2}}))
        kinds = [e.kind for e in events]
        self.assertIn(M.KIND_TOOL_CALL, kinds)
        self.assertIn(M.KIND_TOOL_RESULT, kinds)
        self.assertIn(M.KIND_MESSAGE, kinds)
        self.assertEqual(kinds[-1], M.KIND_DONE)
        self.assertEqual(norm.session_id, "s1")
        tc = [e for e in events if e.kind == M.KIND_TOOL_CALL][0]
        self.assertEqual(tc.data["name"], "Bash")
        self.assertEqual(tc.data["risk"], "shell")

    def test_tool_result_list_content_flattened(self):
        norm = ClaudeNormalizer()
        evs = norm.feed({"type": "user", "session_id": "s", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "is_error": True,
             "content": [{"type": "text", "text": "err line"}]}]}})
        tr = evs[0]
        self.assertEqual(tr.kind, M.KIND_TOOL_RESULT)
        self.assertEqual(tr.data["output"], "err line")
        self.assertTrue(tr.data["is_error"])


class TestDsh(unittest.TestCase):
    def test_text_then_done(self):
        norm = DshNormalizer()
        evs = []
        evs.extend(norm.feed({"type": "text", "text": "hello "}))
        evs.extend(norm.feed({"type": "text", "text": "world"}))
        evs.extend(norm.feed({"type": "done", "status": "completed", "exit_code": 0}))
        kinds = [e.kind for e in evs]
        self.assertIn(M.KIND_MESSAGE, kinds)
        self.assertEqual(kinds[-1], M.KIND_DONE)
        msg = [e for e in evs if e.kind == M.KIND_MESSAGE][0]
        self.assertEqual(msg.data["text"], "hello world")


class TestJsonlReader(unittest.TestCase):
    def test_partial_lines(self):
        r = JsonlReader()
        got = list(r.feed_text('{"a":1}\n{"b":'))
        self.assertEqual(got[0], ("json", {"a": 1}))
        got2 = list(r.feed_text('2}\n'))
        self.assertEqual(got2[0], ("json", {"b": 2}))

    def test_garbage_line(self):
        r = JsonlReader()
        got = list(r.feed_text("not json\n"))
        self.assertEqual(got[0][0], "raw")


if __name__ == "__main__":
    unittest.main()
