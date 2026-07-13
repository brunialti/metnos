"""Test del scrubbing credenziali nel turn log (ADR 0082, 4/5/2026)."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestScrubCredentials(unittest.TestCase):
    def test_scrub_password_pattern(self):
        from agent_runtime import _scrub_credentials
        cases = [
            ("la mia password: mySecretPwd!", "<REDACTED:cred>"),
            ("password=hunter2", "<REDACTED:cred>"),
            ("pwd: foo", "<REDACTED:cred>"),
            ("psw=bar", "<REDACTED:cred>"),
            ("Pass: baz", "<REDACTED:cred>"),
        ]
        for raw, expected in cases:
            cleaned, n = _scrub_credentials(raw)
            self.assertGreaterEqual(n, 1, raw)
            self.assertIn(expected, cleaned, f"raw={raw!r} cleaned={cleaned!r}")

    def test_scrub_username_pattern(self):
        from agent_runtime import _scrub_credentials
        cases = [
            "username: roberto",
            "user=foo",
            "utente: bar",
            "uname=baz",
        ]
        for raw in cases:
            cleaned, n = _scrub_credentials(raw)
            self.assertGreaterEqual(n, 1, raw)
            self.assertIn("<REDACTED:cred>", cleaned)

    def test_idempotent(self):
        from agent_runtime import _scrub_credentials
        text = "password: top-secret"
        c1, n1 = _scrub_credentials(text)
        c2, n2 = _scrub_credentials(c1)
        self.assertEqual(c1, c2, "scrubbing should be idempotent")

    def test_no_password_no_change(self):
        from agent_runtime import _scrub_credentials
        text = "ho perso le mie chiavi"
        c, n = _scrub_credentials(text)
        # NB: 'mie' NON matcha (deve esserci \bp...). Test sensible.
        self.assertEqual(n, 0, f"unexpected match: {c}")

    def test_scrub_args_dict_password_key(self):
        from agent_runtime import _scrub_args_recursive
        args = {"username": "roberto", "password": "MyPwd2026!", "url": "https://x"}
        total = [0]
        out = _scrub_args_recursive(args, total)
        self.assertEqual(out["password"], "<REDACTED:cred>")
        self.assertGreaterEqual(total[0], 1)

    def test_scrub_otp_and_site_value_ref(self):
        from agent_runtime import _scrub_args_recursive, _scrub_credentials
        cleaned, n = _scrub_credentials("codice di verifica: 123456")
        self.assertGreaterEqual(n, 1)
        self.assertNotIn("123456", cleaned)
        total = [0]
        out = _scrub_args_recursive({"value_ref": "123456"}, total)
        self.assertEqual(out["value_ref"], "<REDACTED:cred>")

    def test_turn_log_scrubs_query_in_jsonl(self):
        """Integration: TurnLog.write() produce jsonl con query pulita."""
        from agent_runtime import TurnLog, StepLog
        import agent_runtime as ar

        # Override TURN_LOG_DIR per non scrivere nei dati reali
        with tempfile.TemporaryDirectory() as tmp:
            old = ar.TURN_LOG_DIR
            ar.TURN_LOG_DIR = Path(tmp)
            try:
                t = TurnLog(ts_start=time.time())
                t.user_query = "controlla la mail con password: hunter2"
                t.final_kind = "answer"
                t.final_message = "ok fatto"
                t.ts_end = t.ts_start + 1.0
                step = StepLog(step_num=1)
                step.raw_args = {"username": "roberto", "password": "hunter2"}
                t.steps.append(step)
                t.write()
                # Leggi il jsonl scritto
                files = list(Path(tmp).glob("*.jsonl"))
                self.assertEqual(len(files), 1)
                lines = files[0].read_text().splitlines()
                self.assertEqual(len(lines), 1)
                rec = json.loads(lines[0])
                self.assertNotIn("hunter2", lines[0])
                self.assertIn("REDACTED:cred", lines[0])
                self.assertTrue(rec.get("redacted"))
                self.assertGreaterEqual(rec.get("n_redacted_fields", 0), 1)
            finally:
                ar.TURN_LOG_DIR = old


if __name__ == "__main__":
    unittest.main()
