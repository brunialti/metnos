"""Unit test per `runtime/active_sessions` — single-session-per-user.

ADR Phase 7 Phase 1 (12/5/2026). Verifica:
- Migration idempotente
- register_session single + conflict
- confirm_takeover atomic + token one-shot
- touch_session ritorna False su revoked
- revoke_session idempotente
- list_sessions_for_user
- Concorrenza register (race-safe)

NIENTE LLM (§7.9). I test usano `METNOS_USERS_DB` env per isolare DB.
"""
from __future__ import annotations

import importlib
import os
import sys
import threading
import time
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class ActiveSessionsBase(unittest.TestCase):

    def setUp(self):
        # Isola DB per ogni test. conftest._isolate_home_for_legacy_image_tests
        # gia' redirige HOME a tmp_path; aggiungiamo METNOS_USERS_DB esplicito
        # per evitare ambiguita' nel risolutore.
        tmp = Path(os.environ.get("HOME", "/tmp")) / f"users_{id(self)}.db"
        os.environ["METNOS_USERS_DB"] = str(tmp)
        # Reload moduli che leggono env all'import.
        import users
        import active_sessions
        importlib.reload(users)
        importlib.reload(active_sessions)
        self.users = users
        self.active_sessions = active_sessions
        # Bootstrap host: serve user_id valido per i test.
        users.init_db()
        self.host = users.list_users(role="host")[0]

    def tearDown(self):
        # Pulisci pending takeover state global per evitare cross-talk fra test.
        from active_sessions import _PENDING_TAKEOVERS
        _PENDING_TAKEOVERS.clear()
        from active_sessions import _SUBSCRIBERS
        _SUBSCRIBERS.clear()


class TestMigration(ActiveSessionsBase):

    def test_migration_idempotent(self):
        """init_db chiamata N volte non solleva ne' duplica schema."""
        for _ in range(5):
            self.active_sessions.init_db()
        # Verify schema present
        import sqlite3
        conn = sqlite3.connect(os.environ["METNOS_USERS_DB"])
        try:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(active_sessions)"
            ).fetchall()]
        finally:
            conn.close()
        expected = {"id", "user_id", "device_token", "device_label",
                    "channel", "started_at", "last_seen_at",
                    "revoked_at", "revoke_reason"}
        self.assertTrue(expected.issubset(set(cols)),
                        f"missing columns: {expected - set(cols)}")


class TestRegisterSession(ActiveSessionsBase):

    def test_register_first_session_returns_device_token(self):
        r = self.active_sessions.register_session(
            self.host["id"], "http", device_label="Chrome desktop",
        )
        self.assertFalse(r["conflict"])
        self.assertIn("device_token", r)
        self.assertEqual(len(r["device_token"]), 32)  # uuid4 hex

    def test_register_invalid_channel_raises(self):
        with self.assertRaises(ValueError):
            self.active_sessions.register_session(
                self.host["id"], "smtp", device_label="x",
            )

    def test_register_invalid_user_raises(self):
        with self.assertRaises(ValueError):
            self.active_sessions.register_session(
                "", "http", device_label="x",
            )

    def test_register_conflict_returns_takeover_token(self):
        r1 = self.active_sessions.register_session(
            self.host["id"], "http", device_label="Device A",
        )
        self.assertFalse(r1["conflict"])
        r2 = self.active_sessions.register_session(
            self.host["id"], "http", device_label="Device B",
        )
        self.assertTrue(r2["conflict"])
        self.assertEqual(r2["existing"]["device_label"], "Device A")
        self.assertIn("takeover_token", r2)
        # Token sono uuid hex distinti
        self.assertNotEqual(r1["device_token"], r2["takeover_token"])

    def test_register_different_channel_no_conflict(self):
        """Stessa user_id ma channel diverso: niente conflict."""
        r1 = self.active_sessions.register_session(
            self.host["id"], "http", device_label="Browser",
        )
        r2 = self.active_sessions.register_session(
            self.host["id"], "telegram", device_label="Telegram",
        )
        self.assertFalse(r1["conflict"])
        self.assertFalse(r2["conflict"])


class TestTakeover(ActiveSessionsBase):

    def test_takeover_atomic_revokes_old_creates_new(self):
        r1 = self.active_sessions.register_session(
            self.host["id"], "http", device_label="Device A",
        )
        old_token = r1["device_token"]
        r2 = self.active_sessions.register_session(
            self.host["id"], "http", device_label="Device B",
        )
        tk = r2["takeover_token"]
        res = self.active_sessions.confirm_takeover(tk, new_device_label="Device B")
        self.assertIn("device_token", res)
        self.assertEqual(res["revoked_device_token"], old_token)
        # Old session is revoked
        old = self.active_sessions.get_session(old_token)
        self.assertIsNotNone(old["revoked_at"])
        self.assertEqual(old["revoke_reason"], "takeover")
        # New session is active
        new = self.active_sessions.get_session(res["device_token"])
        self.assertIsNone(new["revoked_at"])
        self.assertEqual(new["device_label"], "Device B")

    def test_takeover_token_one_shot(self):
        """Il takeover_token e' consumato una volta sola."""
        self.active_sessions.register_session(
            self.host["id"], "http", device_label="A",
        )
        r2 = self.active_sessions.register_session(
            self.host["id"], "http", device_label="B",
        )
        tk = r2["takeover_token"]
        self.active_sessions.confirm_takeover(tk)
        with self.assertRaises(ValueError):
            self.active_sessions.confirm_takeover(tk)

    def test_takeover_unknown_token_raises(self):
        with self.assertRaises(ValueError):
            self.active_sessions.confirm_takeover("ffff" * 8)


class TestTouchAndRevoke(ActiveSessionsBase):

    def test_touch_active_session_returns_true(self):
        r = self.active_sessions.register_session(
            self.host["id"], "http", device_label="A",
        )
        self.assertTrue(self.active_sessions.touch_session(r["device_token"]))

    def test_touch_revoked_returns_false(self):
        r = self.active_sessions.register_session(
            self.host["id"], "http", device_label="A",
        )
        self.active_sessions.revoke_session(r["device_token"], reason="manual")
        self.assertFalse(self.active_sessions.touch_session(r["device_token"]))

    def test_touch_unknown_returns_false(self):
        self.assertFalse(self.active_sessions.touch_session("ffff" * 8))

    def test_touch_updates_last_seen_at(self):
        r = self.active_sessions.register_session(
            self.host["id"], "http", device_label="A",
        )
        s1 = self.active_sessions.get_session(r["device_token"])
        time.sleep(1.1)  # iso second resolution
        self.active_sessions.touch_session(r["device_token"])
        s2 = self.active_sessions.get_session(r["device_token"])
        self.assertGreater(s2["last_seen_at"], s1["last_seen_at"])

    def test_revoke_idempotent(self):
        r = self.active_sessions.register_session(
            self.host["id"], "http", device_label="A",
        )
        self.assertTrue(self.active_sessions.revoke_session(r["device_token"]))
        self.assertFalse(self.active_sessions.revoke_session(r["device_token"]))


class TestListSessions(ActiveSessionsBase):

    def test_list_sessions_for_user_includes_history(self):
        # 2 sessioni: una revocata, una attiva (via takeover).
        r1 = self.active_sessions.register_session(
            self.host["id"], "http", device_label="A",
        )
        r2 = self.active_sessions.register_session(
            self.host["id"], "http", device_label="B",
        )
        self.active_sessions.confirm_takeover(r2["takeover_token"])
        all_s = self.active_sessions.list_sessions_for_user(self.host["id"])
        self.assertEqual(len(all_s), 2)
        # piu' recenti prima
        labels = [s["device_label"] for s in all_s]
        self.assertIn("A", labels)
        self.assertIn("B", labels)
        revoked_count = sum(1 for s in all_s if s["revoked_at"])
        self.assertEqual(revoked_count, 1)


class TestRaceSafety(ActiveSessionsBase):

    def test_concurrent_register_only_one_succeeds(self):
        """Due register paralleli sullo stesso (user, channel): solo uno
        riceve device_token, l'altro riceve conflict. Niente doppia
        sessione attiva."""
        results = []
        errors = []
        barrier = threading.Barrier(5)

        def attempt():
            try:
                barrier.wait()
                r = self.active_sessions.register_session(
                    self.host["id"], "http", device_label="parallel",
                )
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=attempt) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(len(errors), 0, f"errors: {errors}")
        winners = [r for r in results if not r.get("conflict")]
        losers = [r for r in results if r.get("conflict")]
        self.assertEqual(len(winners), 1,
                         f"expected 1 winner, got {len(winners)}")
        self.assertEqual(len(losers), 4)

    def test_get_active_for_after_takeover(self):
        r1 = self.active_sessions.register_session(
            self.host["id"], "http", device_label="A",
        )
        r2 = self.active_sessions.register_session(
            self.host["id"], "http", device_label="B",
        )
        new = self.active_sessions.confirm_takeover(r2["takeover_token"])
        active = self.active_sessions.get_active_for(self.host["id"], "http")
        self.assertEqual(active["device_token"], new["device_token"])


if __name__ == "__main__":
    unittest.main()
