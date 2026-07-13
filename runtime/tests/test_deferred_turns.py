"""Fase 7 A.1 — turni DIFFERITI su device offline (consenso esplicito §2.11).

Flusso: device unreachable → dialog «eseguo appena torna online?» → approve →
deferred_turns.add → al primo poll del device il server ri-esegue run_turn e
notifica via user_notices (A.2). TTL → expired + notifica onesta.

Run: `python3 -m pytest runtime/tests/test_deferred_turns.py -xvs`.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class DeferredStoreTests(unittest.TestCase):
    def setUp(self):
        import deferred_turns as dt
        self._orig = dt.DB_PATH
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_defer_"))
        dt.DB_PATH = self.tmp / "deferred.jsonl"
        self.dt = dt

    def tearDown(self):
        import shutil
        self.dt.DB_PATH = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_and_pending_roundtrip(self):
        rid = self.dt.add(device_id="dev1", device_name="PC-X",
                          query="cancella i file in D:/x", actor="host",
                          channel="http")
        recs = self.dt.pending_for_device("dev1")
        self.assertEqual([r["id"] for r in recs], [rid])
        self.assertEqual(recs[0]["state"], "pending")
        # altro device → vuoto
        self.assertEqual(self.dt.pending_for_device("dev2"), [])

    def test_mark_done_excludes_from_pending(self):
        rid = self.dt.add(device_id="dev1", device_name="PC-X",
                          query="q", actor="host", channel="http")
        self.dt.mark(rid, "done")
        self.assertEqual(self.dt.pending_for_device("dev1"), [])

    def test_expired_marked_and_reported(self):
        import os
        os.environ["METNOS_DEFER_TTL_H"] = "0"  # scade subito
        try:
            rid = self.dt.add(device_id="dev1", device_name="PC-X",
                              query="q", actor="host", channel="http")
            time.sleep(0.01)
            recs = self.dt.pending_for_device("dev1")
            self.assertEqual(recs[0]["state"], "expired")
            # ora è marcato expired nello store → non più pending
            self.assertEqual(
                [r for r in self.dt.pending_for_device("dev1")
                 if r["state"] == "pending"], [])
        finally:
            os.environ.pop("METNOS_DEFER_TTL_H", None)


class DeferCompletionTests(unittest.TestCase):
    """orchestration `defer_turn`: approve → add; reject → no-op onesto."""

    def setUp(self):
        import deferred_turns as dt
        self._orig = dt.DB_PATH
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_deferoc_"))
        dt.DB_PATH = self.tmp / "deferred.jsonl"
        self.dt = dt

    def tearDown(self):
        import shutil
        self.dt.DB_PATH = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dispatch(self, decision):
        import orchestration as orch
        oc = {"type": "defer_turn", "original_query": "cancella x su pc",
              "device_id": "dev9", "device_name": "PC-X",
              "conversation_id": "c1"}
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
            "completed": False, "cancelled": False, "on_complete": oc,
        })
        dp.consume_pending_step(sender, did, "decision", decision)
        out = orch.process_completion_callback(sender, did, actor="host",
                                               channel="http")
        dp.cancel_pending(sender, did)
        return out.text

    def test_approve_queues(self):
        txt = self._dispatch("approve")
        recs = self.dt.pending_for_device("dev9")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["query"], "cancella x su pc")
        self.assertEqual(recs[0]["actor"], "host")
        self.assertNotIn("<missing", txt)

    def test_reject_no_queue(self):
        txt = self._dispatch("reject")
        self.assertEqual(self.dt.pending_for_device("dev9"), [])
        self.assertNotIn("<missing", txt)


class OfferDialogTests(unittest.TestCase):
    def test_offer_builds_form_dialog(self):
        import agent_runtime as ar
        import dialog_pending as dp
        out = ar._offer_defer_dialog(
            query="cancella i file su pc-x", device_id="devZ",
            device_name="PC-X", actor="host", channel="http",
            conversation_id="c9", sender_id="http:test-offer")
        self.assertIsNotNone(out)
        self.assertIn("INLINE_FORM:/agent/dialog/", out["final_message"])
        self.assertNotIn("<missing", out["final_message"])
        cap = out["expandable_caps"][0]
        st = dp.load_pending("http:test-offer", cap["dialog_id"])
        self.assertEqual(st["on_complete"]["type"], "defer_turn")
        self.assertEqual(st["on_complete"]["device_id"], "devZ")
        dp.cancel_pending("http:test-offer", cap["dialog_id"])


if __name__ == "__main__":
    unittest.main()
