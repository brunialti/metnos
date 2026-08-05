"""Regression tests for the cross-domain mail/calendar report pipeline."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
os.environ.setdefault("METNOS_ENGINE", "v3")


QUERY = (
    "Analizza le email e gli appuntamenti degli ultimi 45 giorni. Individua "
    "persone, organizzazioni, scadenze, importi e impegni citati in entrambi "
    "i domini; normalizza nomi, indirizzi email, date, valute e stati, unifica "
    "i duplicati mantenendo tutte le origini e segnala conflitti tra messaggi "
    "e calendario. Crea in una nuova cartella Documenti/Verifica Metnos un "
    "rapporto ordinato per scadenza e un foglio con entità, valore "
    "normalizzato, valore originale, origine, responsabile e confidenza. Non "
    "inviare messaggi, non modificare eventi e non sovrascrivere file esistenti."
)

FOCUSED_QUERY = (
    "Analizza in parallelo le email e gli appuntamenti degli ultimi 60 giorni "
    "e riconcilia gli impegni relativi a Policlinico Gemelli, Morciano, TSA "
    "o ecodoppler, visita diabetologica, Standup ed Esercizio. Estrai per "
    "ogni impegno persona, organizzazione, tipo di prestazione o attività, "
    "data, ora, luogo, stato, responsabile e origine. Normalizza nomi, "
    "indirizzi email, date, orari, fusi orari e stati. Elimina logicamente i "
    "messaggi duplicati o quasi duplicati, preservando tutte le origini. "
    "Unifica email ed eventi soltanto quando soggetto, data e tipo di impegno "
    "sono compatibili; non fondere appuntamenti distinti. Distingui "
    "corrispondenze esatte, corrispondenze probabili, elementi presenti "
    "soltanto nelle email, elementi presenti soltanto nel calendario e "
    "cancellazioni prive del corrispondente evento. Segnala orari discordanti "
    "ed eventi senza ora. Crea in una nuova sottocartella di Documenti/Verifica "
    "Metnos un rapporto ordinato prima per gravità del conflitto e poi per "
    "data e un foglio con: entità, tipo di impegno, data normalizzata, ora "
    "normalizzata, valore originale, stato, domini, origini, responsabile, "
    "confidenza e conflitto. Genera anche uno ZIP contenente esclusivamente i "
    "nuovi risultati. Non inviare messaggi, non modificare eventi e non "
    "sovrascrivere file esistenti."
)


def _catalog():
    names = {
        "read_messages", "read_events", "extract_entries", "group_entries",
        "sort_entries", "describe_entries", "create_dirs", "write_files",
        "create_files_spreadsheet", "find_files", "compress_files",
    }
    return [SimpleNamespace(name=name) for name in names]


def _intent():
    from engine.types import Intent
    return Intent(verb="read", object="messages", actions=[
        {"verb": "read", "object": "messages"},
        {"verb": "read", "object": "events"},
        {"verb": "extract", "object": "entries"},
        {"verb": "group", "object": "entries"},
        {"verb": "create", "object": "files"},
    ])


def _broken_framework(include_events=True, mutating=False):
    from engine.types import Framework, StepSpec
    steps = [
        StepSpec("read_messages", {
            "account": "all", "time_window": "last-45d", "max_total": 500}),
    ]
    if include_events:
        steps.append(StepSpec("read_events", {
            "time_window": "last-45d", "top_k": 50}))
    steps.extend([
        StepSpec("extract_entries", {
            "from_step": 1, "fields": ["entità"], "max_per_text": 500}),
        StepSpec("write_files", {
            "from_step": 3,
            "path_template": "Documenti/Verifica Metnos/rapporto_${normalized_name}.md"}),
        StepSpec("create_files_spreadsheet", {
            "from_step": 3, "columns": ["entità", "origine"]}),
    ])
    if mutating:
        steps.insert(2, StepSpec("send_messages", {"to": "x@example.test"}))
    return Framework(steps=steps)


def test_normalizer_builds_one_schema_and_create_only_outputs():
    from engine import dispatch

    out = dispatch._normalize_message_event_report_pipeline(
        _broken_framework(), _intent(), QUERY, _catalog())

    assert [step.tool for step in out.steps] == [
        "read_messages", "read_events", "extract_entries", "extract_entries",
        "group_entries", "sort_entries", "describe_entries", "create_dirs",
        "write_files", "create_files_spreadsheet", "final_answer",
    ]
    mail_extract, event_extract = out.steps[2], out.steps[3]
    assert mail_extract.args["fields"] == event_extract.args["fields"]
    assert mail_extract.args["max_per_text"] == 8
    assert mail_extract.args["max_sources"] == 500
    assert mail_extract.args["batch_size"] == 16
    assert mail_extract.args["drill_down"] is False
    assert event_extract.args["from_step"] == 2

    merge = out.steps[4].args
    assert merge["entries_lists"] == [
        "${step3.entries}", "${step4.entries}"]
    assert merge["dedup_key"] == ["entità", "valore normalizzato"]
    assert merge["cross_domain_key"] == "entità"
    assert merge["domain_field"] == "dominio"
    assert merge["cross_match_fields"][0] == "valore normalizzato"
    assert merge["merge_fields"] == ["origine", "dominio"]
    assert "valore normalizzato" in merge["conflict_fields"]
    assert out.steps[5].args["value_type"] == "date"

    directory = out.steps[7].args["paths"][0]
    assert directory.startswith("Documenti/Verifica Metnos/Run_")
    writer = out.steps[8].args
    assert "path_template" not in writer
    assert writer["path"].endswith("/rapporto_scadenze.md")
    assert writer["mode"] == "fail_if_exists"
    report = out.steps[6].args
    assert report["style"] == "compact"
    assert report["group_by"] == "scadenza"
    sheet = out.steps[9].args
    assert sheet["from_step"] == 6
    assert sheet["path"].endswith("/entita_valori.xlsx")
    assert sheet["columns"] == [
        "entità", "valore normalizzato", "valore originale", "origine",
        "responsabile", "confidenza",
    ]
    assert "${step6.count} righe di dati" in out.final_message


def test_focused_contract_preserves_scope_schema_order_and_archive():
    from engine import dispatch

    out = dispatch._normalize_message_event_report_pipeline(
        _broken_framework(), _intent(), FOCUSED_QUERY, _catalog())

    assert [step.tool for step in out.steps] == [
        "read_messages", "read_events", "extract_entries", "extract_entries",
        "group_entries", "sort_entries", "sort_entries", "describe_entries",
        "create_dirs", "write_files", "create_files_spreadsheet",
        "find_files", "compress_files", "final_answer",
    ]
    assert out.runtime_step_cap == 14
    expected_focus = [
        "Policlinico Gemelli", "Morciano", "TSA", "ecodoppler",
        "visita diabetologica", "Standup", "Esercizio",
    ]
    for extract in out.steps[2:4]:
        assert extract.args["relevance_terms"] == expected_focus
        assert extract.args["state_markers"]["annullato"]
        assert "tipo impegno" in extract.args["fields"]
        assert "data normalizzata" in extract.args["fields"]
        assert "ora normalizzata" in extract.args["fields"]
        assert "non inventare" in extract.args["instruction"]

    merge = out.steps[4].args
    assert merge["dedup_key"] == [
        "entità", "tipo impegno", "data normalizzata", "ora normalizzata"]
    assert merge["cross_domain_key"] == [
        "entità", "tipo impegno", "data normalizzata"]
    assert merge["missing_conflict_fields"] == ["ora normalizzata"]
    assert merge["required_fields_by_domain"] == {
        "calendar": ["ora normalizzata"]}
    assert merge["unmatched_conflict_key"] == [
        "entità", "tipo impegno"]
    assert merge["unmatched_conflict_fields"] == ["data normalizzata"]
    assert merge["match_field"] == "corrispondenza"
    assert merge["match_labels"]["cancelled"] == (
        "cancellazione senza evento")
    assert merge["anchor_field"] == "_relevance_anchors"
    assert merge["anchor_equal_fields"] == ["data normalizzata"]
    assert merge["anchor_match_fields"][0] == "_source_time_mentions"
    assert merge["anchor_within_domains"] == ["email"]
    assert out.steps[5].args == {
        "from_step": 5, "by": "data normalizzata", "desc": False,
        "value_type": "date",
    }
    assert out.steps[6].args == {
        "from_step": 6, "by": "_conflict_count", "desc": True,
        "value_type": "auto",
    }

    sheet = out.steps[10].args
    assert sheet["from_step"] == 7
    assert sheet["columns"] == [
        "entità", "tipo impegno", "data normalizzata", "ora normalizzata",
        "valore originale", "stato", "domini", "origini", "responsabile",
        "confidenza", "conflitto",
    ]
    assert out.steps[11].args["patterns"] == [
        "rapporto_riconciliazione.md", "impegni_riconciliati.xlsx"]
    assert out.steps[12].args["from_step"] == 12
    assert out.steps[12].args["dest"].endswith(
        "/risultati_riconciliazione.zip")
    assert "archivio:" in out.final_message
    assert dispatch._should_cache_plan(out, FOCUSED_QUERY)


def test_focus_parser_is_bounded_and_does_not_filter_a_generic_report():
    from engine import dispatch

    assert dispatch._message_event_focus_terms(FOCUSED_QUERY) == [
        "Policlinico Gemelli", "Morciano", "TSA", "ecodoppler",
        "visita diabetologica", "Standup", "Esercizio",
    ]
    assert dispatch._message_event_focus_terms(QUERY) == []


def test_focus_parser_handles_flattened_markdown_quotes_without_eating_math():
    from engine import dispatch

    quoted = "> " + FOCUSED_QUERY.replace(". ", ". > ")
    assert dispatch._message_event_focus_terms(quoted) == [
        "Policlinico Gemelli", "Morciano", "TSA", "ecodoppler",
        "visita diabetologica", "Standup", "Esercizio",
    ]
    assert dispatch._semantic_query_text("Mantieni importo > 100") == (
        "Mantieni importo > 100")


def test_full_guard_chain_applies_canonical_pipeline_to_cached_bad_plan():
    """The live path runs the whole guard chain, including cache hits."""
    from engine import dispatch

    out = dispatch._apply_deterministic_structure_guards(
        _broken_framework(), _intent(), QUERY, _catalog())
    assert [step.tool for step in out.steps] == [
        "read_messages", "read_events", "extract_entries", "extract_entries",
        "group_entries", "sort_entries", "describe_entries", "create_dirs",
        "write_files", "create_files_spreadsheet", "final_answer",
    ]
    assert out.steps[2].args["batch_size"] == 16
    assert "path_template" not in out.steps[8].args


def test_normalizer_does_not_expand_single_domain_request():
    from engine import dispatch

    original = _broken_framework(include_events=False)
    out = dispatch._normalize_message_event_report_pipeline(
        original, _intent(), QUERY, _catalog())
    assert out is original


def test_normalizer_refuses_an_actual_outbound_mutation():
    from engine import dispatch

    original = _broken_framework(mutating=True)
    out = dispatch._normalize_message_event_report_pipeline(
        original, _intent(), QUERY, _catalog())
    assert out is original
