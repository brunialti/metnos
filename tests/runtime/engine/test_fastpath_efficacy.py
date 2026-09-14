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


from engine.types import Intent, Framework, StepSpec, RunResult, StepRun
from engine import fastpath as eng_fastpath
from engine import dispatch as eng_dispatch
from pipeline_effects import (committed_mutations, ineffective_mutations,
                              pipeline_effect_counts)


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

    def _record(self, query, fw, run, intent=None):
        eng_dispatch._maybe_record_fastpath(
            query, intent or Intent(verb="delete", object="persons"), fw, run)


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
        (conservativo: mai bloccare senza evidenza).

        L'intent va dichiarato COERENTE con la query e col piano: qui si
        misura il criterio di EFFICACIA, non la copertura dell'azione
        richiesta. Con l'intent segnaposto `delete/persons` il piano
        `set_credentials` risultava scoperto e il turno veniva scartato dal
        gate a monte, mascherando il criterio sotto misura.
        """
        fw = _fw("set_credentials")
        run = _run([("set_credentials", {"ok": True})])
        self._record("imposta la credenziale", fw, run,
                     intent=Intent(verb="set", object="credentials"))
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

    def test_unguarded_mutant_skipped_on_empty_not_recorded(self):
        """Regression turn e591854e/71117eef: «cerca fatture ... su spreadsheet»
        misroutato su files → read_files a 0 → create_files_spreadsheet
        NON-guardato auto-skippato (assente da run.steps) → 0 effetto reale.
        Il piano (misroute/non-guardato a vuoto) NON va cachato, altrimenti
        l'errore itera (il path fallito intercetta le richieste nuove)."""
        fw = _fw("read_files", "extract_entries", "create_files_spreadsheet")
        # create assente da run.steps: l'auto-skip su input vuoto l'ha saltato.
        run = _run([
            ("read_files", {"ok": True, "entries": []}),
            ("extract_entries", {"ok": True, "entries": []}),
        ])
        eng_dispatch._maybe_record_fastpath(
            "cerca fatture e metti data e importo su spreadsheet",
            Intent(verb="create", object="files"), fw, run)
        self.assertEqual(eng_fastpath.list_all(), [])

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
    def test_no_effect_receipt_overrides_successful_item_counters(self):
        for counter in ({"ok_count": 1}, {"results": [{"ok": True}]}):
            with self.subTest(counter=counter):
                steps = [StepRun(
                    step_idx=1, tool="set_processes", args={}, ok=True,
                    latency_ms=1, result={"ok": True, **counter,
                                         "_undo": {"outcome": "no_effect"}})]
                counts = pipeline_effect_counts(steps)
                self.assertEqual(counts["mutations"], 0)
                self.assertTrue(counts["mutating_attempted"])
                self.assertEqual(ineffective_mutations(steps), ["set_processes"])
                self.assertEqual(committed_mutations(steps), [])

    def test_effect_receipt_keeps_actual_mutations_and_retry_protection(self):
        for metadata in (None, {}, {"outcome": "invalid"},
                         {"outcome": "reversible"},
                         {"outcome": "irreversible"}):
            with self.subTest(metadata=metadata):
                steps = [{"chosen_tool": "set_processes", "result": {
                    "ok": True, "ok_count": 1, "_undo": metadata}}]
                self.assertEqual(pipeline_effect_counts(steps)["mutations"], 1)
                self.assertEqual(ineffective_mutations(steps), [])
                self.assertEqual(committed_mutations(steps), ["set_processes"])

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


class TestCommittedMutations(unittest.TestCase):
    """committed_mutations (2/7/2026, review Fable): predicato del guard
    anti-doppia-esecuzione sul fall-through L0/L1→L3. Conservativo INVERSO
    a ineffective_mutations: senza output contabile si ASSUME committato."""

    def test_send_with_effect_is_committed(self):
        steps = [StepRun(step_idx=1, tool="send_messages", args={},
                         result={"ok": True, "n_sent": 1}, ok=True,
                         latency_ms=1),
                 StepRun(step_idx=2, tool="move_files", args={},
                         result={"ok": False, "error": "boom"}, ok=False,
                         latency_ms=1)]
        self.assertEqual(committed_mutations(steps), ["send_messages"])

    def test_send_a_vuoto_not_committed(self):
        steps = [StepRun(step_idx=1, tool="send_messages", args={},
                         result={"ok": True, "n_sent": 0}, ok=True,
                         latency_ms=1)]
        self.assertEqual(committed_mutations(steps), [])

    def test_failed_mutant_not_committed(self):
        steps = [StepRun(step_idx=1, tool="delete_files", args={},
                         result={"ok": False, "error": "ko"}, ok=False,
                         latency_ms=1)]
        self.assertEqual(committed_mutations(steps), [])

    def test_partial_failed_mutant_IS_committed(self):
        # Bug live fin-1/mat-2 (7/7): delete glob §2.4 rimuove 3 file e
        # rifiuta il system-file → ok=False MA ok_count=3. Considerarla
        # non-committata faceva ri-eseguire la pipeline dalla recovery
        # (leg fantasma). Parziale = committato (criterio 'mutated').
        steps = [StepRun(
            step_idx=1, tool="delete_files", args={},
            result={"ok": False, "ok_count": 3, "fail_count": 1,
                    "results": [{"path": f"/x{i}", "removed": True}
                                for i in range(3)],
                    "failed": [{"error_code": "ERR_REFUSE_MOVE"}]},
            ok=False, latency_ms=1)]
        self.assertEqual(committed_mutations(steps), ["delete_files"])

    def test_partial_zero_effect_failed_not_committed(self):
        # ok=False con ok_count=0 e results vuoti = davvero nulla di fatto.
        steps = [StepRun(
            step_idx=1, tool="delete_files", args={},
            result={"ok": False, "ok_count": 0, "results": [],
                    "failed": [{"error_code": "ERR_PATH_NOT_FOUND"}]},
            ok=False, latency_ms=1)]
        self.assertEqual(committed_mutations(steps), [])

    def test_partial_send_multi_account_IS_committed(self):
        # 2ter (7/7): gemello NON-delete del parziale. send a 3 account →
        # 1 inviato, 2 falliti (auth) → ok=False MA n_sent=1: la mail È
        # partita, ri-eseguire la RI-INVIA (§2.8). Parziale = committato.
        steps = [StepRun(
            step_idx=1, tool="send_messages", args={},
            result={"ok": False, "n_sent": 1, "fail_count": 2,
                    "results": [{"ok": True, "to": "a@x"}],
                    "failed": [{"to": "b@x", "error_code": "ERR_SMTP_AUTH"},
                               {"to": "c@x", "error_code": "ERR_SMTP_AUTH"}]},
            ok=False, latency_ms=1)]
        self.assertEqual(committed_mutations(steps), ["send_messages"])

    def test_reader_not_committed(self):
        steps = [StepRun(step_idx=1, tool="read_messages", args={},
                         result={"ok": True, "entries": [1]}, ok=True,
                         latency_ms=1)]
        self.assertEqual(committed_mutations(steps), [])

    def test_uncountable_mutant_assumed_committed(self):
        # Confine conservativo: mutante ok senza counter/results → assunto
        # committato (mai ri-eseguire senza evidenza di non-effetto).
        steps = [StepRun(step_idx=1, tool="create_events", args={},
                         result={"ok": True}, ok=True, latency_ms=1)]
        self.assertEqual(committed_mutations(steps), ["create_events"])

    def test_empty_entries_consumer_not_committed(self):
        steps = [StepRun(step_idx=1, tool="send_messages",
                         args={"entries": []},
                         result={"ok": True, "n_sent": 1}, ok=True,
                         latency_ms=1)]
        self.assertEqual(committed_mutations(steps), [])

    def test_seed_done_step_not_committed(self):
        # I seed «done» (eseguiti in un turno PRECEDENTE) sono protetti dalla
        # guardia dedup del proposer: non bloccano il fall-through.
        steps = [StepRun(step_idx=1, tool="send_messages", args={},
                         result={"ok": True, "n_sent": 1}, ok=True,
                         latency_ms=1, kind="done")]
        self.assertEqual(committed_mutations(steps), [])


class TestShouldCachePlanMaterialized(unittest.TestCase):
    """_should_cache_plan — piani MATERIALIZZATI (7/7): liste PATH-like
    literal non-in-query negli step MUTANTI = valori risolti dai result del
    turno → il replay 0b li canonicalizza su path nuovi e serve piani
    incoerenti. No-cache. Glob esenti per VALORE; slug/id preservati
    (garanzia serve-time _mutating_args_grounded)."""

    @staticmethod
    def _fw(*steps):
        from engine.types import Framework, StepSpec
        return Framework(steps=[StepSpec(tool=t, args=a) for t, a in steps])

    def _should(self, fw, q):
        from engine.dispatch import _should_cache_plan
        return _should_cache_plan(fw, q)

    def test_materialized_paths_not_cacheable(self):
        fw = self._fw(("find_files", {"base_path": "/tmp/x"}),
                      ("delete_files", {"paths": ["/tmp/x/a.txt",
                                                  "/tmp/x/b.txt"]}))
        self.assertFalse(self._should(
            fw, "cancella i file nella directory /tmp/x"))

    def test_degenerate_path_in_query_cacheable(self):
        fw = self._fw(("delete_files", {"paths": ["/tmp/x/a.txt"]}))
        self.assertTrue(self._should(fw, "cancella /tmp/x/a.txt"))

    def test_from_step_skeleton_cacheable(self):
        fw = self._fw(("find_files", {"base_path": "/tmp/x"}),
                      ("delete_files", {"from_step": 1}))
        self.assertTrue(self._should(
            fw, "cancella i file nella directory /tmp/x"))

    def test_runtime_turn_directory_is_not_a_materialized_path(self):
        fw = self._fw(("create_dirs", {
            "paths": ["Documenti/Risultati/Run_${RUNTIME:turn_id}"],
            "exist_ok": False,
        }))
        self.assertTrue(self._should(
            fw, "crea i risultati in Documenti/Risultati"))

    def test_glob_value_exempt(self):
        fw = self._fw(("delete_files", {"paths": ["/tmp/gt/*"]}))
        self.assertTrue(self._should(fw, "cancella i file in /tmp/gt"))

    def test_resolved_slugs_still_cacheable(self):
        # policy esistente (slug non path-like): garanzia a serve-time.
        fw = self._fw(("delete_persons", {"chosen_slugs": ["mario-rossi"]}))
        self.assertTrue(self._should(fw, "cancella l'enrollment di mario"))

    def test_windows_path_not_cacheable(self):
        fw = self._fw(("delete_files",
                       {"paths": ["C:\\Users\\rober\\Downloads\\x.pdf"]}))
        self.assertFalse(self._should(
            fw, "cancella i file nella directory downloads"))

    def test_producer_literal_untouched(self):
        fw = self._fw(("read_files", {"paths": ["/tmp/other.txt"]}))
        self.assertTrue(self._should(fw, "leggi il file"))


if __name__ == "__main__":
    unittest.main()
