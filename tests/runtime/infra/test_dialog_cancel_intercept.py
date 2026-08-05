"""Test per `_apply_dialog_cancel` in http_routes_agent (24/5/2026).

Bug live turn 7379cc8c: l'utente in chat scrive "annulla" mentre c'e' un
dialog pending → routing va a `undo_last_turn` invece di `cancel_pending`.
Fix: intercetta pre-pipeline. Coperture:

  - undo pattern + dialog pending → cancella dialog + msg "Dialogo annullato."
  - undo pattern + nessun dialog pending → None (caller prosegue normale).
  - query NON undo + dialog pending → None (no intercept).
  - cancellazione idempotente: re-invocare ritorna None (dialog gia' chiuso).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class DialogCancelInterceptTests(unittest.TestCase):

    def setUp(self):
        import dialog_pending as DP
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_dialog_dir = DP.DIALOG_DIR
        DP.DIALOG_DIR = Path(self.tmp.name)
        self.DP = DP
        # Lazy import dopo path setup
        from http_routes_agent import _apply_dialog_cancel
        self.fn = _apply_dialog_cancel
        self.sender = "http:host:c_test_conv"
        self.owner_user_id = "owner-dialog-cancel"

    def tearDown(self):
        self.DP.DIALOG_DIR = self._orig_dialog_dir
        self.tmp.cleanup()

    def _create_pending(self, dialog_id: str = "dlg_xyz") -> None:
        self.DP.save_pending(self.sender, dialog_id, {
            "dialog_id": dialog_id,
            "executor": "request_disambiguation_from_user",
            "title": "Test",
            "dialog": [{"var": "x", "prompt": "Q",
                         "schema": {"kind": "text"}}],
            # started_at fresco: il soft-TTL (DEFAULT_TTL_S=60s, 29/5/2026)
            # scarterebbe un timestamp hardcoded vecchio prima dell'intercept.
            "started_at": self.DP._utc_now_iso(),
            "owner_user_id": self.owner_user_id,
            "completed": False,
            "cancelled": False,
        })

    def test_undo_with_pending_cancels_and_returns_msg(self):
        from messages import get as _msg  # §11 i18n: msg risolto da DB
        self._create_pending()
        msg = self.fn(
            self.sender, "annulla", owner_user_id=self.owner_user_id)
        self.assertEqual(msg, _msg("MSG_DIALOG_CANCELLED"))
        # Verifica cancellato
        self.assertEqual(self.DP.list_pending(
            self.sender, owner_user_id=self.owner_user_id), [])

    def test_undo_without_pending_returns_none(self):
        msg = self.fn(
            self.sender, "annulla", owner_user_id=self.owner_user_id)
        self.assertIsNone(msg)

    def test_non_undo_query_returns_none_even_with_pending(self):
        self._create_pending()
        msg = self.fn(
            self.sender, "che ore sono", owner_user_id=self.owner_user_id)
        self.assertIsNone(msg)
        # Dialog ancora pending
        self.assertEqual(len(self.DP.list_pending(
            self.sender, owner_user_id=self.owner_user_id)), 1)

    def test_undo_variants_all_intercept(self):
        from messages import get as _msg  # §11 i18n
        # Ogni variante UNDO con pending → cancella
        for query in ("annulla", "undo", "ripristina", "rollback",
                      "annulla l'ultima azione"):
            with self.subTest(query=query):
                self._create_pending(f"dlg_{abs(hash(query))%10000}")
                msg = self.fn(
                    self.sender, query, owner_user_id=self.owner_user_id)
                self.assertEqual(msg, _msg("MSG_DIALOG_CANCELLED"),
                                  f"failed on query={query!r}")

    def test_multiple_pending_count_in_message(self):
        from messages import get as _msg  # §11 i18n
        self._create_pending("dlg_a")
        self._create_pending("dlg_b")
        self._create_pending("dlg_c")
        msg = self.fn(
            self.sender, "annulla", owner_user_id=self.owner_user_id)
        self.assertEqual(msg, _msg("MSG_DIALOG_CANCELLED_N", n=3))

    def test_idempotent_second_call_returns_none(self):
        self._create_pending()
        self.fn(self.sender, "annulla", owner_user_id=self.owner_user_id)
        msg = self.fn(
            self.sender, "annulla", owner_user_id=self.owner_user_id)
        self.assertIsNone(msg)


if __name__ == "__main__":
    unittest.main()
