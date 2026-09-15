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
from types import SimpleNamespace

import pytest

from engine.types import Intent, Framework, StepSpec, StepRun
from output_policy import normalize_terminal, G, L, S, T


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


class TestManifestPresentation(unittest.TestCase):
    def test_declared_list_view_outranks_legacy_data_kind_default(self):
        fw = _fw([
            ("read_messages", {"account": "knowcastle"}),
            ("describe_entries", {"from_step": 1}),
            ("final_answer", {}),
        ], final="${step2.summary}")
        catalog = [SimpleNamespace(
            name="read_messages",
            presentation={"default_view": "list", "list": {"columns": [{"key": "subject", "source": "subject", "cell_max": 80}]}}
        )]
        out, info = normalize_terminal(
            fw, Intent(verb="read", object="messages"), "leggi le email",
            catalog=catalog)
        self.assertEqual(info["mode"], L)
        self.assertTrue(info["manifest_declared"])
        self.assertNotIn("describe_entries", [step.tool for step in out.steps])
        self.assertEqual(out.final_message, "${step1.@table}")


class TestCompoundTerminalPresentation(unittest.TestCase):
    def test_find_then_read_uses_terminal_read_mode(self):
        fw = _fw([
            ("find_files", {"base_path": "/tmp"}),
            ("read_files", {"from_step": 1}),
            ("final_answer", {}),
        ], final="${step2.content}")
        intent = Intent(
            verb="find", object="files",
            actions=[
                {"verb": "find", "object": "files"},
                {"verb": "read", "object": "files"},
            ],
        )

        out, info = normalize_terminal(
            fw, intent, "trova il file e leggine il contenuto")

        self.assertEqual(info["mode"], T)
        self.assertEqual(info["intent_class"], "read")
        self.assertEqual(info["action"], "content")
        self.assertEqual(out.final_message, "${step2.@content}")

    def test_scalar_file_read_drops_lossy_describe(self):
        fw = _fw([
            ("read_files", {"path": "/tmp/note.txt"}),
            ("describe_entries", {"entries": []}),
            ("final_answer", {}),
        ], final="${step2.summary}")

        out, info = normalize_terminal(
            fw, Intent(verb="read", object="files"), "leggi il file")

        self.assertEqual(info["mode"], T)
        self.assertEqual(info["action"], "drop_describe+content")
        self.assertEqual([step.tool for step in out.steps], [
            "read_files", "final_answer",
        ])
        self.assertEqual(out.final_message, "${step1.@content}")

    def test_structured_reader_group_uses_terminal_transform_mode(self):
        fw = _fw([
            ("read_files_csv", {"paths": ["/tmp/expenses.csv"]}),
            ("group_entries", {"from_step": 1, "cross_domain_key": "category"}),
            ("sort_entries", {"from_step": 2, "by": "category"}),
            ("final_answer", {}),
        ])
        intent = Intent(
            verb="read", object="files",
            actions=[
                {"verb": "read", "object": "files"},
                {"verb": "group", "object": None},
            ],
        )

        out, info = normalize_terminal(
            fw, intent, "leggi il CSV e raggruppa per categoria")

        self.assertEqual(info["mode"], L)
        self.assertEqual(info["intent_class"], "transform")
        self.assertEqual(info["action"], "final_only")
        self.assertEqual(out.final_message, "${step3.@table}")


class TestNormalizeScalar(unittest.TestCase):
    def test_time_scalar_preserves_value_instead_of_counting_zero(self):
        fw = _fw([("get_now", {}), ("final_answer", {})],
                 final="Sono le ${step1.time}.")
        out, info = normalize_terminal(
            fw, Intent(verb="get", object="numbers"), "che ora è adesso?")
        self.assertEqual(info["mode"], S)
        self.assertEqual(info["data_kind"], "time")
        self.assertEqual(info["action"], "noop")
        self.assertEqual(out.final_message, "Sono le ${step1.time}.")

    def test_time_scalar_replaces_count_terminal_with_time_value(self):
        fw = _fw([("get_now", {}), ("final_answer", {})],
                 final="Totale: ${step1.@count}")
        out, info = normalize_terminal(
            fw, Intent(verb="get", object="numbers"), "che ora è adesso?")
        self.assertEqual(info["action"], "final_only")
        self.assertEqual(out.final_message, "${step1.time}")
        self.assertNotIn("@count", out.final_message)

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
        self.assertEqual(info["action"], "final_only")
        self.assertEqual([step.tool for step in out.steps].count("read_urls_html"), 1)
        self.assertEqual(out.final_message, "${step3.summary}")
        self.assertEqual(out.steps[2].args["context"], "leggi gli articoli su x")

    def test_enumerate_web_resta_W_noop(self):
        fw = _fw([("find_urls", {"query": "x"}), ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="urls"), "cerca link su x")
        self.assertEqual(info["action"], "noop")
        self.assertIs(out, fw)


def _web_presentation_catalog(reader="read_urls_html"):
    return [SimpleNamespace(
        name=tool,
        args_schema={"type": "object", "properties": {
            ("entries" if tool == "describe_entries" else "urls"):
                {"type": "array"}}},
        presentation={"default_view": "list", "list": {
            "mode": "table", "columns": [
                {"key": "title", "source": "title", "cell_max": 180},
                {"key": "url", "source": "url", "cell_max": 240}],
            "max_rows": 200, "max_chars": 16000, "overflow": "notice"}},
    ) for tool in ("find_urls", reader, "describe_entries")]


@pytest.mark.parametrize("reader", ["read_urls_html", "read_urls_pdf", "get_urls"])
@pytest.mark.parametrize("verb", ["find", "read", "describe"])
def test_web_content_default_table_cannot_remove_synthesis(reader, verb):
    query = "cerca se ci sono novità sullo stack amd rocm"
    fw = _fw([
        ("find_urls", {"search_query": "AMD ROCm"}),
        (reader, {"from_step": 1}),
        ("describe_entries", {"from_step": 2}),
        ("final_answer", {}),
    ], final="${step3.summary}")
    fw.runtime_step_cap = 19

    out, info = normalize_terminal(
        fw, Intent(verb=verb, object="urls"), query,
        catalog=_web_presentation_catalog(reader))

    assert info["mode"] == T
    assert not info["manifest_declared"]
    assert [step.tool for step in out.steps] == [step.tool for step in fw.steps]
    assert out.steps[2].args == {
        "from_step": 2, "style": "by_relevance", "context": query,
        "data_kind": "urls"}
    assert out.final_message == "${step3.summary}"
    assert out.runtime_step_cap == 19
    assert fw.steps[2].args == {"from_step": 2}
    repeated, repeat_info = normalize_terminal(
        out, Intent(verb=verb, object="urls"), query,
        catalog=_web_presentation_catalog(reader))
    assert repeat_info["action"] == "noop"
    assert repeated is out


@pytest.mark.parametrize("lang,query", [
    ("it", "cerca se ci sono novità sullo stack amd rocm"),
    ("en", "search for any updates on the AMD ROCm stack"),
])
def test_web_reader_inserts_one_grounded_summary(lang, query, monkeypatch):
    from engine.executor import Executor
    import describe_entries
    import i18n

    monkeypatch.setattr(i18n, "current_lang", lambda: lang)
    catalog = _web_presentation_catalog()
    fw = _fw([
        ("find_urls", {"search_query": "AMD ROCm"}),
        ("read_urls_html", {"from_step": 1}),
        ("final_answer", {}),
    ], final="${step2.@table}")
    out, info = normalize_terminal(
        fw, Intent(verb="find", object="urls", lang=lang), query, catalog=catalog)
    assert info["action"] == "insert_describe_entries"
    source = {"url": "https://example.org/release", "title": "Release notes",
              "body_text": "The new release adds a portable kernel API. " * 20,
              "date": "2026-09-01"}
    summary = ("La versione aggiunge una API portabile per i kernel."
               if lang == "it" else "The release adds a portable kernel API.")
    calls = []

    def synthesize(entries, prompt, **kwargs):
        calls.append(entries)
        assert entries == [source]
        assert query in prompt
        return summary, {"deterministic": False, "latency_ms": 1}

    monkeypatch.setattr(describe_entries, "call_llm", synthesize)

    def invoke(tool, args):
        if tool == "find_urls":
            return {"ok": True, "entries": [{
                "url": source["url"], "title": source["title"]}]}
        if tool == "read_urls_html":
            return {"ok": True, "entries": [source]}
        assert tool == "describe_entries"
        return describe_entries.handle_describe_entries(args)

    result = Executor(invoke_executor=invoke, catalog=catalog).run(out, query=query)
    assert all(step.ok for step in result.steps), [step.result for step in result.steps]
    assert result.final_kind == "answer" and not result.aborted_reason, result
    assert len(calls) == 1
    assert summary in result.final_text
    assert source["url"] in result.final_text
    assert "| title |" not in result.final_text


@pytest.mark.parametrize("empty_at", ["find_urls", "read_urls_html"])
def test_web_content_zero_results_never_synthesizes_facts(empty_at, monkeypatch):
    from engine.executor import Executor
    import describe_entries
    from messages import get as msg

    monkeypatch.setattr(describe_entries, "call_llm", lambda *a, **kw:
                        pytest.fail("No model call is needed for empty evidence"))

    catalog = _web_presentation_catalog()
    fw = _fw([
        ("find_urls", {"search_query": "sample release"}),
        ("read_urls_html", {"from_step": 1}),
        ("final_answer", {}),
    ])
    out, _ = normalize_terminal(
        fw, Intent(verb="find", object="urls"), "find updates", catalog=catalog)
    tools = []

    def invoke(tool, args):
        tools.append(tool)
        if tool == empty_at:
            return {"ok": empty_at != "find_urls", "entries": [],
                    **({"error_class": "search_no_results", "error": msg("MSG_NO_RESULTS")}
                       if empty_at == "find_urls" else {})}
        if tool == "find_urls":
            return {"ok": True, "entries": [{"url": "https://example.org/release"}]}
        assert tool == "describe_entries"
        assert args["entries"] == []
        return describe_entries.handle_describe_entries(args)

    result = Executor(invoke_executor=invoke, catalog=catalog).run(out)
    assert "example.org" not in result.final_text
    if empty_at == "find_urls":
        assert tools == ["find_urls"]
        assert result.final_kind == "error"
        assert result.steps[-1].result["error_class"] == "search_no_results"
    else:
        assert result.final_text.strip()
        assert result.final_kind == "answer"


@pytest.mark.parametrize("verb,tail", [
    ("list", []), ("compute", []), ("move", []),
    ("find", [("extract_entries", {"from_step": 2, "fields": ["version"]})]),
    ("find", [("create_files_spreadsheet", {"from_step": 2})]),
])
def test_web_read_preserves_explicit_enumeration_and_terminal_operations(verb, tail):
    fw = _fw([
        ("find_urls", {"search_query": "sample"}),
        ("read_urls_html", {"from_step": 1}),
        *tail,
        ("describe_entries", {"from_step": 2}),
        ("final_answer", {}),
    ])
    out, info = normalize_terminal(
        fw, Intent(verb=verb, object="urls"), "request",
        catalog=_web_presentation_catalog())
    assert info["mode"] != T
    if verb in {"list", "compute"} or tail:
        assert "describe_entries" not in [step.tool for step in out.steps]


def test_web_discovery_and_head_keep_metadata_presentation():
    for tool, args in [("find_urls", {"search_query": "sample"}),
                       ("get_urls", {"url": "https://example.org", "method": "HEAD"})]:
        fw = _fw([(tool, args), ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="urls"), "request",
            catalog=_web_presentation_catalog(tool))
        assert info["mode"] == L
        assert out.final_message == "${step1.@table}"
        assert [step.tool for step in out.steps] == [tool, "final_answer"]


@pytest.mark.parametrize("style", ["compact", "by_importance"])
def test_web_content_mode_uses_request_not_incidental_summary_preset(style):
    query = "search for any updates on the AMD ROCm stack"
    fw = _fw([
        ("read_urls_html", {"urls": ["https://example.org/release"]}),
        ("describe_entries", {"from_step": 1, "style": style,
                              "context": "generic article metadata"}),
        ("final_answer", {}),
    ], final="${step2.summary}")
    out, _ = normalize_terminal(
        fw, Intent(verb="find", object="urls"), query,
        catalog=_web_presentation_catalog())
    assert out.steps[1].args == {
        "from_step": 1, "style": "by_relevance", "context": query,
        "data_kind": "urls"}
    assert fw.steps[1].args["style"] == style


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

    def test_sink_terminale_prevale_sul_verbo_primario_compound(self):
        # Turn 3dab1575: query find→...→compress. Il verbo primario ``find``
        # classificava l'output come lista e sostituiva la ricevuta multi-file
        # con la tabella tecnica di compress_files (un solo path molto lungo).
        receipt = (
            "Creati ${step1.results.0.path} e ${step2.results.0.path}.")
        fw = _fw([
            ("create_files_spreadsheet", {"path": "/x/dati.xlsx"}),
            ("compress_files", {"dest": "/x/risultati.zip"}),
            ("final_answer", {}),
        ], final=receipt)
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="files"),
            "trova i documenti e crea un foglio, poi genera un archivio")
        self.assertEqual(info["intent_class"], "package")
        self.assertEqual(info["mode"], "file_delivery")
        self.assertEqual(info["action"], "noop")
        self.assertEqual(out.final_message, receipt)

    def test_multi_file_receipt_is_not_replaced_by_last_sink_table(self):
        receipt = (
            "Completato in ${step3.results.0.path}: rapporto e foglio creati "
            "(${step2.count} righe).")
        fw = _fw([
            ("sort_entries", {"entries": [{"x": 1}], "by": "x"}),
            ("create_dirs", {"paths": ["/x/run"]}),
            ("write_files", {"path": "/x/run/report.md"}),
            ("create_files_spreadsheet", {"path": "/x/run/data.xlsx"}),
            ("final_answer", {}),
        ], final=receipt)

        out, info = normalize_terminal(
            fw, Intent(verb="find", object="files"),
            "trova i dati, analizzali e crea rapporto e foglio")

        self.assertEqual(info["mode"], "list")
        self.assertEqual(info["action"], "noop")
        self.assertIs(out, fw)
        self.assertEqual(out.final_message, receipt)

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
        from messages import get as msg
        hist = [StepRun(step_idx=1, tool="find_images_google_photos", args={},
                        result={"ok": True, "entries": [], "used": 0,
                                "message": "Nota: solo app-created."},
                        ok=True, latency_ms=1)]
        out = _render_final_message("${step1.@table}", hist)
        self.assertTrue(out.startswith(msg("MSG_NO_RESULTS")))
        self.assertIn("Nota: solo app-created.", out)

    def test_empty_table_without_message_explains_zero_results(self):
        from engine.executor import _render_final_message
        from messages import get as msg
        hist = [StepRun(step_idx=1, tool="find_files", args={},
                        result={"ok": True, "entries": [], "used": 0},
                        ok=True, latency_ms=1)]
        self.assertEqual(_render_final_message("${step1.@table}", hist), msg("MSG_NO_RESULTS"))

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

    def test_self_presenting_artifact_uses_receipt_not_technical_table(self):
        from engine.executor import _render_final_message
        receipt = "Ho creato «duplicati» con 3.084 righe di dati."
        hist = [StepRun(
            step_idx=1, tool="create_files_spreadsheet", args={},
            result={
                "ok": True,
                "final_message_hint": receipt,
                "results": [{
                    "title": "duplicati", "path": "/tmp/duplicati.xlsx",
                    "rows": 3085, "data_rows": 3084,
                    "kind": "spreadsheet",
                }],
            },
            ok=True, latency_ms=1,
        )]
        self.assertEqual(
            _render_final_message("${step1.@table}", hist), receipt)

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

    def test_tabella_usa_il_sottoinsieme_filtrato(self):
        fw = _fw([("find_files", {"base_path": "/x"}),
                  ("filter_entries", {"from_step": 1,
                                      "mtime_after": "2026-01-01"}),
                  ("final_answer", {})])
        out, info = normalize_terminal(
            fw, Intent(verb="find", object="files"),
            "file modificati nel 2026")
        self.assertEqual(info["mode"], "list")
        self.assertEqual(out.final_message, "${step2.@table}")

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
    def test_email_resume_uses_stable_six_field_schema(self):
        from engine.executor import _entries_table
        t = _entries_table(
            [{
                "date": "2026-08-04", "account": "knowcastle",
                "from": "sender@example.com", "subject": "Oggetto",
                "body_preview": "Testo breve", "category_hints": ["list"],
                "size": 1234, "uid": "42",
            }],
            presentation={"list": {
                "columns": [
                    {"key": "date", "source": "date", "type": "date", "cell_max": 24},
                    {"key": "account", "source": "account", "cell_max": 32},
                    {"key": "from", "source": "from", "cell_max": 80},
                    {"key": "subject", "source": "subject", "cell_max": 140},
                    {"key": "summary", "source": ["summary", "body_preview"], "cell_max": 180},
                    {"key": "category", "source": "category", "fallback": "unclassified", "cell_max": 32},
                ],
                "max_rows": 200, "max_chars": 16000,
            }},
        )
        assert t.splitlines()[0] == (
            "| date | account | from | subject | summary | category |"
        )
        assert "| 04/08/26 | knowcastle | sender@example.com | Oggetto | Testo breve | unclassified |" in t
        assert "size" not in t and "uid" not in t and "body_preview" not in t

    def test_manifest_presentation_contract_controls_projection(self):
        from engine.executor import _entries_table
        t = _entries_table(
            [{"subject": "S", "body_preview": "B", "internal": "x"}],
            presentation={"list": {
                "columns": [
                    {"key": "subject", "source": "subject", "cell_max": 20},
                    {"key": "summary", "source": ["summary", "body_preview"], "cell_max": 20},
                ],
                "max_rows": 10, "max_chars": 1000,
            }},
        )
        assert t.splitlines()[0] == "| subject | summary |"
        assert "internal" not in t
        assert "| S | B |" in t

    def test_manifest_contract_renders_scalar_entries_via_entry_source(self):
        from engine.executor import _entries_table
        t = _entries_table(
            [["cell-a", "cell-b"], ["cell-c"]],
            presentation={"list": {
                "columns": [{"key": "row", "source": "$entry", "cell_max": 80}],
                "max_rows": 10, "max_chars": 1000,
            }},
        )
        assert t.splitlines()[0] == "| row |"
        assert '["cell-a", "cell-b"]' in t

    def test_manifest_contract_hides_undeclared_email_fields(self):
        from engine.executor import _entries_table
        t = _entries_table(
            [{"date": "2026-08-04", "account": "a", "from": "x",
              "subject": "S", "body_preview": "B", "size": 999}],
            presentation={"list": {
                "columns": [
                    {"key": "date", "source": "date", "type": "date", "cell_max": 24},
                    {"key": "account", "source": "account", "cell_max": 32},
                    {"key": "from", "source": "from", "cell_max": 80},
                    {"key": "subject", "source": "subject", "cell_max": 140},
                    {"key": "summary", "source": ["summary", "body_preview"], "cell_max": 180},
                    {"key": "category", "source": "category", "fallback": "unclassified", "cell_max": 32},
                ], "max_rows": 200, "max_chars": 16000,
            }},
        )
        assert t.splitlines()[0] == "| date | account | from | subject | summary | category |"
        assert "size" not in t and "body_preview" not in t

    def test_manifest_contract_marks_nowrap_cells_for_safe_html_renderer(self):
        from engine.executor import _entries_table
        from html_sanitizer import to_safe_html_full
        t = _entries_table(
            [{"account": "account personale"}],
            presentation={"list": {
                "columns": [{"key": "account", "source": "account", "nowrap": True, "cell_max": 32}],
                "max_rows": 10, "max_chars": 1000,
            }},
        )
        assert "\ue000account personale\ue001" in t
        assert '<td class="cell-nowrap">account personale</td>' in to_safe_html_full(t)

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

    def test_path_integrale_e_mtime_iso(self):
        from engine.executor import _entries_table
        path = (r"C:\Users\rober\.local\share\metnos\Documenti\Progetto Atlas"
                r"\Contratti\Budget_Atlas_approvato.pdf")
        t = _entries_table([{"name": "budget.pdf", "path": path,
                             "mtime": 1_784_535_003.0}])
        self.assertIn(path, t)
        self.assertIn("2026-07-20T", t)
        self.assertNotIn("1784535003", t)

    def test_cella_testuale_lunga_usa_ellissi_visibile(self):
        from engine.executor import _entries_table
        t = _entries_table([{"name": "x" * 200}])
        self.assertIn("…", t)
        self.assertNotIn("x" * 200, t)

    def test_budget_caratteri_limita_tabelle_con_path_lunghi(self):
        from engine.executor import _entries_table
        entries = [
            {"path": "/very/long/" + str(index) + "/" + "x" * 160}
            for index in range(20)
        ]
        table = _entries_table(entries, max_rows=20, max_chars=700)
        self.assertLess(len(table), 900)
        self.assertIn("non mostrati", table)
