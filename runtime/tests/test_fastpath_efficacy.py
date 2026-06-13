"""test_fastpath_efficacy.py — criterio di EFFICACIA del fastpath L0 (12/6/2026).

Bug live 1dcc8307 (12/6 17:55): «cancella l'enrollement di roberto brunialti»
misroutato su delete_credentials(bindings=["roberto brunialti"]) → credenziale
inesistente → n_deleted=0 MA final_kind=answer → il piano «ok a vuoto» veniva
cachato come fastpath e ri-servito in millisecondi BYPASSANDO il proposer
(che nel frattempo era stato fixato). Whack-a-mole: rimuovere la riga non
basta, si ri-cacha al prossimo misroute.

Criterio (dispatch._maybe_record_fastpath + pipeline_effects.ineffective_mutations):
  - mutante eseguito con 0 effetto reale (n_*=0 / ok=False) → NON cacheabile;
  - mutante con effetto reale → cacheabile;
  - producer (find/read/list) a 0 risultati → esito VALIDO, cacheabile;
  - mutante saltato da guard condizionale o senza output contabile → non
    giudicabile, non blocca.

Mock everywhere — no live LLM, no live executor, no BGE-M3, DB isolato.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.types import Intent, Framework, StepSpec, RunResult, StepRun
from engine import fastpath as eng_fastpath
from engine import dispatch as eng_dispatch
from pipeline_effects import ineffective_mutations, pipeline_effect_counts


def _fw(*tools, args_map=None, final="fatto"):
    args_map = args_map or {}
    steps = [StepSpec(tool=t, args=dict(args_map.get(t, {}))) for t in tools]
    steps.append(StepSpec(tool="final_answer", args={}))
    return Framework(steps=steps, final_message=final)


def _run(step_results, final_kind="answer"):
    """RunResult engine con StepRun da (tool, result_dict)."""
    steps = [StepRun(step_idx=i + 1, tool=t, args={}, result=r,
                     ok=bool(r.get("ok", True)), latency_ms=1)
             for i, (t, r) in enumerate(step_results)]
    return RunResult(steps=steps, final_text="x", final_kind=final_kind)


class _FastpathDbCase(unittest.TestCase):
    """DB fastpath isolato per-test (stesso pattern di test_fastpath_lifecycle)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = eng_fastpath._db_path
        eng_fastpath._db_path = lambda: Path(self.tmp) / "fastpaths.sqlite"

    def tearDown(self):
        eng_fastpath._db_path = self._orig

    def _record(self, query, fw, run):
        eng_dispatch._maybe_record_fastpath(
            query, Intent(verb="delete", object="persons"), fw, run)


# ── Criterio efficacia in _maybe_record_fastpath ──────────────────────────

class TestEfficacyGate(_FastpathDbCase):
    def test_mutant_zero_effect_not_recorded(self):
        """Scenario live 1dcc8307: turno misroutato (delete_credentials
        n_deleted=0, «not found») con final answer → NON entra in fastpath."""
        q = "cancella l'enrollement roberto brunialti"
        fw = _fw("delete_credentials",
                 args_map={"delete_credentials": {"bindings": ["roberto brunialti"]}})
        run = _run([("delete_credentials",
                     {"ok": True, "n_deleted": 0,
                      "results": [{"binding": "roberto brunialti",
                                   "deleted": False, "reason": "not found"}]})])
        self._record(q, fw, run)
        self.assertEqual(eng_fastpath.list_all(), [])
        # E il lookup successivo della stessa query resta MISS: il misroute
        # non si auto-perpetua.
        self.assertIsNone(eng_fastpath.lookup(q))

    def test_mutant_real_effect_recorded(self):
        fw = _fw("delete_persons",
                 args_map={"delete_persons": {"chosen_slugs": ["mario-rossi"]}})
        run = _run([("delete_persons",
                     {"ok": True, "n_deleted": 1,
                      "results": [{"slug": "mario-rossi", "deleted": True}]})])
        self._record("cancella l'enrollment di mario", fw, run)
        rows = eng_fastpath.list_all()
        self.assertEqual(len(rows), 1)

    def test_producer_zero_results_still_recorded(self):
        """find a 0 hit = esito VALIDO (vuoto legittimo), cacheabile."""
        fw = _fw("find_files", args_map={"find_files": {"pattern": "*.xyz"}})
        run = _run([("find_files", {"ok": True, "entries": []})])
        eng_dispatch._maybe_record_fastpath(
            "cerca i file xyz", Intent(verb="find", object="files"), fw, run)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_mutant_failed_ok_false_not_recorded(self):
        fw = _fw("send_messages")
        run = _run([("send_messages", {"ok": False, "error": "smtp down"})])
        self._record("manda la mail", fw, run)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_mutant_uncountable_output_not_blocked(self):
        """Mutante senza counter contabile → non giudicabile → registra
        (conservativo: mai bloccare senza evidenza)."""
        fw = _fw("set_credentials")
        run = _run([("set_credentials", {"ok": True})])
        self._record("imposta la credenziale", fw, run)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_skipped_conditional_mutant_not_blocked(self):
        """Mutante con guard if_prev_entries_nonempty SALTATO non compare
        fra gli step eseguiti → il piano (producer a 0) resta cacheabile."""
        fw = Framework(steps=[
            StepSpec(tool="find_messages", args={"folder": "Junk"}),
            StepSpec(tool="move_messages", args={"dst_folder": "Trash"},
                     if_prev_entries_nonempty=True),
            StepSpec(tool="final_answer", args={}),
        ], final_message="fatto")
        run = _run([("find_messages", {"ok": True, "entries": []})])
        eng_dispatch._maybe_record_fastpath(
            "svuota lo spam", Intent(verb="move", object="messages"), fw, run)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_mixed_plan_one_mutant_a_vuoto_blocks(self):
        """Per-step, non aggregato: un mutante a vuoto blocca anche se un
        altro mutante ha avuto effetto."""
        fw = _fw("write_files", "delete_credentials")
        run = _run([
            ("write_files", {"ok": True, "ok_count": 1, "results": [{}]}),
            ("delete_credentials", {"ok": True, "n_deleted": 0, "results": []}),
        ])
        self._record("scrivi e cancella", fw, run)
        self.assertEqual(eng_fastpath.list_all(), [])


# ── pipeline_effects: predicato condiviso, shape-agnostic ──────────────────

class TestIneffectiveMutations(unittest.TestCase):
    def test_engine_steprun_shape(self):
        steps = [StepRun(step_idx=1, tool="delete_credentials", args={},
                         result={"ok": True, "n_deleted": 0}, ok=True,
                         latency_ms=1)]
        self.assertEqual(ineffective_mutations(steps), ["delete_credentials"])

    def test_react_dict_shape_with_json_string_result(self):
        steps = [{"chosen_tool": "move_messages",
                  "result": json.dumps({"ok": True, "n_moved": 0})}]
        self.assertEqual(ineffective_mutations(steps), ["move_messages"])

    def test_producer_zero_is_not_ineffective(self):
        steps = [{"chosen_tool": "find_files",
                  "result": {"ok": True, "entries": []}}]
        self.assertEqual(ineffective_mutations(steps), [])

    def test_effective_mutant_clean(self):
        steps = [{"chosen_tool": "delete_files",
                  "result": {"ok": True, "n_deleted": 3}}]
        self.assertEqual(ineffective_mutations(steps), [])

    def test_duplicate_result_ignored(self):
        steps = [{"chosen_tool": "delete_files",
                  "result": {"ok": True, "n_deleted": 0, "_duplicate": True}}]
        self.assertEqual(ineffective_mutations(steps), [])

    def test_counter_priority_over_results_len(self):
        """delete_credentials «not found»: results=[1 elemento deleted=False]
        ma n_deleted=0 → il counter specifico vince su len(results)."""
        steps = [{"chosen_tool": "delete_credentials",
                  "result": {"ok": True, "n_deleted": 0,
                             "results": [{"deleted": False}]}}]
        self.assertEqual(ineffective_mutations(steps),
                         ["delete_credentials"])

    def test_effect_counts_works_on_engine_shape(self):
        """pipeline_effect_counts (condiviso) legge anche StepRun engine."""
        steps = [StepRun(step_idx=1, tool="find_files", args={},
                         result={"ok": True, "entries": [1, 2]}, ok=True,
                         latency_ms=1),
                 StepRun(step_idx=2, tool="delete_files", args={},
                         result={"ok": True, "n_deleted": 2}, ok=True,
                         latency_ms=1)]
        c = pipeline_effect_counts(steps)
        self.assertEqual(c["items"], 2)
        self.assertEqual(c["mutations"], 2)
        self.assertTrue(c["mutating_attempted"])


if __name__ == "__main__":
    unittest.main()
