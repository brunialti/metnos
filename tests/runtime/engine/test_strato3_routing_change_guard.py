"""Anti-deadlock strato-3 (bug delete_persons, 13/6/2026).

`_strato3_routing_changed` deve NON escalare al dialog 5-azioni quando il
routing CORRENTE produrrebbe una pipeline MAI rifiutata (i ✗ storici sono
stantii — es. dopo undeprecate di delete_persons / fix intent). Deve invece
PRESERVARE l'escalation quando il routing riprodurrebbe ancora una pipeline
rifiutata (deadlock genuino). Decisione esatta: propone (NON esegue) con le
funzioni di produzione e confronta la firma della pipeline col rejected-set.

Test deterministici (§7.9): intent + pool + proposer monkeypatchati, nessuna
LLM, nessuna esecuzione executor.

Run: python3 -m pytest tests/runtime/engine/test_strato3_routing_change_guard.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

QUERY = "cancella l'enrollement di roberto brunialti"
OWNER = "routing-guard-test-user"


class _FakeProposer:
    def __init__(self, tools):
        self._tools = tools

    def propose(self, **kw):
        from engine.types import Framework
        return Framework.from_dict(
            {"steps": [{"tool": t, "args": {}} for t in self._tools]
                      + [{"tool": "final_answer", "args": {}}]})


class Strato3RoutingChangeGuardTests(unittest.TestCase):

    def setUp(self):
        import turn_feedback as TF
        import agent_runtime as AR
        import intent_extractor as IE
        import engine.routing_pool as RP
        import engine.proposer as PROP
        self.TF, self.AR, self.IE, self.RP, self.PROP = TF, AR, IE, RP, PROP

        self.tmp = tempfile.TemporaryDirectory()
        self.fb = Path(self.tmp.name) / "fb.jsonl"
        # 3 ✗ consecutivi per QUERY sulla pipeline rifiutata delete_credentials.
        with self.fb.open("w", encoding="utf-8") as fh:
            for _ in range(3):
                fh.write(json.dumps({
                    "user_query": QUERY, "action": "error",
                    "rejected_pipeline": ["delete_credentials"],
                }) + "\n")

        self._orig_fb = TF.FEEDBACK_PATH
        TF.FEEDBACK_PATH = self.fb
        # Intent deterministico delete/persons (niente LLM).
        self._orig_extract = IE.extract_intent
        IE.extract_intent = lambda q, fast=None: {
            "verb": "delete", "object": "persons", "keywords": [], "actions": []}
        # Pool non vuoto (build_routing_pool non deve dipendere dall'LLM qui).
        self._orig_pool = RP.build_routing_pool
        RP.build_routing_pool = lambda q, intent, catalog, **kw: [
            "delete_persons", "delete_credentials", "get_persons"]
        # Catalog/visibility: evita il load reale, irrilevante col pool fittizio.
        self._orig_catbuiltins = AR._engine_v2_catalog_with_builtins
        AR._engine_v2_catalog_with_builtins = lambda cat: []
        self._orig_loadcat = AR.load_catalog
        AR.load_catalog = lambda *a, **k: type("C", (), {"executors": {}})()
        self._orig_filtervis = AR.filter_for_visibility
        AR.filter_for_visibility = lambda cat, vis=None: cat

    def tearDown(self):
        self.TF.FEEDBACK_PATH = self._orig_fb
        self.IE.extract_intent = self._orig_extract
        self.RP.build_routing_pool = self._orig_pool
        self.AR._engine_v2_catalog_with_builtins = self._orig_catbuiltins
        self.AR.load_catalog = self._orig_loadcat
        self.AR.filter_for_visibility = self._orig_filtervis
        self.tmp.cleanup()

    def _patch_proposer(self, tools):
        self._orig_getprop = self.PROP.get_proposer
        self.PROP.get_proposer = lambda: _FakeProposer(tools)
        self.addCleanup(setattr, self.PROP, "get_proposer", self._orig_getprop)

    def test_gate_active_state(self):
        # Sanity: i 3 ✗ alimentano davvero l'escalation.
        self.assertEqual(
            self.TF.count_consecutive_errors_for_query(QUERY), 3)

    def test_routing_changed_bypasses_escalation(self):
        # Il sistema proporrebbe ORA delete_persons (mai rifiutato) → True.
        self._patch_proposer(["delete_persons"])
        self.assertTrue(self.AR._strato3_routing_changed(QUERY, lang="it"))

    def test_routing_unchanged_preserves_escalation(self):
        # Il sistema riprodurrebbe ancora delete_credentials (rifiutato) → False.
        self._patch_proposer(["delete_credentials"])
        self.assertFalse(self.AR._strato3_routing_changed(QUERY, lang="it"))

    def test_no_rejection_history_not_blocked(self):
        # Nessuna pipeline rifiutata → niente da riprodurre → True (no deadlock).
        self.fb.write_text("", encoding="utf-8")
        self._patch_proposer(["delete_credentials"])
        self.assertTrue(self.AR._strato3_routing_changed(QUERY, lang="it"))

    def test_propose_none_preserves_escalation(self):
        # Proposer indeciso (None) → fail-safe: preserva escalation (False).
        class _NoneProp:
            def propose(self, **kw):
                return None
        self._orig_getprop = self.PROP.get_proposer
        self.PROP.get_proposer = lambda: _NoneProp()
        self.addCleanup(setattr, self.PROP, "get_proposer", self._orig_getprop)
        self.assertFalse(self.AR._strato3_routing_changed(QUERY, lang="it"))

    def test_escalation_uses_stable_values_and_instance_labels(self):
        with mock.patch(
            "orchestration.invoke_get_inputs_internal",
            side_effect=lambda **kwargs: kwargs,
        ):
            result = self.AR._orchestrate_strato3_escalation(
                user_query=QUERY, lang="en", actor="host", channel="http",
                conversation_id="conv", consec_errors=3,
                owner_user_id=OWNER,
            )
        choices = result["dialog"][0]["schema"]["choices"]
        self.assertEqual(
            [choice["value"] for choice in choices],
            ["retry", "synth", "frontier", "reformulate", "abandon"],
        )
        self.assertEqual(choices[0]["label"],
                         "Riprova lo stesso percorso: il motore potrebbe essere cambiato")
        self.assertIn("Come vuoi procedere", result["title"])

    def test_request_language_cannot_override_instance_catalog(self):
        with mock.patch(
            "orchestration.invoke_get_inputs_internal",
            side_effect=lambda **kwargs: kwargs,
        ):
            result = self.AR._orchestrate_strato3_escalation(
                user_query=QUERY, lang="fr", actor="host", channel="http",
                conversation_id="conv", consec_errors=3,
                owner_user_id=OWNER,
            )
        self.assertEqual(result["dialog"][0]["schema"]["choices"][1]["value"],
                         "synth")
        self.assertIn("Costruisci un executor dedicato",
                      result["dialog"][0]["schema"]["choices"][1]["label"])


if __name__ == "__main__":
    unittest.main()
