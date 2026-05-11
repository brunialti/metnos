"""Test del task scheduler v2 `promoter_digest` (jobs/promoter_digest.py).

Copre:
1. Query notification candidates corretta.
2. Telegram inline keyboard generata.
3. Cap N=10 per fire.
4. Idempotenza: re-fire non notifica due volte (notified_at set).
5. Disabilitato via env METNOS_PROMOTER_NOTIFY_ADMIN=false.
6. Multi-message split per body > 4000 char.

Mock di TelegramChannel.send per zero rete §7.9.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class _BaseDigestTest(unittest.TestCase):

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp(prefix="promoter_digest_"))
        self._db = self._tmpdir / "promoter.sqlite"
        self._audit_dir = self._tmpdir / "audit"
        self._env = mock.patch.dict("os.environ", {
            "METNOS_PROMOTER_DB": str(self._db),
            "METNOS_PROMOTER_AUDIT_DIR": str(self._audit_dir),
            "METNOS_PROMOTER_NOTIFY_ADMIN": "true",
            "HOME": str(self._tmpdir),
        })
        self._env.start()
        for mod in ("jobs.promoter_digest", "jobs.promoter_state"):
            sys.modules.pop(mod, None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _seed_promoted_grace(self, proposal_id: str, name: str,
                              example: str = "esempio pratico",
                              notified_at: str | None = None) -> None:
        from jobs.promoter_state import ensure_schema
        conn = sqlite3.connect(str(self._db))
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO proposal_promote "
            "(proposal_id, name, state, promoted_at, grace_until, "
            " practical_example, notified_at, created_at) "
            "VALUES (?, ?, 'promoted_grace', '2026-05-11T00:00:00Z', "
            " '2026-05-14T00:00:00Z', ?, ?, '2026-05-11T00:00:00Z')",
            (proposal_id, name, example, notified_at),
        )
        conn.commit()
        conn.close()


# ─── 1. Query notification candidates corretta ────────────────────────────


class TestQueryCandidates(_BaseDigestTest):

    def test_pending_notification_returns_only_unnotified_grace(self):
        from jobs.promoter_state import pending_notification
        self._seed_promoted_grace("q_001", "find_widgets")
        self._seed_promoted_grace("q_002", "find_widgets",
                                    notified_at="2026-05-11T07:00:00Z")
        rows = pending_notification()
        ids = {r["proposal_id"] for r in rows}
        self.assertIn("q_001", ids)
        self.assertNotIn("q_002", ids)


# ─── 2. Telegram inline keyboard generata ─────────────────────────────────


class TestInlineKeyboard(_BaseDigestTest):

    def test_keyboard_format_ok_rollback(self):
        from jobs.promoter_digest import _build_inline_keyboard
        kb = _build_inline_keyboard("foo_bar_123")
        self.assertEqual(len(kb), 1)
        self.assertEqual(len(kb[0]), 2)
        self.assertEqual(kb[0][0]["text"], "ok")
        self.assertEqual(kb[0][0]["data"], "promoter:foo_bar_123:ok")
        self.assertEqual(kb[0][1]["text"], "rollback")
        self.assertEqual(kb[0][1]["data"], "promoter:foo_bar_123:rollback")
        # callback_data length < 64 byte (limite Telegram).
        for row in kb:
            for btn in row:
                self.assertLess(len(btn["data"].encode("utf-8")), 64)


# ─── 3. Cap N=10 per fire ─────────────────────────────────────────────────


class TestCap(_BaseDigestTest):

    def test_cap_n10_enforced(self):
        # Disabilita aggregated mode (E3 11/5/2026) per testare il path
        # per-item legacy con cap=10.
        os.environ["METNOS_PROMOTER_DIGEST_AGGREGATED"] = "false"
        for i in range(15):
            self._seed_promoted_grace(f"cap_{i:02d}", "find_widgets")
        # Pop PRIMA del patch cosi' il modulo fresco vede il patch.
        sys.modules.pop("jobs.promoter_digest", None)
        with mock.patch(
            "jobs.promoter_digest._resolve_admin_recipient",
            return_value=("123456", None),
        ), mock.patch(
            "jobs.promoter_digest._send_to_admin", return_value=(True, None),
        ):
            from jobs.promoter_digest import task_promoter_digest
            result = task_promoter_digest()
        self.assertEqual(result["metadata"]["cap"], 10)
        self.assertLessEqual(result["ok_count"], 10)
        # 5 restano unnotified.
        from jobs.promoter_state import pending_notification
        remaining = pending_notification()
        self.assertEqual(len(remaining), 5)


# ─── 4. Idempotenza re-fire ───────────────────────────────────────────────


class TestIdempotency(_BaseDigestTest):

    def test_rerun_does_not_renotify(self):
        # 1 item solo: sotto AGGREGATED_THRESHOLD=3, resta path per-item.
        self._seed_promoted_grace("idem_001", "find_widgets")
        sent_count = {"n": 0}

        def _send_mock(recipient, body, keyboard):
            sent_count["n"] += 1
            return (True, None)

        with mock.patch(
            "jobs.promoter_digest._resolve_admin_recipient",
            return_value=("123456", None),
        ), mock.patch(
            "jobs.promoter_digest._send_to_admin", side_effect=_send_mock,
        ):
            from jobs.promoter_digest import task_promoter_digest
            r1 = task_promoter_digest()
            r2 = task_promoter_digest()
        self.assertEqual(r1["ok_count"], 1)
        self.assertEqual(r2["ok_count"], 0)
        # Inviato una volta sola.
        self.assertEqual(sent_count["n"], 1)


# ─── 5. Disabilitato via env METNOS_PROMOTER_NOTIFY_ADMIN=false ───────────


class TestDisableViaEnv(_BaseDigestTest):

    def test_notify_disabled(self):
        os.environ["METNOS_PROMOTER_NOTIFY_ADMIN"] = "false"
        self._seed_promoted_grace("dis_001", "find_widgets")
        from jobs.promoter_digest import task_promoter_digest
        # Force re-read env
        sys.modules.pop("jobs.promoter_digest", None)
        from jobs.promoter_digest import task_promoter_digest as t2
        result = t2()
        self.assertEqual(result["ok_count"], 0)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["metadata"]["reason"],
                          "notify_disabled_via_env")


# ─── 6. Multi-message split per body > 4000 char ──────────────────────────


class TestMultiMessageSplit(_BaseDigestTest):

    def test_split_text_for_telegram_basic(self):
        from jobs.promoter_digest import _split_text_for_telegram
        # Short → un solo chunk.
        chunks = _split_text_for_telegram("hello world", max_len=4000)
        self.assertEqual(chunks, ["hello world"])

    def test_split_text_breaks_on_newline(self):
        from jobs.promoter_digest import _split_text_for_telegram
        # Body 5000 char con newline a meta'.
        body = ("riga uno\n" * 250) + ("riga due\n" * 300)
        chunks = _split_text_for_telegram(body, max_len=2000)
        self.assertGreater(len(chunks), 1)
        # Ogni chunk <= max_len.
        for c in chunks:
            self.assertLessEqual(len(c), 2000)
        # Concatenare tutti i chunks (con \n di join) ricostruisce il body
        # essenzialmente — meglio: il join + lstrip non perde caratteri
        # tranne lstrip di \n leading dopo cut su newline.
        joined = "\n".join(chunks)
        # I caratteri raw vanno preservati a parte i \n di stripping interno.
        self.assertEqual(len(joined.replace("\n", "")),
                          len(body.replace("\n", "")))

    def test_split_text_hard_cut_when_no_newline(self):
        from jobs.promoter_digest import _split_text_for_telegram
        body = "a" * 5000  # no newline → hard cut a max_len.
        chunks = _split_text_for_telegram(body, max_len=2000)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 2000)
        self.assertEqual("".join(chunks), body)

    def test_long_body_triggers_multi_send(self):
        """Body > 4000 → multipli send_to_admin call, keyboard solo all'ultimo.

        Disabilita aggregated (E3): l'aggregato manda UN messaggio piccolo,
        non chunked. Questo test esercita lo split per-item legacy.
        """
        os.environ["METNOS_PROMOTER_DIGEST_AGGREGATED"] = "false"
        long_example = "lorem ipsum\n" * 800  # ~10kB
        self._seed_promoted_grace("multi_001", "find_widgets",
                                    example=long_example)
        sends: list[tuple[str, list[list[dict]] | None]] = []

        def _send_mock(recipient, body, keyboard):
            sends.append((body, keyboard))
            return (True, None)

        sys.modules.pop("jobs.promoter_digest", None)
        with mock.patch(
            "jobs.promoter_digest._resolve_admin_recipient",
            return_value=("123456", None),
        ), mock.patch(
            "jobs.promoter_digest._send_to_admin", side_effect=_send_mock,
        ):
            from jobs.promoter_digest import task_promoter_digest
            task_promoter_digest()
        # Multipli chunk inviati.
        self.assertGreater(len(sends), 1)
        # Solo l'ultimo ha keyboard.
        for body, kb in sends[:-1]:
            self.assertIsNone(kb)
        self.assertIsNotNone(sends[-1][1])


# ─── 7. Aggregated mode triggers form link (E3, 11/5/2026) ────────────────


class TestAggregatedMode(_BaseDigestTest):

    def test_threshold_triggers_aggregated_message(self):
        """3+ grace items → UN messaggio aggregato con bottone open_form."""
        os.environ["METNOS_PROMOTER_DIGEST_AGGREGATED"] = "true"
        for i in range(4):
            self._seed_promoted_grace(f"agg_{i:02d}", "find_x")
        sends: list[tuple[str, list[list[dict]] | None]] = []

        def _send_mock(recipient, body, keyboard):
            sends.append((body, keyboard))
            return (True, None)

        sys.modules.pop("jobs.promoter_digest", None)
        with mock.patch(
            "jobs.promoter_digest._resolve_admin_recipient",
            return_value=("123456", None),
        ), mock.patch(
            "jobs.promoter_digest._send_to_admin", side_effect=_send_mock,
        ):
            from jobs.promoter_digest import task_promoter_digest
            result = task_promoter_digest()
        # UN solo messaggio.
        self.assertEqual(len(sends), 1)
        body, kb = sends[0]
        self.assertIn("decisioni in attesa", body)
        self.assertIn("Apri il form review", body)
        # Keyboard col bottone open_form.
        self.assertIsNotNone(kb)
        flat = [b for row in kb for b in row]
        self.assertTrue(any(b["data"] == "promoter:_aggregated:open_form"
                              for b in flat))
        self.assertEqual(result["metadata"]["mode"], "aggregated")
        # Tutti i grace sono stati marcati notified.
        from jobs.promoter_state import pending_notification
        self.assertEqual(len(pending_notification()), 0)


if __name__ == "__main__":
    unittest.main()
