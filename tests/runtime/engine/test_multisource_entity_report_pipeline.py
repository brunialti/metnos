"""Regression tests for generic three-domain reconciliation pipelines."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
os.environ.setdefault("METNOS_ENGINE", "v3")


CONTACT_QUERY = (
    "Incrocia i PDF, i documenti e i fogli di calcolo presenti in "
    "Documenti/Progetto Atlas, i contatti disponibili e le email degli "
    "ultimi 60 giorni. Estrai persone, organizzazioni, ruoli, indirizzi "
    "email, numeri di telefono, importi, scadenze e decisioni; normalizza "
    "nomi, recapiti, date e valute. Associa le menzioni nei documenti e "
    "nelle email ai contatti, preservando tutte le origini, e segnala "
    "omonimie, recapiti incompatibili, ruoli contraddittori e scadenze "
    "discordanti. Crea una nuova sottocartella in Documenti/Verifica "
    "Metnos, un rapporto ordinato per organizzazione e poi per scadenza, "
    "un foglio con entità, tipo, valore normalizzato, valore originale, "
    "origine, responsabile, confidenza e conflitto, e un archivio compresso "
    "dei soli risultati creati. Non inviare messaggi, non modificare "
    "contatti o file sorgente, non cancellare nulla e non sovrascrivere."
)

CALENDAR_QUERY = (
    "Esamina i PDF, i documenti e i fogli di calcolo presenti in "
    "Documenti/Progetto Atlas, le email e gli appuntamenti degli ultimi "
    "60 giorni. Individua progetti, persone, organizzazioni, scadenze, "
    "importi e impegni; normalizza nomi, email, date, valute e stati. "
    "Riconcilia le entità equivalenti tra file, posta e calendario senza "
    "fondere occorrenze distinte, conserva tutte le origini e segnala "
    "valori contraddittori, date incoerenti e file illeggibili. Crea una "
    "nuova sottocartella in Documenti/Verifica Metnos, genera un rapporto "
    "ordinato prima per progetto e poi per scadenza, un foglio con entità, "
    "tipo, valore normalizzato, valore originale, dominio, origine, "
    "responsabile, confidenza e conflitto, e un archivio ZIP dei soli nuovi "
    "risultati. Non inviare messaggi, non modificare appuntamenti o file "
    "sorgente e non sovrascrivere né cancellare nulla."
)

FOUR_DOMAIN_QUERY = (
    "Incrocia i PDF, i documenti e i fogli di calcolo presenti in "
    "Documenti/Progetto Atlas con le email, gli appuntamenti e i contatti "
    "degli ultimi 60 giorni. Estrai e riconcilia le entità preservando "
    "tutte le origini. Crea un rapporto ordinato prima per gravità del "
    "conflitto e poi per scadenza, un foglio con entità, tipo, valore "
    "normalizzato, valore originale, domini, origini, responsabile, "
    "confidenza e conflitto, e un archivio ZIP. Non inviare "
    "messaggi, non modificare appuntamenti, contatti o file sorgente."
)


def _catalog():
    names = {
        "find_files", "read_files", "read_messages", "read_events",
        "find_contacts", "extract_entries", "group_entries", "sort_entries",
        "describe_entries", "create_dirs", "write_files",
        "create_files_spreadsheet", "compress_files",
    }
    return [SimpleNamespace(name=name) for name in names]


def _intent():
    from engine.types import Intent
    return Intent(verb="read", object="files", actions=[
        {"verb": "find", "object": "files"},
        {"verb": "read", "object": "files"},
        {"verb": "read", "object": "messages"},
        {"verb": "extract", "object": "entries"},
        {"verb": "compress", "object": "files"},
        {"verb": "create", "object": "files"},
    ])


def _broken(contact=True, calendar=False, mutating=False):
    from engine.types import Framework, StepSpec
    steps = [
        StepSpec("find_files", {
            "base_path": "Documenti/Progetto Atlas",
            "patterns": ["*.pdf", "*.docx", "*.xlsx", "*.csv"],
            "recursive": True, "client": "local"}),
        StepSpec("read_files", {"from_step": 1, "parse": "auto"}),
        StepSpec("read_messages", {
            "account": "all", "time_window": "last-60d"}),
    ]
    if calendar:
        steps.append(StepSpec("read_events", {
            "time_window": "last-60d", "top_k": 50}))
    if contact:
        steps.append(StepSpec("find_contacts", {
            "query": "", "max_results": 1000,
            "client": "google_workspace"}))
    if mutating:
        steps.append(StepSpec("send_messages", {"to": "x@example.test"}))
    steps.extend([
        StepSpec("extract_entries", {
            "from_step": len(steps), "fields": ["entità", "tipo"]}),
        StepSpec("create_dirs", {
            "paths": ["Documenti/Verifica Metnos/Progetto Atlas"],
            "parents": True, "client": "local"}),
        StepSpec("write_files", {
            "path": "Documenti/Verifica Metnos/Progetto Atlas/report.md",
            "content": "[Contenuto sintetizzato]"}),
        StepSpec("create_files_spreadsheet", {
            "values": [["entità", "tipo"]],
            "columns": ["entità", "tipo"]}),
        StepSpec("compress_files", {
            "paths": ["report.md"], "dest": "results.zip"}),
        StepSpec("final_answer", {}),
    ])
    return Framework(steps=steps)


def test_files_mail_contacts_use_one_lossless_create_only_dataflow():
    from engine import dispatch

    out = dispatch._normalize_multisource_entity_report_pipeline(
        _broken(), _intent(), CONTACT_QUERY, _catalog())

    assert [step.tool for step in out.steps] == [
        "find_files", "read_files", "read_messages", "find_contacts",
        "extract_entries", "extract_entries", "extract_entries",
        "group_entries", "sort_entries", "sort_entries", "describe_entries",
        "create_dirs", "write_files", "create_files_spreadsheet",
        "find_files", "compress_files", "final_answer",
    ]
    assert out.steps[4].args["from_step"] == 2
    assert out.steps[5].args["from_step"] == 3
    contact_extract = out.steps[6].args
    assert contact_extract["from_step"] == 4
    assert contact_extract["structured_map"]["entità"] == ["name", "id"]
    assert "instruction" not in contact_extract

    merge = out.steps[7].args
    assert merge["entries_lists"] == [
        "${step5.entries}", "${step6.entries}", "${step7.entries}"]
    assert merge["dedup_key"] == [
        "entità", "tipo", "valore normalizzato"]
    assert merge["cross_domain_key"] == "entità"
    assert merge["reconcile_within_domains"] == ["files"]
    assert merge["drop_unmatched_domains"] == ["contacts"]
    assert out.steps[5].args["relevance_entries"] == "${step5.entries}"
    assert "Atlas" in out.steps[5].args["relevance_terms"]
    assert out.steps[8].args["value_type"] == "date"
    assert out.steps[9].args["by"] == "organizzazione"

    directory = out.steps[11].args
    assert directory["exist_ok"] is False
    assert directory["paths"][0].endswith("/Run_${RUNTIME:turn_id}")
    writer = out.steps[12].args
    assert writer["mode"] == "fail_if_exists"
    assert writer["content"].endswith("${step11.summary}")
    assert "${step6.input_source_total}" in writer["content"]
    assert "${step4.used}/${step4.available_total}" in writer["content"]
    assert "record di riferimento letti" in writer["content"]
    assert "${step8.conflicts}" in writer["content"]
    sheet = out.steps[13].args
    assert sheet["from_step"] == 10
    assert "values" not in sheet
    assert sheet["path"].endswith("/entita_riconciliate.xlsx")
    archive = out.steps[15].args
    assert archive["from_step"] == 15
    assert archive["dest"].endswith("/risultati_riconciliazione.zip")
    assert "${step10.count} righe di dati" in out.final_message
    assert out.runtime_step_cap == len(out.steps) == 17


def test_files_mail_calendar_preserves_all_three_domains_and_project_order():
    from engine import dispatch

    out = dispatch._normalize_multisource_entity_report_pipeline(
        _broken(contact=False, calendar=True), _intent(), CALENDAR_QUERY,
        _catalog())

    assert [step.tool for step in out.steps[:8]] == [
        "find_files", "read_files", "read_messages", "read_events",
        "extract_entries", "extract_entries", "extract_entries",
        "group_entries",
    ]
    assert out.steps[6].args["from_step"] == 4
    assert "structured_map" not in out.steps[6].args
    assert out.steps[9].tool == "sort_entries"
    assert out.steps[9].args["by"] == "progetto"
    assert out.steps[10].args["group_by"] == "progetto"
    assert "dominio" in out.steps[13].args["columns"]


def test_multisource_normalizer_is_idempotent_and_refuses_external_mutation():
    from engine import dispatch

    first = dispatch._normalize_multisource_entity_report_pipeline(
        _broken(), _intent(), CONTACT_QUERY, _catalog())
    second = dispatch._normalize_multisource_entity_report_pipeline(
        first, _intent(), CONTACT_QUERY, _catalog())
    assert second.to_dict() == first.to_dict()

    mutating = _broken(mutating=True)
    refused = dispatch._normalize_multisource_entity_report_pipeline(
        mutating, _intent(), CONTACT_QUERY, _catalog())
    assert refused is mutating


def test_specialized_normalizers_do_not_absorb_three_domain_plan():
    from engine import dispatch

    original = _broken(contact=False, calendar=True)
    assert dispatch._normalize_document_report_pipeline(
        original, _intent(), CALENDAR_QUERY, _catalog()) is original
    assert dispatch._normalize_message_event_report_pipeline(
        original, _intent(), CALENDAR_QUERY, _catalog()) is original


def test_runtime_owned_step_budget_does_not_raise_llm_plan_cap():
    from engine.executor import Executor
    from engine.types import Framework, StepSpec

    callback = lambda _tool, _args: {"ok": True, "entries": []}
    ordinary = Framework(
        steps=[StepSpec("probe", {}) for _ in range(13)])
    ordinary_run = Executor(invoke_executor=callback).run(ordinary)
    assert ordinary_run.aborted_reason == "cap_steps 12"
    assert len(ordinary_run.steps) == 12

    canonical = Framework(
        steps=[*[StepSpec("probe", {}) for _ in range(16)],
               StepSpec("final_answer", {})],
        final_message="done",
        runtime_step_cap=17,
    )
    canonical_run = Executor(invoke_executor=callback).run(canonical)
    assert canonical_run.aborted_reason == ""
    assert canonical_run.final_kind == "answer"
    assert canonical_run.final_text == "done"
    assert len(canonical_run.steps) == 16


def test_four_domain_ordering_preserves_runtime_owned_step_budget():
    from engine import dispatch

    canonical = dispatch._normalize_multisource_entity_report_pipeline(
        _broken(contact=True, calendar=True), _intent(), FOUR_DOMAIN_QUERY,
        _catalog())
    assert len(canonical.steps) == 19
    assert canonical.runtime_step_cap == 19
    assert canonical.steps[4].args["query"] == ""
    assert canonical.steps[11].args == {
        "from_step": 11,
        "by": "_conflict_count",
        "desc": True,
        "value_type": "auto",
    }
    assert canonical.steps[12].args["group_by"] == ""
    assert canonical.steps[9].args["coalesce_source_facts"] is True
    assert "domini" in canonical.steps[15].args["columns"]
    assert "origini" in canonical.steps[15].args["columns"]
    for step in canonical.steps[5:9]:
        if step.tool == "extract_entries":
            assert "domini" not in step.args["fields"]
            assert "origini" not in step.args["fields"]
            assert "dominio" in step.args["fields"]
            assert "origine" in step.args["fields"]

    finalized = dispatch._finalize_framework_for_run(
        canonical, _intent(), FOUR_DOMAIN_QUERY, _catalog(), {})

    assert len(finalized.steps) == 19
    assert finalized.runtime_step_cap == 19


def test_blockquoted_multiline_conflict_severity_is_semantic_not_markup():
    from engine import dispatch

    query = FOUR_DOMAIN_QUERY.replace(
        "gravità del conflitto", "gravità del\n> conflitto")
    query = "\n".join("> " + line if not line.startswith(">") else line
                      for line in query.splitlines())
    out = dispatch._normalize_multisource_entity_report_pipeline(
        _broken(contact=True, calendar=True), _intent(), query, _catalog())

    assert out.steps[11].args["by"] == "_conflict_count"
    assert out.steps[11].args["desc"] is True


def test_multisource_contact_universal_wildcard_means_reference_registry():
    from engine import dispatch

    broken = _broken(contact=True, calendar=True)
    contact = next(step for step in broken.steps
                   if step.tool == "find_contacts")
    contact.args["query"] = "*"
    out = dispatch._normalize_multisource_entity_report_pipeline(
        broken, _intent(), FOUR_DOMAIN_QUERY, _catalog())

    assert out.steps[4].tool == "find_contacts"
    assert out.steps[4].args["query"] == ""
