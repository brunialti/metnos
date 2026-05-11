"""tests per runtime/users.py — multi-user management (sprint 4/5/2026, ADR 0083).

Run: `python3 -m pytest runtime/tests/test_users.py -v`.

Tutti i test usano un DB temp isolato via env METNOS_USERS_DB.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class UsersTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "users.db"
        os.environ["METNOS_USERS_DB"] = str(self._db)
        os.environ["USER"] = "roberto"
        # Reload del modulo per leggere env all'import
        import importlib
        import users  # noqa: WPS433
        importlib.reload(users)
        self.users = users

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("METNOS_USERS_DB", None)

    # --- create + get -------------------------------------------------------

    def test_init_bootstrap_creates_host(self):
        """init_db crea automaticamente l'host se non c'e'."""
        self.users.init_db()
        hosts = self.users.list_users(role="host")
        self.assertEqual(len(hosts), 1)
        self.assertEqual(hosts[0]["name"], "roberto")
        self.assertEqual(hosts[0]["role"], "host")
        self.assertEqual(hosts[0]["autonomy_level"], "full")

    def test_create_guest_and_get_by_name(self):
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        u = self.users.create_user(
            "Lucia",
            display_name="Lucia Rossi",
            role="guest",
            owner_user_id=host["id"],
            autonomy_level="restricted",
        )
        self.assertEqual(u["name"], "lucia")
        self.assertEqual(u["display_name"], "Lucia Rossi")
        self.assertEqual(u["role"], "guest")
        # Lookup per name
        v = self.users.get_user("lucia")
        self.assertEqual(v["id"], u["id"])
        # Lookup per id
        w = self.users.get_user(u["id"])
        self.assertEqual(w["name"], "lucia")

    # --- name unique violation ---------------------------------------------

    def test_create_duplicate_name_raises(self):
        """Creazione due user con stesso name (case-insensitive) fallisce."""
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        self.users.create_user("lucia", owner_user_id=host["id"])
        with self.assertRaises(ValueError) as ctx:
            self.users.create_user("LUCIA", owner_user_id=host["id"])
        self.assertIn("already exists", str(ctx.exception))

    # --- channel binding ---------------------------------------------------

    def test_add_and_get_channel(self):
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        u = self.users.create_user("marco", owner_user_id=host["id"])
        ch = self.users.add_channel(u["id"], "telegram", "555111", verified=True)
        self.assertEqual(ch["channel"], "telegram")
        self.assertEqual(ch["recipient_id"], "555111")
        self.assertIsNotNone(ch["verified_at"])
        # get
        c2 = self.users.get_channel(u["id"], "telegram")
        self.assertEqual(c2["recipient_id"], "555111")
        # remove
        self.assertTrue(self.users.remove_channel(u["id"], "telegram"))
        self.assertIsNone(self.users.get_channel(u["id"], "telegram"))

    # --- pairing token verify ---------------------------------------------

    def test_pairing_token_verify(self):
        """issue_pairing_token + consume_pairing_token end-to-end."""
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        u = self.users.create_user("paola", owner_user_id=host["id"])
        token = self.users.issue_pairing_token(u["id"], "telegram", ttl_s=3600)
        self.assertTrue(token)
        self.assertEqual(len(token), 32)
        # consume
        verified = self.users.consume_pairing_token("telegram", "999888", token)
        self.assertEqual(verified["id"], u["id"])
        # ora il binding e' attivo
        ch = self.users.get_channel(u["id"], "telegram")
        self.assertEqual(ch["recipient_id"], "999888")
        self.assertIsNotNone(ch["verified_at"])
        # consumo doppio: deve fallire (token cleared)
        with self.assertRaises(ValueError) as ctx:
            self.users.consume_pairing_token("telegram", "999888", token)
        self.assertIn("unknown", str(ctx.exception).lower())

    # --- pairing token expiry ----------------------------------------------

    def test_pairing_token_expiry(self):
        """Token con expires_at nel passato fallisce con 'expired'."""
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        u = self.users.create_user("anna", owner_user_id=host["id"])
        # Issue normalmente, poi forziamo expires_at nel passato
        token = self.users.issue_pairing_token(u["id"], "telegram", ttl_s=3600)
        import sqlite3
        conn = sqlite3.connect(str(self._db))
        try:
            past = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60)
            )
            conn.execute(
                "UPDATE user_channels SET pairing_expires_at=? WHERE user_id=?",
                (past, u["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(ValueError) as ctx:
            self.users.consume_pairing_token("telegram", "777666", token)
        self.assertIn("expired", str(ctx.exception).lower())

    # --- find_user_by_recipient -------------------------------------------

    def test_find_user_by_recipient(self):
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        u = self.users.create_user("giulia", owner_user_id=host["id"])
        self.users.add_channel(u["id"], "telegram", "111222", verified=True)
        found = self.users.find_user_by_recipient("telegram", "111222")
        self.assertIsNotNone(found)
        self.assertEqual(found["name"], "giulia")
        # recipient sconosciuto
        none = self.users.find_user_by_recipient("telegram", "9999999")
        self.assertIsNone(none)
        # canale non verified non e' visibile
        v = self.users.create_user("mario", owner_user_id=host["id"])
        self.users.add_channel(v["id"], "telegram", "333444", verified=False)
        self.assertIsNone(
            self.users.find_user_by_recipient("telegram", "333444"),
            "channel non verified non deve risolversi",
        )

    # --- resolve_recipients (mix id/name/chat_id) -------------------------

    def test_resolve_recipients_mixed(self):
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        lucia = self.users.create_user("lucia", owner_user_id=host["id"])
        self.users.add_channel(lucia["id"], "telegram", "1001", verified=True)
        marco = self.users.create_user("marco", owner_user_id=host["id"])
        # marco senza canale telegram → error channel_not_paired
        targets = ["lucia", marco["id"], "@555666", "ghost", ""]
        results = self.users.resolve_recipients(targets, "telegram")
        self.assertEqual(len(results), 5)
        # 0: lucia by name → ok
        self.assertEqual(results[0]["recipient_id"], "1001")
        self.assertIsNone(results[0]["error"])
        self.assertEqual(results[0]["user"]["name"], "lucia")
        # 1: marco by id → channel non paired
        self.assertEqual(results[1]["error"], "channel_not_paired")
        self.assertEqual(results[1]["user"]["name"], "marco")
        # 2: @555666 chat_id diretto → ok, no user
        self.assertIsNone(results[2]["user"])
        self.assertEqual(results[2]["recipient_id"], "555666")
        self.assertIsNone(results[2]["error"])
        # 3: ghost user inesistente → error user_not_found
        self.assertEqual(results[3]["error"], "user_not_found")
        # 4: empty target → error empty_target
        self.assertEqual(results[4]["error"], "empty_target")

    # --- delete cascade -----------------------------------------------------

    def test_delete_user_cascades_channels(self):
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        u = self.users.create_user("toomany", owner_user_id=host["id"])
        self.users.add_channel(u["id"], "telegram", "9000", verified=True)
        self.users.add_channel(u["id"], "mail", "x@y.z", verified=True)
        self.assertEqual(len(self.users.list_channels(u["id"])), 2)
        self.assertTrue(self.users.delete_user(u["id"]))
        self.assertIsNone(self.users.get_user(u["id"]))
        self.assertEqual(len(self.users.list_channels(u["id"])), 0)

    # --- extra: autobind host telegram + idempotenza -----------------------

    def test_autobind_host_telegram_idempotent(self):
        """autobind_host_telegram: prima volta binda, seconda volta no-op."""
        self.users.init_db()
        ch1 = self.users.autobind_host_telegram("587627005")
        self.assertIsNotNone(ch1)
        self.assertEqual(ch1["recipient_id"], "587627005")
        self.assertIsNotNone(ch1["verified_at"])
        ch2 = self.users.autobind_host_telegram("587627005")
        self.assertIsNotNone(ch2)
        # Stesso recipient
        self.assertEqual(ch2["recipient_id"], "587627005")


if __name__ == "__main__":
    unittest.main()
