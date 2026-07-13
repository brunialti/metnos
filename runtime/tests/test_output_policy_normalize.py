"""test_output_policy_normalize.py — normalize_terminal (output-policy §7.9).

Il runtime — non il proposer — sceglie il TERMINALE di presentazione:
  - mode G/S: drop describe_entries post-producer + final_message
    deterministico (header @shown / Totale @count);
  - mode T su find_urls: insert read_urls_html prima della sintesi (§5.4);
  - mode T su read_sites: insert describe_entries locale e mirato alla query;
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


class TestNormalizeSitesRead(unittest.TestCase):
    def test_read_sites_inserisce_sintesi_mirata(self):
        query = "accedi a example.com e trova tutte le fatture 2025"
        fw = _fw([
            ("open_sites", {"urls": ["https://example.com"]}),
            ("login_sites", {"from_step": 1}),
            ("act_sites", {"from_step": 2, "action": "trova fatture 2025"}),
            ("read_sites", {"from_step": 3}),
            ("final_answer", {}),
        ])

        out, info = normalize_terminal(
            fw, Intent(verb="open", object="sites"), query)

        self.assertEqual(info["mode"], T)
        self.assertEqual(info["data_kind"], "sites")
        self.assertEqual(info["action"], "insert_describe_entries")
        self.assertEqual([step.tool for step in out.steps], [
            "open_sites", "login_sites", "act_sites", "read_sites",
            "describe_entries", "final_answer"])
        self.assertEqual(out.steps[4].args, {
            "from_step": 4, "style": "by_relevance",
            "context": query, "data_kind": "sites",
        })
        self.assertEqual(out.final_message, "${step5.summary}")
        self.assertEqual([step.tool for step in fw.steps], [
            "open_sites", "login_sites", "act_sites", "read_sites",
            "final_answer"])

    def test_read_sites_riusa_describe_esistente(self):
        fw = _fw([
            ("read_sites", {"session_ids": ["sid"]}),
            ("describe_entries", {"from_step": 1, "context": "fatture"}),
            ("final_answer", {}),
        ], final="risposta grezza")

        out, info = normalize_terminal(
            fw, Intent(verb="read", object="sites"), "leggi le fatture")

        self.assertEqual(info["action"], "final_only")
        self.assertEqual([step.tool for step in out.steps].count(
            "describe_entries"), 1)
        self.assertEqual(out.final_message, "${step2.summary}")

    def test_spreadsheet_da_sites_conserva_link_sorgente(self):
        fw = _fw([
            ("read_sites", {"session_ids": ["sid"],
                            "include_screenshot": False}),
            ("extract_entries", {"from_step": 1,
                                 "fields": ["data", "importo"]}),
            ("create_files_spreadsheet", {"from_step": 2,
                                           "columns": ["data", "importo"]}),
            ("final_answer", {}),
        ])

        out, info = normalize_terminal(
            fw, Intent(verb="open", object="sites"),
            "crea uno spreadsheet con data e importo")

        self.assertEqual(info["mode"], "list")
        self.assertEqual(out.final_message,
                         "${step3.@table}\n\n${step1.@links}")


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


class TestTableAppendsExecutorMessage(unittest.TestCase):
    """@table appende SEMPRE la voce onesta `message` dell'executor (§2.8).

    Turn e2b0e529: «0 album» su Google Photos senza dichiarare che l'API vede
    SOLO l'app-created (né album posseduti né condivisi) — il render L-mode
    sostituisce la prosa LLM, quindi il perimetro deve viaggiare nel result."""

    def test_table_with_entries_appends_message(self):
        from engine.executor import _render_final_message
        hist = [StepRun(step_idx=1, tool="find_images_google_photos", args={},
                        result={"ok": True,
                                "entries": [{"title": "T", "id": "A"}],
                                "message": "Nota: solo app-created."},
                        ok=True, latency_ms=1)]
        out = _render_final_message("${step1.@table}", hist)
        self.assertIn("title", out.split("Nota:")[0])        # tabella prima
        self.assertTrue(out.rstrip().endswith("Nota: solo app-created."))

    def test_table_zero_entries_appends_message(self):
        from engine.executor import _render_final_message
        hist = [StepRun(step_idx=1, tool="find_images_google_photos", args={},
                        result={"ok": True, "entries": [], "used": 0,
                                "message": "Nota: solo app-created."},
                        ok=True, latency_ms=1)]
        out = _render_final_message("${step1.@table}", hist)
        self.assertTrue(out.startswith("0"))                 # conteggio onesto
        self.assertIn("Nota: solo app-created.", out)

    def test_table_without_message_unchanged(self):
        from engine.executor import _render_final_message
        hist = [StepRun(step_idx=1, tool="find_files", args={},
                        result={"ok": True, "entries": [], "used": 0},
                        ok=True, latency_ms=1)]
        self.assertEqual(_render_final_message("${step1.@table}", hist), "0")

    def test_table_uses_transform_results_when_entries_are_absent(self):
        from engine.executor import _render_final_message
        hist = [StepRun(step_idx=1, tool="create_files_spreadsheet", args={},
                        result={"ok": True, "results": [{
                            "title": "fatture", "path": "/tmp/fatture.xlsx",
                            "rows": 2, "kind": "spreadsheet"}]},
                        ok=True, latency_ms=1)]
        out = _render_final_message("${step1.@table}", hist)
        self.assertIn("fatture.xlsx", out)
        self.assertIn("rows", out)

    def test_links_magic_renders_unique_http_sources(self):
        from engine.executor import _render_final_message
        hist = [StepRun(step_idx=1, tool="read_sites", args={},
                        result={"ok": True, "entries": [
                            {"title": "Fatture", "url": "https://example.com/invoices"},
                            {"title": "Duplicato", "url": "https://example.com/invoices"},
                            {"title": "Non sicuro", "url": "javascript:alert(1)"},
                        ]}, ok=True, latency_ms=1)]
        out = _render_final_message("${step1.@links}", hist)
        self.assertEqual(out,
                         "- [Fatture](https://example.com/invoices)")

    def test_note_magic_renders_message_or_empty(self):
        from engine.executor import _render_final_message
        hist = [StepRun(step_idx=1, tool="find_images_google_photos", args={},
                        result={"ok": True, "entries": [],
                                "message": "Nota perimetro."},
                        ok=True, latency_ms=1)]
        self.assertEqual(_render_final_message("H${step1.@note}", hist),
                         "H\n\nNota perimetro.")
        hist[0].result.pop("message")
        self.assertEqual(_render_final_message("H${step1.@note}", hist), "H")

    def test_gallery_header_carries_note_template(self):
        # G-mode (il modo REALE del turn e2b0e529: header gallery, non @table):
        # il final deve portare @gallery_fallback (entries remote senza path,
        # turn 4fa8d6bd) e @note accanto a @shown.
        fw = _fw([("find_images_google_photos", {"albums": True}),
                  ("final_answer", {})], final="${step1.summary}")
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="images"),
            "quali sono gli album che ho su google foto")
        if info["mode"] == G:                       # matrice: enumerate images
            self.assertIn("@shown", out.final_message)
            self.assertIn("@gallery_fallback", out.final_message)
            self.assertIn("@note", out.final_message)

    def test_gallery_fallback_bullets_for_remote_entries(self):
        from engine.executor import _render_final_message
        hist = [StepRun(step_idx=1, tool="find_images_google_photos", args={},
                        result={"ok": True, "entries": [
                            {"id": "A", "title": "Test", "items_count": 2,
                             "url": "https://photos.google.com/x"},
                            {"id": "B", "title": "metnos-e2e", "items_count": 2,
                             "url": "https://photos.google.com/y"}]},
                        ok=True, latency_ms=1)]
        out = _render_final_message("H${step1.@gallery_fallback}", hist)
        self.assertIn("- Test | 2", out)
        self.assertIn("- metnos-e2e | 2", out)

    def test_gallery_fallback_empty_for_local_entries(self):
        from engine.executor import _render_final_message
        hist = [StepRun(step_idx=1, tool="find_images_indices", args={},
                        result={"ok": True, "entries": [
                            {"path": "/x/a.jpg", "score": 0.9}]},
                        ok=True, latency_ms=1)]
        self.assertEqual(
            _render_final_message("H${step1.@gallery_fallback}", hist), "H")


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

    def test_processes_self_presenting_noop(self):
        # get_processes ha presentazione bespoke (blocco health «📊 Stato»
        # prepended da agent_runtime) → output_policy NON tocca il terminale
        # (evita la tabella @table doppia, turn 557265c5).
        fw = _fw([("get_processes", {}), ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="get", object="processes"), "elenca i processi")
        self.assertEqual(info["action"], "noop")
        self.assertIs(out, fw)
        self.assertNotIn("@table", out.final_message or "")

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
