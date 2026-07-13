"""Test del flow Strato 1 ADR 0089 — extract_credentials + scrubbing.

Copertura:
  - extract_credentials con varianti naturali (user X pwd Y, utente X password Y,
    user=X pass=Y, username:X password:Y, IT/EN).
  - dominio derivato da host CIFS/URL/bare hostname + binding inferito.
  - scrubbing offsets esatti.
  - apply_credentials_extraction roundtrip (estrazione + cifratura + redact).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestExtractCredentials(unittest.TestCase):
    """Strato 1 — estrazione regex deterministica (no LLM)."""

    def test_user_then_pwd_inline(self):
        from agent_runtime import extract_credentials
        q = "monta lo share \\\\192.168.1.20\\Public user roberto pwd hunter2"
        out = extract_credentials(q)
        self.assertEqual(len(out), 1)
        c = out[0]
        self.assertEqual(c["username"], "roberto")
        self.assertEqual(c["password"], "hunter2")
        self.assertEqual(c["domain"], "cifs_192.168.1.20")
        self.assertEqual(c["context"]["binding"], "cifs")
        self.assertEqual(c["context"]["host"], "192.168.1.20")
        self.assertIn("Public", c["context"]["share"])

    def test_utente_password_italian(self):
        from agent_runtime import extract_credentials
        q = "accedi al sito https://webmail.example.com utente alice password segreta123"
        out = extract_credentials(q)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["username"], "alice")
        self.assertEqual(out[0]["password"], "segreta123")
        self.assertEqual(out[0]["domain"], "webmail.example.com")
        self.assertEqual(out[0]["context"]["binding"], "web")

    def test_natural_site_credentials_without_scheme(self):
        from agent_runtime import extract_credentials
        out = extract_credentials(
            "credenziali di telepass.com, utente xxxxx, password yyy")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["domain"], "telepass.com")
        self.assertEqual(out[0]["username"], "xxxxx")
        self.assertEqual(out[0]["password"], "yyy")
        self.assertEqual(out[0]["context"]["binding"], "web")

    def test_natural_site_credentials_usr_pwd(self):
        from agent_runtime import extract_credentials
        out = extract_credentials(
            "ricorda credenziali telepass.com usr:xxxxx , pwd:yyyyy")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["domain"], "telepass.com")
        self.assertEqual(out[0]["username"], "xxxxx")
        self.assertEqual(out[0]["password"], "yyyyy")

    def test_natural_credentials_allow_connectors_and_quoted_values(self):
        from agent_runtime import extract_credentials
        out = extract_credentials(
            'telepass.com email "nome@example.com" con password "a,b c"')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["username"], "nome@example.com")
        self.assertEqual(out[0]["password"], "a,b c")

    def test_store_only_intent_is_semantic_and_compound_continues(self):
        from agent_runtime import _is_credentials_store_only_intent
        from engine.types import Intent
        assert _is_credentials_store_only_intent(
            Intent(verb="set", object="credentials"))
        assert not _is_credentials_store_only_intent(Intent(
            verb="set", object="credentials",
            actions=[{"verb": "set", "object": "credentials"},
                     {"verb": "login", "object": "sites"}]))

    def test_engine_confirms_credentials_already_stored_before_planner(self):
        import agent_runtime
        import intent_extractor
        from unittest import mock
        with mock.patch.object(intent_extractor, "extract_intent", return_value={
                "verb": "set", "object": "credentials", "keywords": []}):
            out = agent_runtime._run_engine(
                "ricorda credenziali telepass.com usr:<REDACTED:cred> ", [],
                turn_id="test-credentials", actor="host", channel="http",
                credential_meta=[{"domain": "telepass.com",
                                  "context": {"binding": "web"}}])
        self.assertEqual(out["match_source"], "credential_extraction")
        self.assertEqual(out["steps"][0].chosen_tool, "set_credentials")
        self.assertNotIn("password", out["final_text"].lower())

    def test_equal_sign_syntax(self):
        from agent_runtime import extract_credentials
        q = "ssh user=mario pass=topolino@nas.local"
        out = extract_credentials(q)
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(out[0]["username"], "mario")
        self.assertTrue(out[0]["password"].startswith("topolino"))
        self.assertTrue(out[0]["domain"].startswith("ssh_"))

    def test_colon_syntax(self):
        from agent_runtime import extract_credentials
        q = "monta share //nas.lan/data username:carlo password:abc123"
        out = extract_credentials(q)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["username"], "carlo")
        self.assertEqual(out[0]["password"], "abc123")
        self.assertEqual(out[0]["domain"], "cifs_nas.lan")

    def test_pwd_then_user(self):
        from agent_runtime import extract_credentials
        q = "//srv/x pwd hunter user roberto"
        out = extract_credentials(q)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["username"], "roberto")
        self.assertEqual(out[0]["password"], "hunter")
        self.assertEqual(out[0]["domain"], "cifs_srv")

    def test_no_creds_returns_empty(self):
        from agent_runtime import extract_credentials
        self.assertEqual(extract_credentials("trovami i file *.py"), [])
        self.assertEqual(extract_credentials(""), [])
        self.assertEqual(extract_credentials("mail di oggi"), [])

    def test_scrub_spans_offsets(self):
        """Gli scrub_spans devono coprire ESATTAMENTE i value, non le keyword."""
        from agent_runtime import extract_credentials
        q = "user roberto pwd hunter2 //h/s"
        out = extract_credentials(q)
        self.assertEqual(len(out), 1)
        spans = out[0]["scrub_spans"]
        # roberto = chars 5..12, hunter2 = chars 17..24
        # lo span deve corrispondere ai value, non alle keyword "user"/"pwd"
        for start, end in spans:
            substr = q[start:end]
            self.assertIn(substr, ("roberto", "hunter2"))

    def test_redact_replaces_values_only(self):
        """Strato 1 redact: i value sono sostituiti da `<REDACTED:cred:domain>`,
        ma le keyword restano leggibili.
        """
        from agent_runtime import _redact_spans, extract_credentials
        q = "user roberto pwd hunter2 share //nas/x"
        out = extract_credentials(q)
        # Aggrega tutti gli span
        spans = [tuple(s) for c in out for s in c["scrub_spans"]]
        redacted = _redact_spans(q, spans, out[0]["domain"])
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("roberto", redacted)
        self.assertIn("user", redacted)
        self.assertIn("pwd", redacted)
        self.assertIn("REDACTED:cred:cifs_nas", redacted)


class TestApplyCredentialsExtraction(unittest.TestCase):
    """Strato 1 end-to-end: extract + store cifrato + redact."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import credentials
        self._old_cred_dir = credentials.CRED_DIR
        credentials.CRED_DIR = Path(self.tmp) / "credentials"

    def tearDown(self):
        import credentials
        credentials.CRED_DIR = self._old_cred_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_stores_and_redacts(self):
        from agent_runtime import apply_credentials_extraction
        import credentials
        q = "monta share \\\\192.168.99.99\\public user pippo pwd paperino"
        redacted, meta = apply_credentials_extraction(q)
        # 1) Le creds sono sparite dal testo
        self.assertNotIn("pippo", redacted)
        self.assertNotIn("paperino", redacted)
        # 2) Il dominio e' stato salvato cifrato
        self.assertIn("cifs_192.168.99.99", credentials.list_domains())
        loaded = credentials.load("cifs_192.168.99.99")
        self.assertEqual(loaded["username"], "pippo")
        self.assertEqual(loaded["password"], "paperino")
        # 3) La metadata esposta NON contiene la pwd
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0]["domain"], "cifs_192.168.99.99")
        self.assertNotIn("password", meta[0])
        self.assertNotIn("username", meta[0])

    def test_apply_no_creds_passthrough(self):
        from agent_runtime import apply_credentials_extraction
        q = "trovami file *.py"
        redacted, meta = apply_credentials_extraction(q)
        self.assertEqual(redacted, q)
        self.assertEqual(meta, [])

    def test_apply_natural_web_credentials_stores_exact_host_and_redacts(self):
        from agent_runtime import apply_credentials_extraction
        import credentials
        q = ("ricorda credenziali telepass.com usr:alice "
             "e password:secret123")
        redacted, meta = apply_credentials_extraction(q)
        self.assertNotIn("alice", redacted)
        self.assertNotIn("secret123", redacted)
        self.assertEqual(meta[0]["domain"], "telepass.com")
        payload = credentials.load("telepass.com")
        self.assertEqual(payload["username"], "alice")
        self.assertEqual(payload["password"], "secret123")


if __name__ == "__main__":
    unittest.main()
