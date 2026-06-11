"""test_output_policy_normalize.py — normalize_terminal (output-policy §7.9).

Il runtime — non il proposer — sceglie il TERMINALE di presentazione:
  - mode G/S: drop describe_entries post-producer + final_message
    deterministico (header @shown / Totale @count);
  - mode T su find_urls: insert read_urls_html prima della sintesi (§5.4);
  - altri modi: invariati.
SoT matrice: internal/reports/output_presentation_matrix_2026-05-31.md.
Gate: METNOS_OUTPUT_POLICY=1 (default OFF) — engine.is_output_policy_enabled.
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
        # «foto di silvia al mare» → ENUMERATE×images → G
        fw = _fw([("find_images_indices", {"query": "silvia mare"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})],
                 final="${step2.summary}")
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"), "foto di silvia al mare")
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
        fw = _fw([("find_images_indices", {"query": "silvia"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"), "quante foto di silvia")
        self.assertEqual(info["mode"], S)
        self.assertNotIn("describe_entries", [s.tool for s in out.steps])
        self.assertIn("${step1.@count}", out.final_message)
        # «Totale: N» non è count-only → niente auto-append entries list.
        self.assertNotEqual(out.final_message.strip(), "${step1.@count}")


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
    def test_modi_lista_invariati(self):
        fw = _fw([("find_files", {"base_path": "/tmp"}),
                  ("describe_entries", {"from_step": 1}),
                  ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="files"), "elenca i file in /tmp")
        self.assertEqual(info["action"], "noop")
        self.assertIs(out, fw)

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
    def test_default_off(self):
        from engine import is_output_policy_enabled
        old = os.environ.pop("METNOS_OUTPUT_POLICY", None)
        try:
            self.assertFalse(is_output_policy_enabled())
            os.environ["METNOS_OUTPUT_POLICY"] = "1"
            self.assertTrue(is_output_policy_enabled())
            os.environ["METNOS_OUTPUT_POLICY"] = "0"
            self.assertFalse(is_output_policy_enabled())
        finally:
            if old is None:
                os.environ.pop("METNOS_OUTPUT_POLICY", None)
            else:
                os.environ["METNOS_OUTPUT_POLICY"] = old


if __name__ == "__main__":
    unittest.main()
