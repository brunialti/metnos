"""Test del modulo runtime.credentials (ADR 0082, 4/5/2026)."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import credentials
        # Override CRED_DIR per isolare i test (non tocchiamo i veri dati)
        self._old_cred_dir = credentials.CRED_DIR
        credentials.CRED_DIR = Path(self.tmp) / "credentials"

    def tearDown(self):
        import credentials
        credentials.CRED_DIR = self._old_cred_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        import credentials
        payload = {
            "login_url": "https://test.example.com/login",
            "method": "POST",
            "form_data": {"username": "testuser", "password": "testpwd"},
            "session_cookie_names": ["SID"],
        }
        path = credentials.store("test.example.com", payload)
        self.assertTrue(path.exists())
        loaded = credentials.load("test.example.com")
        self.assertEqual(loaded, payload)

    def test_load_missing_returns_none(self):
        import credentials
        out = credentials.load("does.not.exist.xyz")
        self.assertIsNone(out)

    def test_list_domains(self):
        import credentials
        credentials.store("a.example.com", {"login_url": "x"})
        credentials.store("b.example.com", {"login_url": "y"})
        domains = credentials.list_domains()
        self.assertEqual(sorted(domains), ["a.example.com", "b.example.com"])

    def test_remove(self):
        import credentials
        credentials.store("zap.example.com", {"login_url": "x"})
        self.assertTrue(credentials.remove("zap.example.com"))
        self.assertFalse(credentials.remove("zap.example.com"))
        self.assertNotIn("zap.example.com", credentials.list_domains())

    def test_fingerprint(self):
        import credentials
        payload = {
            "login_url": "https://x",
            "form_data": {"username": "u", "password": "secret-pwd"},
        }
        credentials.store("fp.example.com", payload)
        fp = credentials.fingerprint("fp.example.com")
        self.assertIsNotNone(fp)
        self.assertEqual(len(fp), 16)
        # Stesso payload → stesso fingerprint
        credentials.store("fp2.example.com", payload)
        fp2 = credentials.fingerprint("fp2.example.com")
        self.assertEqual(fp, fp2)
        # Senza password → None
        credentials.store("nopwd.example.com", {"login_url": "x"})
        self.assertIsNone(credentials.fingerprint("nopwd.example.com"))

    def test_invalid_domain_rejected(self):
        import credentials
        with self.assertRaises(ValueError):
            credentials.store("../etc/passwd", {"login_url": "x"})
        with self.assertRaises(ValueError):
            credentials.store("a/b", {"login_url": "x"})
        with self.assertRaises(ValueError):
            credentials.store("", {"login_url": "x"})


if __name__ == "__main__":
    unittest.main()
