"""Mass-mutation approval gate (§2.11, bug live 1ba8e2c4 6/7).

Una delete/move di MASSA (>soglia item) NON parte senza consenso umano: il
runtime inserisce un `get_approval` con `guard_count`+`guard_threshold` prima
dell'azione distruttiva. Copre piani normali E ricostruiti dalla recovery (era
lì che nasceva la delete di 681 file → 195 persi).

Run: `python3 -m pytest runtime/tests/test_mass_mutation_gate.py -xvs`.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from engine import dispatch as D  # noqa: E402
from engine.types import Framework, StepSpec  # noqa: E402


def _fw(*steps):
    return Framework(steps=[StepSpec(tool=t, args=a) for t, a in steps])


def _tools(fw):
    return [s.tool for s in fw.steps]


class InsertMassMutationGateTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("METNOS_MASS_MUTATION_THRESHOLD", None)  # default 20

    def test_from_step_delete_gets_gate(self):
        fw = _fw(("find_files", {"base_path": "/d"}),
                 ("delete_files", {"from_step": 1}))
        out = D._insert_mass_mutation_gate(fw, "q", {"actor": "roberto"})
        self.assertEqual(_tools(out),
                         ["find_files", "get_approval", "delete_files"])
        gate = out.steps[1].args
        self.assertEqual(gate["guard_count"], "${step1.@count}")
        self.assertEqual(gate["guard_threshold"], 20)
        # delete resta dopo il gate, from_step invariato (find_files è ancora #1)
        self.assertEqual(out.steps[2].args["from_step"], 1)

    def test_inline_paths_over_threshold_gets_gate(self):
        fw = _fw(("delete_files", {"paths": [f"/f{i}" for i in range(25)]}))
        out = D._insert_mass_mutation_gate(fw, "q", {})
        self.assertEqual(_tools(out), ["get_approval", "delete_files"])
        self.assertEqual(out.steps[0].args["guard_count"], 25)

    def test_inline_paths_under_threshold_no_gate(self):
        fw = _fw(("delete_files", {"paths": ["/a", "/b", "/c"]}))
        out = D._insert_mass_mutation_gate(fw, "q", {})
        self.assertEqual(_tools(out), ["delete_files"])

    def test_move_also_gated(self):
        fw = _fw(("find_files", {"base_path": "/d"}),
                 ("move_files", {"from_step": 1, "dst_template": "/x/{name}"}))
        out = D._insert_mass_mutation_gate(fw, "q", {})
        self.assertIn("get_approval", _tools(out))

    def test_write_not_gated(self):
        # write è additivo → fuori dal primo taglio.
        fw = _fw(("find_files", {"base_path": "/d"}),
                 ("write_files", {"from_step": 1}))
        out = D._insert_mass_mutation_gate(fw, "q", {})
        self.assertNotIn("get_approval", _tools(out))

    def test_skip_when_gate_approved(self):
        # ripresa post-approvazione → nessun re-inserimento (re-run pulito).
        fw = _fw(("find_files", {"base_path": "/d"}),
                 ("delete_files", {"from_step": 1}))
        out = D._insert_mass_mutation_gate(fw, "q", {"_gate_approved": True})
        self.assertNotIn("get_approval", _tools(out))

    def test_skip_when_gate_already_present(self):
        fw = _fw(("get_approval", {"prompt": "x", "on_approve": {}}),
                 ("delete_files", {"from_step": 1}))
        out = D._insert_mass_mutation_gate(fw, "q", {})
        self.assertEqual(_tools(out).count("get_approval"), 1)

    def test_threshold_zero_disables(self):
        os.environ["METNOS_MASS_MUTATION_THRESHOLD"] = "0"
        try:
            fw = _fw(("find_files", {"base_path": "/d"}),
                     ("delete_files", {"from_step": 1}))
            out = D._insert_mass_mutation_gate(fw, "q", {})
            self.assertNotIn("get_approval", _tools(out))
        finally:
            os.environ.pop("METNOS_MASS_MUTATION_THRESHOLD", None)

    def test_device_in_prompt(self):
        fw = _fw(("find_files", {"base_path": "/d"}),
                 ("delete_files", {"from_step": 1}))
        out = D._insert_mass_mutation_gate(
            fw, "q", {"target_device": "PC-ROBERTO"})
        self.assertIn("PC-ROBERTO", out.steps[1].args["prompt"])

    def test_inline_glob_rewritten_to_find_files(self):
        # Buco e2e 6/7: delete(paths=["/dir/*"]) len=1 sfuggiva. Ora riscritto
        # a [find_files, get_approval(@count), delete(from_step)].
        fw = _fw(("delete_files", {"paths": ["/tmp/gt/*"], "client": "local"}))
        out = D._insert_mass_mutation_gate(fw, "q", {})
        self.assertEqual(_tools(out),
                         ["find_files", "get_approval", "delete_files"])
        self.assertEqual(out.steps[0].args["base_path"], "/tmp/gt")
        self.assertEqual(out.steps[0].args["patterns"], ["*"])
        self.assertIs(out.steps[0].args["recursive"], False)
        self.assertEqual(out.steps[1].args["guard_count"], "${step1.@count}")
        self.assertEqual(out.steps[2].args["from_step"], 1)
        self.assertNotIn("paths", out.steps[2].args)

    def test_inline_glob_remote_windows(self):
        fw = _fw(("delete_files",
                  {"paths": [r"C:\Users\rober\Downloads\*"]}))
        out = D._insert_mass_mutation_gate(fw, "q", {"target_device": "PC"})
        self.assertEqual(_tools(out),
                         ["find_files", "get_approval", "delete_files"])
        self.assertEqual(out.steps[0].args["base_path"],
                         r"C:\Users\rober\Downloads")

    def test_glob_helper_multi_parent_none(self):
        self.assertIsNone(
            D._glob_paths_to_find_files(["/a/*", "/b/*"]))

    def test_glob_helper_no_glob_none(self):
        self.assertIsNone(
            D._glob_paths_to_find_files(["/a/x.txt", "/a/y.txt"]))

    def test_gate_before_first_destructive_only(self):
        # due delete → un solo gate, prima della PRIMA.
        fw = _fw(("find_files", {"base_path": "/d"}),
                 ("delete_files", {"from_step": 1}),
                 ("delete_files", {"paths": ["/x"]}))
        out = D._insert_mass_mutation_gate(fw, "q", {})
        self.assertEqual(_tools(out).count("get_approval"), 1)
        self.assertEqual(_tools(out)[1], "get_approval")


class GateResumeRawQueryTests(unittest.TestCase):
    """Il gate-resume salva la query RAW (con la destinazione «su pc-X»), non
    quella strippata dal resolver (bug live 981ddc9f 6/7: il resume dipendeva
    dallo sticky target per l'host giusto)."""

    def test_resume_stores_raw_query(self):
        from unittest import mock
        run = type("R", (), {"gate_dialog_id": "d1"})()
        st = {"on_complete": {"approve_value": "approve"}}
        saved = {}
        with mock.patch("dialog_pending.load_pending", return_value=st), \
             mock.patch("dialog_pending.save_pending",
                        side_effect=lambda s, d, x: saved.update(x)):
            D._inject_gate_resume_if_paused(
                run, "cancella i file nella directory C:/d",
                {"actor": "host", "channel": "http",
                 "user_query_raw":
                     "cancella su pc-roberto i file nella directory C:/d"})
        self.assertEqual(saved["on_complete"]["original_query"],
                         "cancella su pc-roberto i file nella directory C:/d")

    def test_resume_falls_back_to_query(self):
        from unittest import mock
        run = type("R", (), {"gate_dialog_id": "d1"})()
        saved = {}
        with mock.patch("dialog_pending.load_pending",
                        return_value={"on_complete": {}}), \
             mock.patch("dialog_pending.save_pending",
                        side_effect=lambda s, d, x: saved.update(x)):
            D._inject_gate_resume_if_paused(run, "q base", {"actor": "host"})
        self.assertEqual(saved["on_complete"]["original_query"], "q base")


class GetApprovalWebFormTests(unittest.TestCase):
    """Canale http → fmt='form' + marker INLINE_FORM nel hint (bug live
    3db55063 6/7: 'dialogue' sul web = nessuna UI di risposta)."""

    def _invoke(self, **args):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_ga2", _RUNTIME.parent / "executors" / "get_approval"
            / "get_approval.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.invoke(args)

    def test_http_channel_uses_form_with_marker(self):
        out = self._invoke(prompt="Confermi?",
                           on_approve={"tool": "final_answer"},
                           channel="http", actor="host")
        self.assertEqual(out.get("decision"), "input_required")
        self.assertEqual(out.get("fmt"), "form")
        self.assertIn("INLINE_FORM:/agent/dialog/", out["final_message_hint"])
        self.assertIn(out["dialog_id"], out["final_message_hint"])

    def test_other_channels_keep_dialogue(self):
        out = self._invoke(prompt="Confermi?",
                           on_approve={"tool": "final_answer"},
                           channel="", actor="host")
        self.assertEqual(out.get("fmt"), "dialogue")
        self.assertNotIn("INLINE_FORM", out["final_message_hint"])


class UndoHonestFinalTests(unittest.TestCase):
    """Il `message` i18n dell'executor vince sul generico «Nessun risultato
    trovato» (bug live f9cb0033 6/7: undo onesto mascherato)."""

    def test_executor_message_wins_over_no_results(self):
        import agent_runtime
        log = agent_runtime.TurnLog(ts_start=0.0, user_query="annulla")
        sl = agent_runtime.StepLog(step_num=1)
        sl.chosen_tool = "undo_last_turn"
        sl.result = {"ok": True, "undone_count": 0, "skipped_count": 0,
                     "message": "Nessuna operazione reversibile da annullare "
                                "nell'ultimo turno.", "details": []}
        log.steps.append(sl)
        log.final_message = ""
        log.final_kind = "answer"
        log.write()
        self.assertIn("Nessuna operazione reversibile", log.final_message)
        self.assertNotIn("Nessun risultato trovato", log.final_message)


class GetApprovalThresholdTests(unittest.TestCase):
    """get_approval passa trasparente sotto/uguale soglia, chiede sopra."""

    def _invoke(self, **args):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_ga", _RUNTIME.parent / "executors" / "get_approval"
            / "get_approval.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.invoke(args)

    def test_count_under_threshold_passes(self):
        out = self._invoke(prompt="x", on_approve={"tool": "final_answer"},
                           guard_count=5, guard_threshold=20)
        self.assertEqual(out["decision"], "approved")

    def test_count_over_threshold_asks(self):
        out = self._invoke(prompt="Confermi?",
                           on_approve={"tool": "final_answer"},
                           guard_count=50, guard_threshold=20)
        self.assertEqual(out.get("decision"), "input_required")

    def test_zero_threshold_legacy_behaviour(self):
        # default threshold=0: passa solo su count 0 (consent-gate outbound).
        out0 = self._invoke(prompt="x", on_approve={"tool": "final_answer"},
                            guard_count=0)
        self.assertEqual(out0["decision"], "approved")
        out1 = self._invoke(prompt="Confermi?",
                            on_approve={"tool": "final_answer"}, guard_count=1)
        self.assertEqual(out1.get("decision"), "input_required")


if __name__ == "__main__":
    unittest.main()
