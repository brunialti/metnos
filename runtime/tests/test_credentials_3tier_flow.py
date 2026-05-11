"""Smoke E2E del flow Strato 1+2+3 ADR 0089 + ADR 0091 (mockato).

Pipeline coperta:

  Strato 1: query "monta share \\\\h\\s user X pwd Y"
    → extract_credentials estrae +
    → credentials.store cifra +
    → query passata al PLANNER ha pwd redacted.

  Strato 2: admin invoke con command_proposed contenente ${METNOS_CIFS_CREDS}
            ma dominio NON ancora salvato → decision=needs_inputs (ADR 0091)
            con payload strutturato (title/description/dialog/on_complete).
            Niente carta vaglio fino a quando le creds non sono raccolte.

  Strato 2 → 1: dopo che le creds sono state salvate, admin riprova
            → si arriva alla carta vaglio normale (approval_required).

  Strato 3: admin emette istruzioni CLI come fallback indipendente di
            ADR 0091 (l'utente puo' sempre passare per `metnos-cli`).

Niente subprocess reale verso mount/sudoer (mock fasulli). Niente Telegram
reale: chiamate dirette ai moduli runtime.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestCredentials3TierFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import credentials
        self._old_cred_dir = credentials.CRED_DIR
        credentials.CRED_DIR = Path(self.tmp) / "credentials"

    def tearDown(self):
        import credentials
        credentials.CRED_DIR = self._old_cred_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_strato1_query_extraction_pipeline(self):
        """Una query con creds inline → estrazione + store + redacted."""
        from agent_runtime import apply_credentials_extraction
        import credentials
        q = ("monta nella mia area lo share \\\\nas.local\\Public "
             "user roberto pwd hunter2 -o uid=1000")
        red, meta = apply_credentials_extraction(q)
        self.assertIn("cifs_nas.local", credentials.list_domains())
        self.assertEqual(credentials.load("cifs_nas.local")["username"], "roberto")
        self.assertEqual(credentials.load("cifs_nas.local")["password"], "hunter2")
        # Niente plaintext nella query passata al PLANNER
        self.assertNotIn("hunter2", red)
        self.assertNotIn("roberto", red)
        # Metadata sicura per iniezione nel prompt — niente pwd
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0]["domain"], "cifs_nas.local")
        self.assertNotIn("password", meta[0])

    def test_strato2_admin_emits_needs_inputs_when_missing(self):
        """admin con placeholder ${METNOS_CIFS_CREDS} ma dominio assente
        → decision=needs_inputs (ADR 0091), payload strutturato per
        l'orchestratore runtime (no carta vaglio, no summary plain text).
        """
        from verb_unique.admin import invoke
        # Non abbiamo salvato nulla → dominio cifs_192.0.2.10 assente.
        out = invoke(
            intent="mount cifs share di test",
            command_proposed=(
                "mount -t cifs //192.0.2.10/Public /mnt/test "
                "-o credentials=${METNOS_CIFS_CREDS},uid=1000"
            ),
            actor="host",
        )
        self.assertEqual(out["decision"], "needs_inputs")
        self.assertEqual(out["credentials_domain"], "cifs_192.0.2.10")
        self.assertFalse(out["approval_required"])
        # Niente summary plain text: la UX e' generata dal runtime
        # tramite get_inputs.final_message_hint.
        self.assertEqual(out["summary"], "")
        # Payload strutturato per l'orchestratore.
        payload = out["needs_inputs"]
        self.assertIsNotNone(payload)
        self.assertEqual(payload["dialog"][0]["var"], "username")
        self.assertEqual(payload["dialog"][1]["var"], "password")
        self.assertEqual(
            payload["on_complete"]["type"], "save_credentials_and_resume",
        )
        self.assertEqual(
            payload["on_complete"]["resume_call"], "admin",
        )

    def test_strato2_advances_when_creds_present(self):
        """Stessa invoke: dopo che le creds sono salvate, decision NON e'
        piu' needs_inputs: il flow e' libero di proseguire al vaglio
        (approval_required, execute_silent o reject in base alla signature;
        mai needs_inputs)."""
        import credentials
        # Pre-salva le creds
        credentials.store("cifs_192.0.2.10", {
            "username": "u", "password": "p", "binding": "cifs",
            "host": "192.0.2.10",
        })
        from verb_unique.admin import invoke
        out = invoke(
            intent="mount cifs share di test",
            command_proposed=(
                "mount -t cifs //192.0.2.10/Public /mnt/test "
                "-o credentials=${METNOS_CIFS_CREDS},uid=1000"
            ),
            actor="host",
        )
        # Verifica: il bypass Strato 2 non scatta perche' le creds esistono.
        self.assertNotEqual(out["decision"], "needs_inputs")
        # In base allo stato del SafetyStore (graylist/whitelist sul .33), il
        # decision sara' approval_required o execute_silent (sudoer mock).
        self.assertIn(out["decision"],
                      ("approval_required", "execute_silent", "reject"))

    def test_strato3_cli_instructions_format(self):
        """_format_cli_instructions emette indicazioni terminale leggibili."""
        from verb_unique.admin import _format_cli_instructions
        text = _format_cli_instructions("cifs_192.168.1.20", {
            "binding": "cifs", "host": "192.168.1.20",
        })
        self.assertIn("metnos-cli credentials add", text)
        self.assertIn("cifs_192.168.1.20", text)
        self.assertIn("ssh", text)  # suggerimento di accesso remoto
        self.assertIn("password", text)

    def test_pending_kind_get_inputs_response_in_runtime_path(self):
        """Lato runtime ADR 0091: agent_runtime intercetta
        decision=needs_inputs e auto-orchestra get_inputs (kind=
        get_inputs_response nel cap_pending). Verifica che le primitive
        di estrazione siano ortogonali (niente cred → meta vuoto)."""
        from agent_runtime import apply_credentials_extraction
        red, meta = apply_credentials_extraction("trovami file *.py")
        self.assertEqual(meta, [])
        self.assertEqual(red, "trovami file *.py")


if __name__ == "__main__":
    unittest.main()
