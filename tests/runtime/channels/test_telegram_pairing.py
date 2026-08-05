"""tests per il pairing Telegram multi-user (sprint 4/5/2026, ADR 0083).

Run: `python3 -m pytest tests/runtime/channels/test_telegram_pairing.py -v`.

Mock channel: nessuna chiamata HTTP a Telegram, solo invocazioni dirette
del dispatcher `daemon.handle_message`.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class _MockChannel:
    """Channel finto: non tocca rete; raccoglie i send per assertion."""
    name = "telegram"
    default_chat_id = "1000"  # bootstrap host

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, recipient: str, message) -> dict:
        self.sent.append((recipient, message.text))
        return {"ok": True, "sent_message_id": "fake-1"}

    def poll(self):
        return []


class TelegramPairingTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        td = Path(self._tmpdir.name)
        # DB users isolato
        os.environ["METNOS_USERS_DB"] = str(td / "users.db")
        # DB pairings isolato (env letto da pairing._open_db)
        os.environ["METNOS_PAIRINGS_DB"] = str(td / "pairings.db")
        os.environ["USER"] = "roberto"
        # Reload moduli che leggono path da env all'import
        import importlib
        import users
        importlib.reload(users)
        self.users = users
        import pairing
        importlib.reload(pairing)
        self.pairing = pairing
        from channels import daemon as _daemon
        importlib.reload(_daemon)
        self._daemon_mod = _daemon

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("METNOS_USERS_DB", None)
        os.environ.pop("METNOS_PAIRINGS_DB", None)

    def _make_daemon(self):
        ch = _MockChannel()
        # run_turn iniettato come stub (non viene chiamato per /start).
        d = self._daemon_mod.ChannelDaemon(
            ch,
            run_turn=lambda *a, **kw: None,
            dry_run=False,
            bootstrap_default_sender=False,
        )
        return d, ch

    def _make_inbound(self, text: str, sender_id: str = "9999"):
        from channels import InboundMessage
        return InboundMessage(
            channel="telegram",
            sender_id=sender_id,
            text=text,
            message_id="msg-1",
            received_at=time.time(),
            extra={},
        )

    # --- valid token --------------------------------------------------------

    def test_start_with_valid_token_pairs_user(self):
        """`/start <token>` valido lega il chat_id allo user e risponde positive."""
        # Setup: host bootstrap + guest "lucia" + token telegram
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        lucia = self.users.create_user("lucia", owner_user_id=host["id"])
        token = self.users.issue_pairing_token(lucia["id"], "telegram", ttl_s=3600)
        d, ch = self._make_daemon()
        msg = self._make_inbound(f"/start {token}", sender_id="42")
        res = d.handle_message(msg)
        self.assertTrue(res["ok"], f"start fallito: {res}")
        self.assertEqual(res["name"], "lucia")
        self.assertEqual(res["role"], "guest")
        # Il chat_id 42 deve essere ora bindato a lucia in users.db
        bound = self.users.find_user_by_recipient("telegram", "42")
        self.assertIsNotNone(bound)
        self.assertEqual(bound["name"], "lucia")
        # E in pairings.db deve esserci un pairing
        p = self.pairing.get_pairing("telegram", "42")
        self.assertIsNotNone(p)
        self.assertEqual(p.actor, "lucia")
        # Reply positivo inviato
        self.assertTrue(any("lucia" in t for _, t in ch.sent))

    # --- expired token ------------------------------------------------------

    def test_start_with_expired_token_fails(self):
        """Token scaduto risponde 'Token scaduto o invalido.' e non binda."""
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        marco = self.users.create_user("marco", owner_user_id=host["id"])
        token = self.users.issue_pairing_token(marco["id"], "telegram", ttl_s=3600)
        # Forza expires_at nel passato
        import sqlite3
        conn = sqlite3.connect(os.environ["METNOS_USERS_DB"])
        try:
            past = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60),
            )
            conn.execute(
                "UPDATE user_channels SET pairing_expires_at=? WHERE user_id=?",
                (past, marco["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        d, ch = self._make_daemon()
        msg = self._make_inbound(f"/start {token}", sender_id="77")
        res = d.handle_message(msg)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "invalid_token")
        # Nessun binding creato
        self.assertIsNone(self.users.find_user_by_recipient("telegram", "77"))
        # Reply negativo
        self.assertTrue(any("scaduto" in t.lower() or "invalid" in t.lower()
                            for _, t in ch.sent))

    # --- token consumato due volte ----------------------------------------

    def test_start_token_double_use_second_fails(self):
        """Stesso token usato due volte: la seconda volta fallisce."""
        self.users.init_db()
        host = self.users.list_users(role="host")[0]
        anna = self.users.create_user("anna", owner_user_id=host["id"])
        token = self.users.issue_pairing_token(anna["id"], "telegram", ttl_s=3600)
        d, ch = self._make_daemon()
        # Prima uso: ok
        msg1 = self._make_inbound(f"/start {token}", sender_id="100")
        r1 = d.handle_message(msg1)
        self.assertTrue(r1["ok"])
        # Secondo uso (stesso token): deve fallire
        msg2 = self._make_inbound(f"/start {token}", sender_id="200")
        r2 = d.handle_message(msg2)
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["reason"], "invalid_token")
        # Solo il primo chat_id e' bindato
        self.assertIsNotNone(self.users.find_user_by_recipient("telegram", "100"))
        self.assertIsNone(self.users.find_user_by_recipient("telegram", "200"))


if __name__ == "__main__":
    unittest.main()
