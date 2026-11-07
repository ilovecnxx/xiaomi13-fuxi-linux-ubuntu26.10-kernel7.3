"""Unit tests for redaction."""
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "src")))

from superterm.redaction import Redactor, mask_token  # noqa: E402


class TestRedactor(unittest.TestCase):
    def test_openai_key(self):
        r = Redactor()
        out, changed = r.redact("key is sk-abcdef123456789 here")
        self.assertTrue(changed)
        self.assertNotIn("sk-abcdef123456789", out)
        self.assertIn("<REDACTED>", out)

    def test_bearer(self):
        r = Redactor()
        out, changed = r.redact("Authorization: Bearer abcdef123456789xyz")
        self.assertTrue(changed)
        self.assertNotIn("abcdef123456789xyz", out)

    def test_configured_secret(self):
        r = Redactor(extra_secrets=["supersecretvalue123"])
        out, changed = r.redact("token=supersecretvalue123 end")
        self.assertTrue(changed)
        self.assertNotIn("supersecretvalue123", out)

    def test_jwt(self):
        r = Redactor()
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
        out, changed = r.redact(f"bearer {jwt}")
        self.assertTrue(changed)
        self.assertNotIn(jwt, out)

    def test_obj_recursive(self):
        r = Redactor()
        obj = {"a": "sk-secretkey12345678", "b": ["sk-anotherkey99999"], "c": 5}
        out = r.redact_obj(obj)
        self.assertNotIn("sk-secretkey12345678", str(out))
        self.assertNotIn("sk-anotherkey99999", str(out))
        self.assertEqual(out["c"], 5)

    def test_benign_text_untouched(self):
        r = Redactor()
        out, changed = r.redact("This is a normal sentence with no keys.")
        self.assertFalse(changed)
        self.assertEqual(out, "This is a normal sentence with no keys.")

    def test_mask_token(self):
        self.assertEqual(mask_token("sk-1234567890abcd"), "****abcd")
        self.assertEqual(mask_token("ab"), "****")


if __name__ == "__main__":
    unittest.main()
