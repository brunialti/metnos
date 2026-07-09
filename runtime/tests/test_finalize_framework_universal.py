"""Preparazione UNIVERSALE pre-esecuzione (10/7, turn 1f1eb714).

Il challenger della RECOVERY era l'unico path che eseguiva un piano senza la
pipeline guard: dopo 2 fallimenti remoti di get_processes, il challenger
sceglieva `get_location` per la query «ip metos server» → «Condividi una
posizione su Telegram» (risposta assurda). `_finalize_framework_for_run` è
l'UNICA fonte (L3 + recovery): guard deterministici → output_policy →
ordering → gates. Questi test replicano il caso live e bloccano la classe.

Run: `python3 -m pytest runtime/tests/test_finalize_framework_universal.py -xvs`.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RT))

os.environ.setdefault("METNOS_ENGINE", "v3")


class FinalizeFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import loader
        cls.catalog = loader.load_catalog()
        import detection_lexicon as dl
        dl.ensure_seeded()

    def _finalize(self, steps, intent, query):
        from engine.dispatch import _finalize_framework_for_run
        from engine.types import Framework, StepSpec
        fw = Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in steps],
                       final_message="")
        return _finalize_framework_for_run(fw, intent, query,
                                           self.catalog, {})

    def test_recovery_challenger_realigned_to_intent(self):
        # Il caso live 1f1eb714: challenger get_location, intent (get, processes),
        # query hardware «ip … server» → riallineato a get_processes CON
        # include_health (ensure_health_arg).
        from engine.types import Intent
        out = self._finalize(
            [("get_location", {"actor": "Roberto"}), ("final_answer", {})],
            Intent(verb="get", object="processes"),
            "ip metos server")
        tools = [s.tool for s in out.steps]
        self.assertIn("get_processes", tools)
        self.assertNotIn("get_location", tools)
        gp = next(s for s in out.steps if s.tool == "get_processes")
        self.assertTrue(gp.args.get("include_health"))

    def test_correct_plan_passes_unchanged_tools(self):
        # Piano già giusto → la finalize non cambia la scelta-tool (idempotenza
        # di fatto sul caso comune).
        from engine.types import Intent
        out = self._finalize(
            [("find_files", {"base_path": "/tmp", "pattern": "*.txt"}),
             ("final_answer", {})],
            Intent(verb="find", object="files"),
            "trova i txt in /tmp")
        self.assertEqual([s.tool for s in out.steps][0], "find_files")

    def test_idempotent_double_pass(self):
        # T4 esteso: finalize(finalize(fw)) == finalize(fw) sul caso live.
        from engine.types import Intent
        i = Intent(verb="get", object="processes")
        once = self._finalize(
            [("get_location", {}), ("final_answer", {})], i, "ip metos server")
        twice_fw = self._finalize(
            [(s.tool, dict(s.args or {})) for s in once.steps], i,
            "ip metos server")
        self.assertEqual([s.tool for s in once.steps],
                         [s.tool for s in twice_fw.steps])


if __name__ == "__main__":
    unittest.main()
