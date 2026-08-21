"""Preparazione UNIVERSALE pre-esecuzione (10/7, turn 1f1eb714).

Il challenger della RECOVERY era l'unico path che eseguiva un piano senza la
pipeline guard: dopo 2 fallimenti remoti di get_processes, il challenger
sceglieva `get_location` per la query «ip metos server» → «Condividi una
posizione su Telegram» (risposta assurda). `_finalize_framework_for_run` è
l'UNICA fonte (L0 + L1 + L3 + recovery): guard deterministici → output_policy →
ordering → gates. Questi test replicano il caso live e bloccano la classe.

Run: `python3 -m pytest tests/runtime/engine/test_finalize_framework_universal.py -xvs`.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_RT = (Path(__file__).resolve().parents[3] / "runtime")

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

    def test_general_device_status_selects_cpu_temperature(self):
        """Regressione live 796e3cf6: lo stato mostrava «sensore assente»
        perché include_health non attivava il provider hardware gestito."""
        from engine.types import Intent
        out = self._finalize(
            [("get_processes", {"include_health": True, "top": 10}),
             ("final_answer", {})],
            Intent(verb="get", object="processes"),
            "stato pc-roberto")
        args = next(s.args for s in out.steps
                    if s.tool == "get_processes")
        self.assertTrue(args["include_health"])
        self.assertEqual(args["sensor_domains"], ["cpu"])
        self.assertEqual(args["sensor_types"], ["temperature"])

    def test_general_status_repairs_health_and_sensors_together(self):
        from engine.types import Intent
        out = self._finalize(
            [("get_processes", {}), ("final_answer", {})],
            Intent(verb="get", object="processes"),
            "stato del server")
        args = next(s.args for s in out.steps
                    if s.tool == "get_processes")
        self.assertTrue(args["include_health"])
        self.assertEqual(args["sensor_domains"], ["cpu"])
        self.assertEqual(args["sensor_types"], ["temperature"])

    def test_explicit_temperature_repairs_missing_sensor_selection(self):
        from engine.types import Intent
        out = self._finalize(
            [("get_processes", {"include_health": True}),
             ("final_answer", {})],
            Intent(verb="get", object="numbers"),
            "temperatura pc-roberto")
        args = next(s.args for s in out.steps
                    if s.tool == "get_processes")
        self.assertEqual(args["sensor_domains"], ["cpu"])
        self.assertEqual(args["sensor_types"], ["temperature"])

    def test_targeted_memory_status_does_not_activate_sensors(self):
        from engine.types import Intent
        out = self._finalize(
            [("get_processes", {"include_health": True}),
             ("final_answer", {})],
            Intent(verb="get", object="numbers"),
            "quanta RAM ha pc-roberto")
        args = next(s.args for s in out.steps
                    if s.tool == "get_processes")
        self.assertNotIn("sensor_domains", args)
        self.assertNotIn("sensor_types", args)

    def test_explicit_sensor_selection_is_preserved(self):
        from engine.types import Intent
        original = {
            "include_health": True,
            "sensor_domains": ["gpu"],
            "sensor_types": ["temperature"],
        }
        out = self._finalize(
            [("get_processes", original), ("final_answer", {})],
            Intent(verb="get", object="processes"),
            "stato pc-roberto")
        args = next(s.args for s in out.steps
                    if s.tool == "get_processes")
        self.assertEqual(args["sensor_domains"], ["gpu"])
        self.assertEqual(args["sensor_types"], ["temperature"])

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
        self.assertEqual([s.args for s in once.steps],
                         [s.args for s in twice_fw.steps])

    def test_cached_time_plan_is_repaired_before_execution(self):
        from engine.dispatch import _finalize_framework_for_run
        from engine.types import Framework, Intent, StepSpec

        stale = Framework(
            steps=[StepSpec(tool="get_now", args={}),
                   StepSpec(tool="final_answer", args={})],
            final_message="Totale: ${step1.@count}",
        )
        out = _finalize_framework_for_run(
            stale, Intent(verb="get", object="numbers"),
            "che ora è adesso?", self.catalog, {})
        self.assertEqual(out.final_message, "${step1.time}")

    def test_compound_file_inventory_repairs_result_scope_and_health_route(self):
        """Regressione live ddd828a6: due normalizzazioni indipendenti.

        - `name_regex` non può escludere una directory antenata;
        - `include_health` su get_files prova un riallineamento oggetto errato.
        """
        from engine.types import Intent

        intent = Intent(verb="list", object="files", actions=[
            {"verb": "list", "object": "files"},
            {"verb": "filter", "object": "files"},
            {"verb": "get", "object": "numbers"},
        ])
        out = self._finalize([
            ("find_files", {"base_path": "Documenti/Progetto Atlas",
                            "patterns": ["*.pdf", "*.docx", "*.xlsx", "*.csv"],
                            "recursive": True}),
            ("filter_entries", {
                "name_regex": r"^(?!.*Risultati_Menos_\*).*"}),
            ("get_files", {"include_health": True}),
            ("final_answer", {}),
        ], intent, (
            "Su PC-ROBERTO trova i file in Documenti/Progetto Atlas, "
            "senza includere le cartelle Risultati_Metnos_*. "
            "Indica RAM, spazio libero e carico CPU."))

        filt = next(s for s in out.steps if s.tool == "filter_entries")
        self.assertNotIn("name_regex", filt.args)
        self.assertEqual(filt.args["where_field"], "path")
        self.assertIn("Risultati_Metnos_", filt.args["where_regex"])
        self.assertEqual(filt.args["from_step"], 1)
        health = next(s for s in out.steps if s.tool == "get_processes")
        self.assertTrue(health.args["include_health"])
        self.assertEqual(health.args["top"], 1)

    def test_compound_health_does_not_rewrite_explicit_get_now(self):
        from engine.types import Intent
        out = self._finalize([
            ("find_files", {"base_path": "Documenti/Progetto Atlas"}),
            ("get_processes", {"include_health": True}),
            ("get_now", {}),
            ("final_answer", {}),
        ], Intent(verb="find", object="files", actions=[
            {"verb": "find", "object": "files"},
            {"verb": "get", "object": "processes"},
            {"verb": "get", "object": "numbers"},
        ]), "trova i file e indica salute sistema e data ora locale")
        self.assertIn("get_now", [s.tool for s in out.steps])

    def test_outbound_local_context_gets_jit_gate(self):
        from engine.dispatch import _insert_outbound_data_gate
        from engine.types import Framework, StepSpec

        executor = SimpleNamespace(
            name="consult_test",
            args_schema={"properties": {"local_context": {"type": "object"}}},
            capabilities=[{
                "name": "llm:online", "hint": [],
                "outbound_args": ["local_context"],
            }],
        )
        framework = Framework(steps=[
            StepSpec(tool="consult_test", args={
                "local_context": {"inline": {"case": "private"}},
            }),
            StepSpec(tool="final_answer", args={}),
        ], final_message="")
        with patch("messages.get", side_effect=lambda _code, **kwargs:
                   f"Approve external context for {kwargs['tool']}?"):
            out = _insert_outbound_data_gate(
                framework, "analizza",
                {"actor": "host", "channel": "http"}, [executor])
        self.assertEqual(
            [step.tool for step in out.steps],
            ["get_approval", "consult_test", "final_answer"],
        )
        self.assertIn("consult_test", out.steps[0].args["prompt"])

    def test_real_consult_frontier_contract_gets_jit_gate(self):
        """Integration guard: exercise the loaded, signed production manifest,
        not only a synthetic capability object."""
        from engine.dispatch import _insert_outbound_data_gate
        from engine.types import Framework, StepSpec

        consult = next(item for item in self.catalog
                       if item.name == "consult_frontier")
        framework = Framework(steps=[
            StepSpec(tool="get_now", args={}),
            StepSpec(tool="consult_frontier", args={
                "role": "reviewer",
                "output_spec": {"format": "markdown"},
                "local_context": {"inline": {"case": "private"}},
            }),
            StepSpec(tool="final_answer", args={}),
        ], final_message="")
        out = _insert_outbound_data_gate(
            framework, "review", {"actor": "host", "channel": "http"},
            self.catalog,
        )
        assert consult.name == "consult_frontier"
        self.assertEqual(
            [step.tool for step in out.steps],
            ["get_now", "get_approval", "consult_frontier", "final_answer"],
        )
        self.assertEqual(out.steps[1].args["timeout_s"], 3600)
        self.assertEqual(out.steps[1].args["actor"], "host")

    def test_outbound_gate_is_value_sensitive_and_resume_idempotent(self):
        from engine.dispatch import _insert_outbound_data_gate
        from engine.types import Framework, StepSpec

        executor = SimpleNamespace(
            name="consult_test",
            args_schema={"properties": {"local_context": {"type": "object"}}},
            capabilities=[{
                "name": "llm:online", "hint": [],
                "outbound_args": ["local_context"],
            }],
        )
        for args, runtime_ctx in (({"local_context": {}}, {}),
                                  ({"local_context": {"inline": {"x": 1}}},
                                   {"_gate_approved": True})):
            framework = Framework(
                steps=[StepSpec(tool="consult_test", args=args)],
                final_message="",
            )
            out = _insert_outbound_data_gate(
                framework, "analizza", runtime_ctx, [executor])
            self.assertEqual([step.tool for step in out.steps], ["consult_test"])

    def test_unannotated_executor_keeps_legacy_behavior(self):
        from engine.dispatch import _insert_outbound_data_gate
        from engine.types import Framework, StepSpec

        executor = SimpleNamespace(
            name="legacy_test",
            args_schema={"properties": {"payload": {"type": "object"}}},
            capabilities=[{"name": "llm:online", "hint": []}],
        )
        framework = Framework(
            steps=[StepSpec(tool="legacy_test", args={"payload": {"x": 1}})],
            final_message="",
        )
        out = _insert_outbound_data_gate(
            framework, "analizza", {}, [executor])
        self.assertEqual([step.tool for step in out.steps], ["legacy_test"])


if __name__ == "__main__":
    unittest.main()
