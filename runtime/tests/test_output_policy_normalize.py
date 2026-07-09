"""test_output_policy_normalize.py — normalize_terminal (output-policy §7.9).

Il runtime — non il proposer — sceglie il TERMINALE di presentazione:
  - mode G/S: drop describe_entries post-producer + final_message
    deterministico (header @shown / Totale @count);
  - mode T su find_urls: insert read_urls_html prima della sintesi (§5.4);
  - altri modi: invariati.
SoT matrice: internal/reports/output_presentation_matrix_2026-05-31.md.
Gate: default ON dal 9/7/2026 (opt-out METNOS_OUTPUT_POLICY=0) — engine.is_output_policy_enabled.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.types import Intent, Framework, StepSpec, StepRun
from output_policy import normalize_terminal, G, S, T


def _fw(steps, final=""):
    return Framework(steps=[StepSpec(tool=t, args=a) for t, a in steps],
                     final_message=final)


class TestNormalizeGallery(unittest.TestCase):
    def test_foto_senza_verbo_drop_describe_e_header(self):
        # «foto di ospite al mare» → ENUMERATE×images → G
        fw = _fw([("find_images_indices", {"query": "ospite mare"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})],
                 final="${step2.summary}")
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"), "foto di ospite al mare")
        self.assertEqual(info["mode"], G)
        self.assertEqual(info["action"], "drop_describe+final")
        tools = [s.tool for s in out.steps]
        self.assertNotIn("describe_entries", tools)
        self.assertIn("${step1.@shown}", out.final_message)
        # purezza: l'input NON è mutato
        self.assertEqual([s.tool for s in fw.steps],
                         ["find_images_indices", "describe_entries",
                          "final_answer"])

    def test_visualize_marker(self):
        fw = _fw([("find_images_indices", {"query": "tramonto"}),
                  ("final_answer", {})], final="${step1.@count} foto")
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"),
            "mostrami le foto del tramonto")
        self.assertEqual(info["mode"], G)
        self.assertEqual(info["action"], "final_only")
        self.assertIn("@shown", out.final_message)

    def test_drop_abortito_se_step_superstite_referenzia_describe(self):
        # Uno step non-final pesca dal describe → drop sarebbe lossy → solo final.
        fw = _fw([("find_images_indices", {"query": "x"}),
                  ("describe_entries", {"from_step": 1}),
                  ("classify_entries", {"from_step": 2, "classes": ["a", "b"]}),
                  ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"), "foto di x")
        self.assertEqual(info["action"], "final_only")
        self.assertIn("describe_entries", [s.tool for s in out.steps])

    def test_renumber_dopo_drop(self):
        # describe in mezzo: [find, describe, filter(from_step=1), final]
        fw = _fw([("find_images_indices", {"query": "x"}),
                  ("describe_entries", {"from_step": 1}),
                  ("filter_entries", {"from_step": 1, "where_in": ["a"]}),
                  ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"), "foto di x")
        tools = [s.tool for s in out.steps]
        self.assertEqual(tools, ["find_images_indices", "filter_entries",
                                 "final_answer"])
        self.assertEqual(out.steps[1].args["from_step"], 1)


class TestNormalizeScalar(unittest.TestCase):
    def test_count_marker_totale(self):
        fw = _fw([("find_images_indices", {"query": "ospite"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"), "quante foto di ospite")
        self.assertEqual(info["mode"], S)
        self.assertNotIn("describe_entries", [s.tool for s in out.steps])
        self.assertIn("${step1.@count}", out.final_message)
        # «Totale: N» non è count-only → niente auto-append entries list.
        self.assertNotEqual(out.final_message.strip(), "${step1.@count}")

    def test_count_preserva_messaggio_specifico_con_conteggio(self):
        # Piano GIÀ pulito (no describe) + messaggio LLM specifico che porta già
        # il conteggio (@count su step superstite): NON declassare al generico
        # «Totale: N» (matrice S = numero+unità; §2.8 non peggiorare l'output).
        fw = _fw([("find_images_indices", {"query": "roberto"}),
                  ("final_answer", {})],
                 final="Hai ${step1.@count} foto di Roberto.")
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"), "quante foto di roberto")
        self.assertEqual(info["mode"], S)
        self.assertEqual(info["action"], "noop")
        self.assertEqual(out.final_message, "Hai ${step1.@count} foto di Roberto.")
        self.assertIs(out, fw)  # framework invariato

    def test_count_preserva_e_droppa_describe_ricablando_ref(self):
        # Messaggio specifico che referenzia il PRODUCER (@count su step1), con
        # un describe in mezzo da droppare: si droppa e si preserva il messaggio
        # remappando il ref (step2→step1 dopo il drop).
        fw = _fw([("find_images_indices", {"query": "x"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})],
                 final="Trovate ${step1.@count} foto.")
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"), "quante foto di x")
        self.assertEqual(info["mode"], S)
        self.assertEqual(info["action"], "drop_describe")
        self.assertNotIn("describe_entries", [s.tool for s in out.steps])
        self.assertEqual(out.final_message, "Trovate ${step1.@count} foto.")

    def test_count_sostituisce_se_messaggio_pesca_dal_describe(self):
        # Il messaggio base pesca dal describe DROPPATO (${step2.summary}) →
        # non preservabile (ref lossy) → conteggio deterministico «Totale: N».
        fw = _fw([("find_images_indices", {"query": "x"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})],
                 final="${step2.summary}")
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"), "quante foto di x")
        self.assertEqual(info["mode"], S)
        self.assertEqual(info["action"], "drop_describe+final")
        self.assertNotIn("describe_entries", [s.tool for s in out.steps])
        self.assertIn("${step1.@count}", out.final_message)
        self.assertIn("Totale", out.final_message)


class TestNormalizeWebRead(unittest.TestCase):
    def test_read_urls_inserito_prima_di_describe(self):
        fw = _fw([("find_urls", {"query": "notizie ARK"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})], final="${step2.summary}")
        out, info = normalize_terminal(
            fw, Intent(verb="read", object="urls"),
            "leggi le notizie su ARK e riassumile")
        self.assertEqual(info["mode"], T)
        self.assertEqual(info["action"], "insert_read_urls_html")
        tools = [s.tool for s in out.steps]
        self.assertEqual(tools, ["find_urls", "read_urls_html",
                                 "describe_entries", "final_answer"])
        self.assertEqual(out.steps[1].args, {"from_step": 1})
        # describe ricablato sul reader; final shiftato di +1.
        self.assertEqual(out.steps[2].args["from_step"], 2)
        self.assertEqual(out.final_message, "${step3.summary}")

    def test_no_insert_se_reader_gia_presente(self):
        fw = _fw([("find_urls", {"query": "x"}),
                  ("read_urls_html", {"from_step": 1}),
                  ("describe_entries", {"from_step": 2}),
                  ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="read", object="urls"), "leggi gli articoli su x")
        self.assertEqual(info["action"], "noop")
        self.assertIs(out, fw)

    def test_enumerate_web_resta_W_noop(self):
        fw = _fw([("find_urls", {"query": "x"}), ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="urls"), "cerca link su x")
        self.assertEqual(info["action"], "noop")
        self.assertIs(out, fw)


class TestNormalizeNoop(unittest.TestCase):
    def test_mode_L_ora_tabella(self):
        # Ex «modi lista invariati»: dal 9/7 L è IMPLEMENTATO (tabella).
        fw = _fw([("find_files", {"base_path": "/tmp"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="files"), "elenca i file in /tmp")
        self.assertEqual(info["action"], "drop_describe+final")
        self.assertIn("@table", out.final_message)

    def test_mutate_invariato(self):
        fw = _fw([("move_files", {"paths": ["/tmp/a"], "dst": "/tmp/b"}),
                  ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="move", object="files"), "sposta /tmp/a in /tmp/b")
        self.assertEqual(info["action"], "noop")

    def test_framework_vuoto(self):
        fw = Framework()
        out, info = normalize_terminal(fw, Intent(verb="find"), "x")
        self.assertIs(out, fw)
        self.assertEqual(info["action"], "noop")


class TestShownMagic(unittest.TestCase):
    def test_shown_usa_used_non_available_total(self):
        from engine.executor import _render_final_message
        hist = [StepRun(step_idx=1, tool="find_images_indices", args={},
                        result={"ok": True, "entries": [{"path": "a"}] * 3,
                                "used": 3, "available_total": 31655},
                        ok=True, latency_ms=1)]
        self.assertEqual(_render_final_message("${step1.@shown}", hist), "3")
        # @count invece privilegia available_total (totale pre-cap).
        self.assertEqual(_render_final_message("${step1.@count}", hist),
                         "31655")

    def test_shown_fallback_len_entries(self):
        from engine.executor import _render_final_message
        hist = [StepRun(step_idx=1, tool="find_images_indices", args={},
                        result={"ok": True, "entries": [{"path": "a"}] * 7},
                        ok=True, latency_ms=1)]
        self.assertEqual(_render_final_message("${step1.@shown}", hist), "7")


class TestFlagGate(unittest.TestCase):
    def test_default_on_optout(self):
        # Dal 9/7/2026: default ON, opt-OUT con METNOS_OUTPUT_POLICY=0.
        from engine import is_output_policy_enabled
        old = os.environ.pop("METNOS_OUTPUT_POLICY", None)
        try:
            self.assertTrue(is_output_policy_enabled())      # default ON
            os.environ["METNOS_OUTPUT_POLICY"] = "1"
            self.assertTrue(is_output_policy_enabled())
            os.environ["METNOS_OUTPUT_POLICY"] = "0"
            self.assertFalse(is_output_policy_enabled())     # opt-out
        finally:
            if old is None:
                os.environ.pop("METNOS_OUTPUT_POLICY", None)
            else:
                os.environ["METNOS_OUTPUT_POLICY"] = old


if __name__ == "__main__":
    unittest.main()


class TestNormalizeListTable(unittest.TestCase):
    """mode L (list/table): drop describe + final = tabella deterministica."""

    def test_find_files_drop_describe_e_tabella(self):
        from output_policy import L
        fw = _fw([("find_files", {"base_path": "/x"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})], final="${step2.summary}")
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="files"), "elenca i file in /x")
        self.assertEqual(info["mode"], L)
        self.assertEqual(info["action"], "drop_describe+final")
        self.assertNotIn("describe_entries", [s.tool for s in out.steps])
        self.assertIn("${step1.@table}", out.final_message)

    def test_processes_mode_L(self):
        from output_policy import L
        fw = _fw([("get_processes", {}), ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="get", object="processes"), "elenca i processi")
        self.assertEqual(info["mode"], L)
        self.assertIn("@table", out.final_message)

    def test_purezza_input_non_mutato(self):
        fw = _fw([("find_files", {"base_path": "/x"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})])
        normalize_terminal(fw, Intent(verb="find", object="files"), "file")
        self.assertEqual([s.tool for s in fw.steps],
                         ["find_files", "describe_entries", "final_answer"])


class TestTableRenderer(unittest.TestCase):
    def test_tabella_markdown(self):
        from engine.executor import _entries_table
        t = _entries_table([{"name": "a", "size": 1}, {"name": "b", "size": 2}])
        lines = t.splitlines()
        self.assertTrue(lines[0].startswith("| name"))
        self.assertEqual(lines[1], "| --- | --- |")
        self.assertEqual(len(lines), 4)  # header+sep+2 righe

    def test_pipe_escaped_e_troncamento_righe(self):
        from engine.executor import _entries_table
        t = _entries_table([{"name": "a|b"}], max_rows=1)
        self.assertIn("a\\|b", t)

    def test_more_rows_note(self):
        from engine.executor import _entries_table
        ents = [{"name": f"f{i}"} for i in range(5)]
        t = _entries_table(ents, max_rows=2)
        self.assertIn("f0", t); self.assertIn("f1", t)
        self.assertNotIn("| f2 |", t)  # troncata, con nota §2.7
