"""_mutating_args_grounded: i PLACEHOLDER non sono valori baked (cache-key bug 24/6).

Il guard serve-time L0/L1 (GARANZIA Roberto 15/6) rifiuta un piano cachato il cui
step MUTANTE ha un valore-arg DISCRIMINANTE (id/numero/slug) non presente nella
query corrente — impedisce `delete_task(id=42)` su «cancella task 40».

Bug: `create_events(start="${step1.entries.0.start}")` faceva fallire il
grounding (token «step1»/«entries»/«0» non in query) → ogni ripetizione di
«trova slot e prenota» re-pianificava (hit-rate ~1% sui compound propose+fire,
median 14s). I placeholder `${...}` sono risolti a RUNTIME, non discriminano la
query → vanno ignorati. La SICUREZZA (literal baked) resta intatta.

Run: `python3 -m pytest runtime/tests/test_mutating_args_grounded_placeholders.py -xvs`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from engine.dispatch import _mutating_args_grounded  # noqa: E402
from engine.types import Framework, StepSpec  # noqa: E402


def _fw(tool, args):
    return Framework(steps=[StepSpec(tool=tool, args=args),
                            StepSpec(tool="final_answer", args={})])


class MutatingArgsGroundedTests(unittest.TestCase):

    # --- SICUREZZA: i literal baked restano controllati (invariante hard) ---

    def test_baked_id_mismatch_rejected(self):
        # delete_tasks(id=42) su «task 40» → RIFIUTA (re-plan). NON cacheare un
        # target che la query corrente non nomina.
        self.assertFalse(_mutating_args_grounded(
            _fw("delete_tasks", {"id": "42"}), "cancella il task 40"))

    def test_baked_id_match_grounded(self):
        self.assertTrue(_mutating_args_grounded(
            _fw("delete_tasks", {"id": "42"}), "cancella il task 42"))

    def test_mixed_placeholder_plus_baked_id_rejected(self):
        # Anche con un placeholder, un literal baked mismatch va catturato.
        self.assertFalse(_mutating_args_grounded(
            _fw("delete_tasks", {"ids": "${step1.x} 99"}),
            "cancella i task trovati"))

    def test_baked_repo_slug_mismatch_rejected(self):
        self.assertFalse(_mutating_args_grounded(
            _fw("set_issues_github", {"repo": "altro/repo"}),
            "chiudi la issue su brunialti/metnos"))

    # --- FIX: i placeholder NON discriminano → grounded (cacheabile) ---

    def test_stepref_placeholder_grounded(self):
        # create_events(start="${step1.entries.0.start}") → grounded (il valore
        # arriva dallo step a monte, risolto a runtime).
        self.assertTrue(_mutating_args_grounded(
            _fw("create_events", {"start": "${step1.entries.0.start}",
                                   "end": "${step1.entries.0.end}",
                                   "summary": "Appuntamento"}),
            "trova uno slot libero questa settimana e prenota un appuntamento"))

    def test_runtime_placeholder_grounded(self):
        self.assertTrue(_mutating_args_grounded(
            _fw("send_messages", {"to": "${RUNTIME:actor}"}),
            "mandami il riassunto"))

    def test_filler_placeholder_grounded(self):
        self.assertTrue(_mutating_args_grounded(
            _fw("write_files", {"path": "${FILLER:dest}"}),
            "salva il risultato in un file"))

    def test_no_mutating_step_always_grounded(self):
        # un piano read-only non è mai toccato dal guard.
        self.assertTrue(_mutating_args_grounded(
            _fw("read_events", {"time_window": "this-week"}),
            "che eventi ho"))


if __name__ == "__main__":
    unittest.main()
