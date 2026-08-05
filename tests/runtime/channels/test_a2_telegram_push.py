"""A.2 push immediato (fase 7) — il daemon telegram PUSHA gli avvisi
tardivi/differiti (user_notices) senza aspettare la prossima-visita.

Scenario: un'op remota completata DOPO il timeout del turno (A.0/B.4) o un
turno differito eseguito al ritorno del device (A.1) accoda un avviso in
`user_notices` per (channel, actor). Su telegram il daemon lo consegna al
primo giro di poll. Isolato: DB pairing + NOTICES_DIR temporanei, canale
fake (nessuna Bot API).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class A2TelegramPushTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_a2push_"))
        # Pairing DB isolato (pairing._open_db + actor_resolver leggono l'env)
        self._orig_pdb = os.environ.get("METNOS_PAIRINGS_DB")
        os.environ["METNOS_PAIRINGS_DB"] = str(self.tmp / "pairings.db")
        import user_notices as un
        self._orig_notices = un.NOTICES_DIR
        un.NOTICES_DIR = self.tmp / "notices"
        self.un = un
        # Pairing telegram reale nel DB isolato → actor 'host' (primo pairing).
        # bootstrap_default_chat_id inserisce senza flusso-codice (no chiave).
        import pairing
        self.sender_id = "555001"
        pairing.bootstrap_default_chat_id("telegram", self.sender_id)
        # Il push risolve il destinatario dal registro utenti, non dal nome
        # actor. Colleghiamo il pairing al solo host di questa sandbox.
        import users
        bound = users.find_user_by_recipient("telegram", self.sender_id)
        if bound is None:
            host = users.list_users(role="host")[0]
            users.add_channel(
                host["id"], "telegram", self.sender_id, verified=True)
            bound = users.find_user_by_recipient("telegram", self.sender_id)
        self.owner_user_id = str(bound["id"])
        # Daemon con canale fake
        from channels.daemon import ChannelDaemon
        fake = MagicMock()
        fake.name = "telegram"
        fake.send = MagicMock(return_value={"ok": True})
        self.fake = fake
        d = ChannelDaemon.__new__(ChannelDaemon)
        d.channel = fake
        d._stop = False
        d.dry_run = False
        d.run_turn = None
        self.d = d

    def tearDown(self):
        import shutil
        self.un.NOTICES_DIR = self._orig_notices
        if self._orig_pdb is None:
            os.environ.pop("METNOS_PAIRINGS_DB", None)
        else:
            os.environ["METNOS_PAIRINGS_DB"] = self._orig_pdb
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _actor(self):
        from actor_resolver import resolve_actor
        return resolve_actor("telegram", self.sender_id)

    def test_pushes_pending_notice_to_paired_chat(self):
        self.un.append(
            "telegram", self._actor(), "📬 op completata in ritardo",
            owner_user_id=self.owner_user_id)
        sent = self.d._push_pending_notices()
        self.assertEqual(sent, 1)
        # inviato al chat_id del pairing
        args, kwargs = self.fake.send.call_args
        self.assertEqual(kwargs["recipient"], self.sender_id)
        self.assertIn("ritardo", kwargs["message"].text)
        # drenata: un secondo giro non re-invia (no doppia consegna)
        self.assertEqual(self.d._push_pending_notices(), 0)

    def test_no_notice_no_send(self):
        self.assertEqual(self.d._push_pending_notices(), 0)
        self.fake.send.assert_not_called()

    def test_other_channel_notice_untouched(self):
        # Un avviso per http NON viene pushato dal daemon telegram (resta
        # per la prossima-visita di TurnLog.write).
        self.un.append(
            "http", self._actor(), "avviso web",
            owner_user_id=self.owner_user_id)
        self.assertEqual(self.d._push_pending_notices(), 0)
        self.assertEqual(
            self.un.drain(
                "http", self._actor(), owner_user_id=self.owner_user_id),
            ["avviso web"])

    def test_multiple_notices_same_actor_all_sent(self):
        a = self._actor()
        self.un.append("telegram", a, "uno", owner_user_id=self.owner_user_id)
        self.un.append("telegram", a, "due", owner_user_id=self.owner_user_id)
        self.assertEqual(self.d._push_pending_notices(), 2)
        self.assertEqual(self.fake.send.call_count, 2)

    def test_send_failure_does_not_crash(self):
        self.fake.send.side_effect = RuntimeError("bot api giù")
        self.un.append(
            "telegram", self._actor(), "x", owner_user_id=self.owner_user_id)
        # non solleva; l'avviso è comunque drenato (best-effort, evita loop)
        self.assertEqual(self.d._push_pending_notices(), 0)


if __name__ == "__main__":
    unittest.main()
