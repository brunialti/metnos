"""Unit test per `runtime/active_sessions` — single-session-per-user.

ADR Phase 7 Phase 1 (12/5/2026). Verifica:
- Migration idempotente
- register_session single + conflict
- le due semantiche di takeover (conversazione corrente / precedente)
- confirm_takeover atomic + token one-shot + protezione dalle race
- touch_session migra il conversation_id legacy in modo write-once
- revoke_session idempotente
- list_sessions_for_user
- Concorrenza register (race-safe)

NIENTE LLM (§7.9). I test usano `METNOS_USERS_DB` env per isolare DB.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class ActiveSessionsBase(unittest.TestCase):

    def setUp(self):
        # Isola DB per ogni test in una dir temporanea dedicata, auto-pulita in
        # tearDown. NON usare HOME: senza conftest attivo (unittest diretto)
        # finirebbe nella home reale lasciando file orfani `users_*.db`.
        self._tmpdir = tempfile.mkdtemp(prefix="metnos_test_users_")
        os.environ["METNOS_USERS_DB"] = str(Path(self._tmpdir) / "users.db")
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
        # Rimuovi la dir temporanea del DB di test (no leak in HOME).
        shutil.rmtree(getattr(self, "_tmpdir", ""), ignore_errors=True)


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
                    "channel", "conversation_id", "started_at", "last_seen_at",
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
        self.assertRegex(r["conversation_id"], r"^c_")
        self.assertEqual(len(r["device_token"]), 32)  # uuid4 hex

    def test_register_preserves_client_conversation_and_binds_owner(self):
        conv_id = "c_browser_alpha"
        result = self.active_sessions.register_session(
            self.host["id"], "http", conversation_id=conv_id,
            legacy_actor="host",
        )
        self.assertEqual(result["conversation_id"], conv_id)
        conversation = self.active_sessions.get_conversation(conv_id)
        self.assertEqual(conversation["user_id"], self.host["id"])
        self.assertEqual(conversation["legacy_actor"], "host")

    def test_conversation_id_cannot_cross_users(self):
        conv_id = "c_owner_bound"
        first = self.active_sessions.register_session(
            self.host["id"], "http", conversation_id=conv_id,
        )
        self.active_sessions.revoke_session(first["device_token"])
        with self.assertRaisesRegex(ValueError, "conversation_owner_mismatch"):
            self.active_sessions.register_session(
                "different-user", "http", conversation_id=conv_id,
            )

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
        self.assertTrue(r2["existing"]["can_continue"])
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

    def test_register_different_users_same_channel_are_independent(self):
        """La lease e la conversazione sono separate per principal."""
        guest = self.users.create_user(
            "session_guest",
            owner_user_id=self.host["id"],
        )
        host_session = self.active_sessions.register_session(
            self.host["id"], "http", device_label="Host browser",
            conversation_id="c_host_browser",
        )
        guest_session = self.active_sessions.register_session(
            guest["id"], "http", device_label="Guest browser",
            conversation_id="c_guest_browser",
        )

        self.assertFalse(host_session["conflict"])
        self.assertFalse(guest_session["conflict"])
        self.assertNotEqual(
            host_session["device_token"], guest_session["device_token"],
        )
        self.assertEqual(
            self.active_sessions.get_active_for(self.host["id"], "http")
            ["conversation_id"],
            "c_host_browser",
        )
        self.assertEqual(
            self.active_sessions.get_active_for(guest["id"], "http")
            ["conversation_id"],
            "c_guest_browser",
        )


class TestTakeover(ActiveSessionsBase):

    def test_continue_existing_transfers_old_conversation(self):
        conv_a = "c_conversation_a"
        conv_b = "c_conversation_b"
        first = self.active_sessions.register_session(
            self.host["id"], "http", device_label="A",
            conversation_id=conv_a,
        )
        conflict = self.active_sessions.register_session(
            self.host["id"], "http", device_label="B",
            conversation_id=conv_b,
        )
        result = self.active_sessions.confirm_takeover(
            conflict["takeover_token"], mode="continue_existing",
            expected_user_id=self.host["id"],
        )
        self.assertEqual(result["conversation_id"], conv_a)
        self.assertEqual(result["mode"], "continue_existing")
        active = self.active_sessions.get_active_for(self.host["id"], "http")
        self.assertEqual(active["conversation_id"], conv_a)
        valid, reason = self.active_sessions.validate_writer(
            first["device_token"], user_id=self.host["id"],
            channel="http", conversation_id=conv_a,
        )
        self.assertFalse(valid)
        self.assertEqual(reason, "session_revoked")

    def test_activate_current_preserves_new_device_conversation(self):
        self.active_sessions.register_session(
            self.host["id"], "http", conversation_id="c_old_device",
        )
        conflict = self.active_sessions.register_session(
            self.host["id"], "http", conversation_id="c_current_device",
        )
        result = self.active_sessions.confirm_takeover(
            conflict["takeover_token"], mode="activate_current",
            expected_user_id=self.host["id"],
        )
        self.assertEqual(result["conversation_id"], "c_current_device")
        ok, reason = self.active_sessions.validate_writer(
            result["device_token"], user_id=self.host["id"],
            channel="http", conversation_id="c_current_device",
        )
        self.assertTrue(ok, reason)

    def test_stale_conflict_cannot_revoke_newer_writer(self):
        first = self.active_sessions.register_session(
            self.host["id"], "http", conversation_id="c_first_writer",
        )
        conflict_b = self.active_sessions.register_session(
            self.host["id"], "http", conversation_id="c_second_writer",
        )
        conflict_c = self.active_sessions.register_session(
            self.host["id"], "http", conversation_id="c_third_writer",
        )
        second = self.active_sessions.confirm_takeover(
            conflict_b["takeover_token"], mode="activate_current",
        )
        with self.assertRaisesRegex(ValueError, "active_session_changed"):
            self.active_sessions.confirm_takeover(
                conflict_c["takeover_token"], mode="activate_current",
            )
        active = self.active_sessions.get_active_for(self.host["id"], "http")
        self.assertEqual(active["device_token"], second["device_token"])
        self.assertNotEqual(active["device_token"], first["device_token"])

    def test_wrong_owner_does_not_consume_takeover_token(self):
        self.active_sessions.register_session(
            self.host["id"], "http", conversation_id="c_owned_takeover",
        )
        conflict = self.active_sessions.register_session(
            self.host["id"], "http", conversation_id="c_owned_candidate",
        )
        with self.assertRaisesRegex(ValueError, "takeover_owner_mismatch"):
            self.active_sessions.confirm_takeover(
                conflict["takeover_token"], expected_user_id="attacker",
            )
        result = self.active_sessions.confirm_takeover(
            conflict["takeover_token"], expected_user_id=self.host["id"],
        )
        self.assertEqual(result["conversation_id"], "c_owned_candidate")

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

    def test_legacy_touch_without_conversation_does_not_invent_one(self):
        import sqlite3
        self.active_sessions.init_db()
        conn = sqlite3.connect(os.environ["METNOS_USERS_DB"])
        try:
            conn.execute(
                "INSERT INTO active_sessions "
                "(user_id,device_token,device_label,channel,conversation_id,"
                "started_at,last_seen_at) VALUES (?,?,?,?,?,?,?)",
                (self.host["id"], "legacy-token", "legacy", "http", None,
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()

        untouched = self.active_sessions.touch_session(
            "legacy-token", user_id=self.host["id"],
        )
        self.assertIsNone(untouched["conversation_id"])
        bound = self.active_sessions.touch_session(
            "legacy-token", user_id=self.host["id"],
            conversation_id="c_legacy_bound", legacy_actor="host",
        )
        self.assertEqual(bound["conversation_id"], "c_legacy_bound")
        # Write-once: un ping successivo non puo' spostare la sessione.
        rebound = self.active_sessions.touch_session(
            "legacy-token", user_id=self.host["id"],
            conversation_id="c_other_value",
        )
        self.assertEqual(rebound["conversation_id"], "c_legacy_bound")

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
