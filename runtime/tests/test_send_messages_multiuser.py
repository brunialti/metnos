"""tests per send_messages multi-user (sprint 4/5/2026, ADR 0083+0084).

Mock di SMTP + TelegramChannel: nessuna chiamata di rete reale.

Run: `python3 -m pytest runtime/tests/test_send_messages_multiuser.py -v`.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

# Lo script send_messages.py vive in /opt/myclaw/executors/send_messages/
_EXEC = Path("/opt/myclaw/executors/send_messages").resolve()
sys.path.insert(0, str(_EXEC))


class _FakeSMTP:
    def __init__(self):
        self.sent: list[dict] = []

    def send_message(self, msg, *, from_addr, to_addrs):
        self.sent.append({"from": from_addr, "to": to_addrs,
                          "subject": msg.get("Subject"),
                          "body_present": True})

    def quit(self):
        pass


class SendMessagesMultiuserTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        td = Path(self._tmpdir.name)
        os.environ["METNOS_USERS_DB"] = str(td / "users.db")
        os.environ["METNOS_PAIRINGS_DB"] = str(td / "pairings.db")
        os.environ["USER"] = "roberto"
        # Reload moduli
        import users
        importlib.reload(users)
        self.users = users
        # send_messages e' uno script standalone, va re-importato
        if "send_messages" in sys.modules:
            del sys.modules["send_messages"]
        import send_messages  # noqa: WPS433
        self.send_messages = send_messages

        # Setup base: host + lucia (telegram pairata) + marco (telegram pairata)
        self.users.init_db()
        self.host = self.users.list_users(role="host")[0]
        self.lucia = self.users.create_user(
            "lucia", display_name="Lucia", owner_user_id=self.host["id"],
        )
        self.users.add_channel(self.lucia["id"], "telegram", "1001",
                               verified=True)
        self.marco = self.users.create_user(
            "marco", display_name="Marco", owner_user_id=self.host["id"],
        )
        self.users.add_channel(self.marco["id"], "telegram", "2002",
                               verified=True)

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("METNOS_USERS_DB", None)
        os.environ.pop("METNOS_PAIRINGS_DB", None)

    def _patch_telegram(self):
        """Patch `backends.messages.telegram_bot.send` per non aprire connessioni reali.

        Dopo il refactor 13/5/2026 (Q1 canonical+args), il dispatcher
        `send_messages` instrada al backend builtin
        `runtime/backends/messaging/telegram_bot.py`. Mock-iamo a quel
        livello per simulare l'invio.
        """
        sent = []
        from backends.messages import telegram_bot as _tg

        def fake_send(args):
            results = []
            for m in args.get("messages", []):
                rid = m.get("recipient_id") or m.get("chat_id")
                body = m.get("body") or m.get("text") or ""
                subject = m.get("subject")
                full = f"{subject}\n\n{body}".strip() if subject else body
                sent.append({"chat_id": str(rid), "body": full})
                rec = {
                    "channel": "telegram",
                    "recipient_id": str(rid),
                    "sent_message_id": f"fake-{rid}",
                    "ok": True,
                }
                for k in ("recipient_user_id", "recipient_name", "target"):
                    if k in m:
                        rec[k] = m[k]
                results.append(rec)
            return {"ok": True, "ok_count": len(results), "fail_count": 0,
                    "results": results, "failed": []}
        return mock.patch.object(_tg, "send", side_effect=fake_send), sent

    # --- 1. send a host ----------------------------------------------------

    def test_send_to_host_via_telegram(self):
        """Host con canale telegram pairato: il send va a buon fine."""
        self.users.add_channel(self.host["id"], "telegram", "5000",
                               verified=True)
        patch_tg, sent = self._patch_telegram()
        with patch_tg:
            res = self.send_messages.invoke({
                "messages": [{"to_user": self.host["name"], "body": "hello host"}],
                "via_channel": "telegram",
                "actor": "host",
            })
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["ok_count"], 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["chat_id"], "5000")
        self.assertIn("hello host", sent[0]["body"])

    # --- 2. send a guest singolo ------------------------------------------

    def test_send_to_single_guest(self):
        """`to_user='lucia'` risolve a chat_id 1001 e invia."""
        patch_tg, sent = self._patch_telegram()
        with patch_tg:
            res = self.send_messages.invoke({
                "messages": [{"to_user": "lucia", "subject": "Sveglia",
                              "body": "buongiorno"}],
                "via_channel": "telegram",
                "actor": "host",
            })
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["ok_count"], 1)
        self.assertEqual(res["fail_count"], 0)
        rec = res["results"][0]
        self.assertEqual(rec["channel"], "telegram")
        self.assertEqual(rec["recipient_id"], "1001")
        self.assertEqual(rec["recipient_name"], "lucia")
        self.assertEqual(rec["recipient_user_id"], self.lucia["id"])

    # --- 3. send a lista mista (telegram, multi-target per stesso msg) -----

    def test_send_to_mixed_list(self):
        """`to_user=['lucia','marco','@9999']`: 3 recipient risolti, tutti ok."""
        patch_tg, sent = self._patch_telegram()
        with patch_tg:
            res = self.send_messages.invoke({
                "messages": [{
                    "to_user": ["lucia", "marco", "@9999"],
                    "body": "messaggio in lista",
                }],
                "via_channel": "telegram",
                "actor": "host",
            })
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["ok_count"], 3)
        self.assertEqual(res["fail_count"], 0)
        chat_ids = {r["recipient_id"] for r in res["results"]}
        self.assertEqual(chat_ids, {"1001", "2002", "9999"})

    # --- 4. vaglio cross-user da guest a guest ----------------------------

    def test_cross_user_send_from_guest_to_guest_blocked(self):
        """`actor='lucia'` invia a 'marco' → vaglio richiesto, fail con
        ERR_VAGLIO_REQUIRED. La send a se stessa (lucia→lucia) e' permessa.
        """
        patch_tg, sent = self._patch_telegram()
        with patch_tg:
            res = self.send_messages.invoke({
                "messages": [{
                    "to_user": ["lucia", "marco"],
                    "body": "ciao",
                }],
                "via_channel": "telegram",
                "actor": "lucia",
            })
        # Almeno uno dei due (marco) deve essere bloccato
        self.assertFalse(res["ok"])
        self.assertGreater(res["fail_count"], 0)
        blocked = [f for f in res["failed"]
                   if f.get("error_code") == "ERR_VAGLIO_REQUIRED"]
        self.assertEqual(len(blocked), 1, f"failed={res['failed']}")
        self.assertEqual(blocked[0]["recipient_name"], "marco")
        # lucia → lucia OK
        self.assertEqual(res["ok_count"], 1)
        self.assertEqual(res["results"][0]["recipient_name"], "lucia")

    # --- 5. user inesistente errore --------------------------------------

    def test_send_to_unknown_user_errors(self):
        """`to_user='ghost'` → fail con error 'user_not_found'."""
        patch_tg, sent = self._patch_telegram()
        with patch_tg:
            res = self.send_messages.invoke({
                "messages": [{"to_user": "ghost", "body": "hello?"}],
                "via_channel": "telegram",
                "actor": "host",
            })
        self.assertFalse(res["ok"])
        self.assertEqual(res["fail_count"], 1)
        self.assertEqual(res["failed"][0]["error"], "user_not_found")
        # E nessun send è stato tentato
        self.assertEqual(len(sent), 0)


if __name__ == "__main__":
    unittest.main()
