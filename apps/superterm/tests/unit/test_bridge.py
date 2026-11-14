"""Tests for the strict UI bridge protocol (no GUI required)."""
import asyncio
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "src"))
sys.path.insert(0, SRC)


class TestBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "state")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "config")
        from superterm.app import App
        from superterm.bridge import Bridge
        self.app = App()
        self.bridge = Bridge(self.app)

    def tearDown(self):
        self.app.close()

    def call(self, method, params=None, rid="r1", pv=1):
        raw = json.dumps({"protocol_version": pv, "request_id": rid, "method": method,
                          "params": params or {}})
        return json.loads(asyncio.run(self.bridge.handle(raw)))

    def test_boot_state(self):
        r = self.call("app.get_boot_state")
        self.assertTrue(r["ok"])
        self.assertIn("profiles", r["result"])
        self.assertFalse(r["result"]["capabilities"]["interactive_approvals"])

    def test_approval_response_is_explicitly_unsupported(self):
        for decision in ("approve", "deny"):
            r = self.call("approval_respond", {"request_id": "pending", "decision": decision})
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"]["code"], "unsupported")

    def test_bad_protocol(self):
        r = self.call("app.get_boot_state", pv=99)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "bad_protocol")

    def test_unknown_method(self):
        r = self.call("nope.method")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "unknown_method")

    def test_session_create_and_open(self):
        r = self.call("session_create", {"provider": "codex", "workspace": "/tmp"})
        self.assertTrue(r["ok"])
        sid = r["result"]["session"]["id"]
        r2 = self.call("session_open", {"session_id": sid})
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["result"]["session"]["id"], sid)

    def test_config_roundtrip(self):
        r = self.call("config_save_profile", {"display_name": "T", "app_type": "codex",
                                              "base_url": "https://t/v1", "wire_api": "responses",
                                              "token": "sk-secret12345678"})
        self.assertTrue(r["ok"])
        pid = r["result"]["profile"]["id"]
        self.assertTrue(r["result"]["profile"]["has_token"])
        self.assertNotIn("sk-secret12345678", json.dumps(r))
        # set binding
        rb = self.call("config_set_binding", {"agent": "codex", "provider_id": pid, "model_id": "m1"})
        self.assertTrue(rb["ok"])

    def test_oversize_rejected(self):
        big = "x" * (3 * 1024 * 1024)
        raw = json.dumps({"protocol_version": 1, "request_id": "r", "method": "app.get_boot_state",
                          "params": {"pad": big}})
        r = json.loads(asyncio.run(self.bridge.handle(raw)))
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "too_large")

    def test_workspace_read_file_forbidden_escape(self):
        r = self.call("session_create", {"provider": "codex", "workspace": "/tmp"})
        sid = r["result"]["session"]["id"]
        rr = self.call("workspace_read_file", {"session_id": sid, "path": "../../etc/passwd"})
        self.assertFalse(rr["ok"])
        self.assertEqual(rr["error"]["code"], "forbidden")

    def test_workspace_list_outside_roots_forbidden(self):
        for bad in ("/etc", "/root", "/"):
            rr = self.call("workspace_list", {"root": bad})
            self.assertFalse(rr["ok"], f"listing {bad} should be forbidden")
            self.assertEqual(rr["error"]["code"], "forbidden")

    def test_session_create_outside_roots_forbidden(self):
        rr = self.call("session_create", {"provider": "codex", "workspace": "/etc"})
        self.assertFalse(rr["ok"])
        self.assertEqual(rr["error"]["code"], "forbidden")


if __name__ == "__main__":
    unittest.main()
