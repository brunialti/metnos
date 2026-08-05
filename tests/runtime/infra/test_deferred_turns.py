"""Fase 7 A.1 — turni DIFFERITI su device offline (consenso esplicito §2.11).

Flusso: device unreachable → dialog «eseguo appena torna online?» → approve →
deferred_turns.add → al primo poll del device il server ri-esegue run_turn e
notifica via user_notices (A.2). TTL → expired + notifica onesta.

Run: `python3 -m pytest tests/runtime/infra/test_deferred_turns.py -xvs`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class DeferredStoreTests(unittest.TestCase):
    def setUp(self):
        import deferred_turns as dt
        self._orig = dt.DB_PATH
        self._orig_lock = dt.LOCK_PATH
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_defer_"))
        dt.DB_PATH = self.tmp / "deferred.jsonl"
        dt.LOCK_PATH = self.tmp / "deferred.lock"
        self.dt = dt
        self.owner_user_id = "owner-deferred-store"

    def tearDown(self):
        import shutil
        self.dt.DB_PATH = self._orig
        self.dt.LOCK_PATH = self._orig_lock
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_and_pending_roundtrip(self):
        rid = self.dt.add(device_id="dev1", device_name="PC-X",
                          query="cancella i file in D:/x", actor="host",
                          channel="http",
                          owner_user_id=self.owner_user_id)
        recs = self.dt.pending_for_device(
            "dev1", owner_user_id=self.owner_user_id)
        self.assertEqual([r["id"] for r in recs], [rid])
        self.assertEqual(recs[0]["state"], "pending")
        # altro device → vuoto
        self.assertEqual(self.dt.pending_for_device(
            "dev2", owner_user_id=self.owner_user_id), [])

    def test_mark_done_excludes_from_pending(self):
        rid = self.dt.add(device_id="dev1", device_name="PC-X",
                          query="q", actor="host", channel="http",
                          owner_user_id=self.owner_user_id)
        self.dt.mark(rid, "done", owner_user_id=self.owner_user_id)
        self.assertEqual(self.dt.pending_for_device(
            "dev1", owner_user_id=self.owner_user_id), [])

    def test_expired_marked_and_reported(self):
        import os
        os.environ["METNOS_DEFER_TTL_H"] = "0"  # scade subito
        try:
            rid = self.dt.add(device_id="dev1", device_name="PC-X",
                              query="q", actor="host", channel="http",
                              owner_user_id=self.owner_user_id)
            time.sleep(0.01)
            recs = self.dt.pending_for_device(
                "dev1", owner_user_id=self.owner_user_id)
            self.assertEqual(recs[0]["state"], "expired")
            # ora è marcato expired nello store → non più pending
            self.assertEqual(
                [r for r in self.dt.pending_for_device(
                    "dev1", owner_user_id=self.owner_user_id)
                 if r["state"] == "pending"], [])
        finally:
            os.environ.pop("METNOS_DEFER_TTL_H", None)


class DeferCompletionTests(unittest.TestCase):
    """orchestration `defer_turn`: approve → add; reject → no-op onesto."""

    def setUp(self):
        import deferred_turns as dt
        self._orig = dt.DB_PATH
        self._orig_lock = dt.LOCK_PATH
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_deferoc_"))
        dt.DB_PATH = self.tmp / "deferred.jsonl"
        dt.LOCK_PATH = self.tmp / "deferred.lock"
        self.dt = dt
        self.owner_user_id = "owner-deferred-completion"

    def tearDown(self):
        import shutil
        self.dt.DB_PATH = self._orig
        self.dt.LOCK_PATH = self._orig_lock
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dispatch(self, decision):
        import orchestration as orch
        oc = {"type": "defer_turn", "original_query": "cancella x su pc",
              "device_id": "dev9", "device_name": "PC-X",
              "conversation_id": "c1",
              "owner_user_id": self.owner_user_id}
        # via il dispatcher interno (stesso path del submit reale)
        return orch._dispatch_completion_for_test(oc, {"decision": decision}) \
            if hasattr(orch, "_dispatch_completion_for_test") else \
            self._via_process(oc, decision)

    def _via_process(self, oc, decision):
        # percorso reale: dialog_pending + process_completion_callback
        import uuid
        import dialog_pending as dp
        import orchestration as orch
        did = uuid.uuid4().hex[:16]
        sender = "http:test-defer"
        dp.save_pending(sender, did, {
            "dialog_id": did, "title": "t",
            "dialog": [{"var": "decision", "prompt": "?",
                        "schema": {"kind": "choice", "choices": [
                            {"label": "sì", "value": "approve"},
                            {"label": "no", "value": "reject"}]}}],
            "fmt": "dialogue", "fmt_arg": "auto",
            "values_collected": {}, "step_index": 0,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "actor": "host", "channel": "http", "timeout_s": 600,
            "owner_user_id": self.owner_user_id,
            "completed": False, "cancelled": False, "on_complete": oc,
        })
        dp.consume_pending_step(
            sender, did, "decision", decision,
            owner_user_id=self.owner_user_id)
        out = orch.process_completion_callback(sender, did, actor="host",
                                               channel="http",
                                               owner_user_id=self.owner_user_id)
        dp.cancel_pending(
            sender, did, owner_user_id=self.owner_user_id)
        return out.text

    def test_approve_queues(self):
        txt = self._dispatch("approve")
        recs = self.dt.pending_for_device(
            "dev9", owner_user_id=self.owner_user_id)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["query"], "cancella x su pc")
        self.assertEqual(recs[0]["actor"], "host")
        self.assertNotIn("<missing", txt)

    def test_reject_no_queue(self):
        txt = self._dispatch("reject")
        self.assertEqual(self.dt.pending_for_device(
            "dev9", owner_user_id=self.owner_user_id), [])
        self.assertNotIn("<missing", txt)


class OfferDialogTests(unittest.TestCase):
    def test_offer_builds_form_dialog(self):
        import agent_runtime as ar
        import dialog_pending as dp
        out = ar._offer_defer_dialog(
            query="cancella i file su pc-x", device_id="devZ",
            device_name="PC-X", actor="host", channel="http",
            conversation_id="c9", sender_id="http:test-offer",
            owner_user_id="owner-defer-offer")
        self.assertIsNotNone(out)
        self.assertIn("INLINE_FORM:/agent/dialog/", out["final_message"])
        self.assertNotIn("<missing", out["final_message"])
        cap = out["expandable_caps"][0]
        st = dp.load_pending(
            "http:test-offer", cap["dialog_id"],
            owner_user_id="owner-defer-offer")
        self.assertEqual(st["on_complete"]["type"], "defer_turn")
        self.assertEqual(st["on_complete"]["device_id"], "devZ")
        dp.cancel_pending(
            "http:test-offer", cap["dialog_id"],
            owner_user_id="owner-defer-offer")


class PendingDeferHttpRegressionTests(unittest.TestCase):
    """A repeated offline request is a retry, not an invalid form answer."""

    def setUp(self):
        import dialog_pending as dp
        from channels import daemon

        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_defer_http_"))
        self.dp = dp
        self.daemon = daemon
        self.orig_dialog_dir = dp.DIALOG_DIR
        self.orig_cap_dir = daemon.CAP_PENDING_DIR
        dp.DIALOG_DIR = self.tmp / "dialogs"
        daemon.CAP_PENDING_DIR = self.tmp / "caps"
        self.owner = "owner-defer-http"
        self.sender = "http:owner-defer-http:conv-ram"
        # Reproduce the legacy state coordinate visible in the failing turn.
        self.state_sender = "http:host"
        self.query = "quanta ram ha il pc-roberto"

    def tearDown(self):
        import shutil

        self.dp.DIALOG_DIR = self.orig_dialog_dir
        self.daemon.CAP_PENDING_DIR = self.orig_cap_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _offer(self):
        import agent_runtime as ar

        out = ar._offer_defer_dialog(
            query=self.query,
            device_id="device-pc-roberto",
            device_name="PC-ROBERTO",
            actor="host",
            channel="http",
            conversation_id="conv-ram",
            sender_id=self.state_sender,
            owner_user_id=self.owner,
        )
        self.assertIsNotNone(out)
        cap = out["expandable_caps"][0]
        self.daemon._cap_pending_save(
            self.sender,
            self.query,
            cap,
            "7361f297b0704bc2",
            owner_user_id=self.owner,
        )
        return cap

    def test_repeated_request_retires_stale_offer_and_falls_through(self):
        import http_routes_agent as routes

        cap = self._offer()
        reply = routes._apply_dialog_pending(
            self.sender,
            self.query,
            actor="host",
            channel="http",
            conversation_id="conv-ram",
            owner_user_id=self.owner,
            admit_only_valid_closed=True,
        )

        self.assertIsNone(reply)
        state = self.dp.load_pending(
            self.state_sender,
            cap["dialog_id"],
            owner_user_id=self.owner,
        )
        self.assertTrue(state["cancelled"])
        self.assertIsNone(self.daemon._cap_pending_load(
            self.sender, owner_user_id=self.owner))

    def test_invalid_http_reply_re_presents_the_form(self):
        import http_routes_agent as routes

        cap = self._offer()
        reply = routes._apply_dialog_pending(
            self.sender,
            "forse",
            actor="host",
            channel="http",
            conversation_id="conv-ram",
            owner_user_id=self.owner,
        )

        self.assertIn(
            f"INLINE_FORM:/agent/dialog/{cap['dialog_id']}/form", reply)
        self.assertEqual(len(self.dp.list_pending(
            self.state_sender, owner_user_id=self.owner)), 1)

    def test_short_circuited_pending_turn_is_durable(self):
        import config
        import http_routes_agent as routes
        import users

        original_turns = config.PATH_TURNS
        config.PATH_TURNS = self.tmp / "turns"
        try:
            with mock.patch.object(
                    users, "owner_deletion_started", return_value=False):
                routes._persist_pending_http_turn(
                    turn_id="856cc85b00000000",
                    query=self.query,
                    message="Ripresento la scelta.",
                    actor="host",
                    owner_user_id=self.owner,
                    conversation_id="conv-ram",
                )
            rows = (config.PATH_TURNS /
                    f"{time.strftime('%Y-%m-%d')}.jsonl").read_text(
                        encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            row = json.loads(rows[0])
            self.assertEqual(row["turn_id"], "856cc85b00000000")
            self.assertEqual(row["user_query"], self.query)
            self.assertEqual(row["final_message"], "Ripresento la scelta.")
            self.assertEqual(row["owner_user_id"], self.owner)
            self.assertEqual(row["mode"], "pending")
        finally:
            config.PATH_TURNS = original_turns


if __name__ == "__main__":
    unittest.main()
