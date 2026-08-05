"""Regressioni del workflow documentale multidominio Atlas (20/7/2026)."""
from __future__ import annotations

import os
import re
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
os.environ.setdefault("METNOS_ENGINE", "v3")


QUERY = (
    "Nella cartella Documenti/Progetto Atlas, trova PDF, documenti e fogli di "
    "calcolo modificati negli ultimi 60 giorni. Elimina logicamente i duplicati "
    "confrontando firme e contenuto, estrai scadenze, importi, persone e "
    "decisioni, quindi crea un riepilogo ordinato per scadenza e un foglio con "
    "origine, data, responsabile, importo e livello di confidenza. Segnala dati "
    "contraddittori e file illeggibili. Salva i risultati in una nuova "
    "sottocartella, genera anche un archivio compresso, ma non sovrascrivere né "
    "cancellare nulla senza approvazione."
)


def _filter_schema():
    return {"properties": {
        "entries": {}, "mtime_after": {}, "mtime_before": {},
        "where_field": {}, "where_value": {}, "where_in": {},
    }}


def test_time_window_replaces_stale_generic_modified_time(monkeypatch):
    from time_window_resolver import resolve_time_window
    monkeypatch.setattr("time_window_parser.parse_time_window",
                        lambda _spec: ("2026-05-21T00:00:00+02:00",
                                       "2026-07-20T00:00:00+02:00"))
    out = resolve_time_window(
        "filter_entries",
        {"entries": [{"path": "/tmp/a.pdf", "mtime": 1.0}],
         "where_field": "modified_time", "where_value": "now_minus_60d"},
        QUERY, _filter_schema())
    assert out["mtime_after"].startswith("2026-05-21")
    assert "where_field" not in out
    assert "where_value" not in out


def test_time_window_preserves_unrelated_generic_predicate(monkeypatch):
    from time_window_resolver import resolve_time_window
    monkeypatch.setattr("time_window_parser.parse_time_window",
                        lambda _spec: ("2026-05-21T00:00:00+02:00",
                                       "2026-07-20T00:00:00+02:00"))
    out = resolve_time_window(
        "filter_entries",
        {"entries": [{"path": "/tmp/a.pdf", "mtime": 1.0}],
         "where_field": "status", "where_value": "open"},
        QUERY, _filter_schema())
    assert out["where_field"] == "status"
    assert out["where_value"] == "open"


def test_read_format_resolver_repairs_json_placeholder():
    from read_format_resolver import resolve_read_format
    schema = {"properties": {
        "parse": {"enum": ["json", "auto"]},
        "deduplicate_content": {"type": "boolean"},
    }}
    out = resolve_read_format(
        "read_files",
        {"parse": "json", "entries": [{"path": r"C:\Dati\a.pdf"}]},
        QUERY, args_schema=schema)
    assert out["parse"] == "auto"
    assert out["deduplicate_content"] is True


def _write_docx(path: Path, text: str) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def _write_xlsx(path: Path, value: str) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{value}'
        '</t></is></c></row></sheetData></worksheet>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", xml)


def test_auto_reader_mixed_formats_dedup_and_unreadable(tmp_path):
    from backends.files import local
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\nBT (Scadenza 2026-08-10) Tj ET\n%%EOF")
    duplicate = tmp_path / "a_copia.pdf"
    duplicate.write_bytes(pdf.read_bytes())
    docx = tmp_path / "b.docx"
    _write_docx(docx, "Responsabile Giulia")
    xlsx = tmp_path / "c.xlsx"
    _write_xlsx(xlsx, "EUR 42000")
    csv = tmp_path / "d.csv"
    csv.write_text("decisione,responsabile\napprovato,Marco\n", encoding="utf-8")
    corrupt = tmp_path / "rotto.pdf"
    corrupt.write_bytes(b"not a pdf")
    entries = [{"path": str(path), "mtime": 123.0, "name": path.name}
               for path in (pdf, duplicate, docx, xlsx, csv, corrupt)]
    out = local.read({"entries": entries, "parse": "auto",
                      "deduplicate_content": True})
    assert out["ok"] is True
    assert out["deduped_count"] == 1
    assert len(out["entries"]) == 5
    assert out["unreadable_count"] == 1
    assert any("Giulia" in item.get("content", "") for item in out["entries"])
    assert any("42000" in item.get("content", "") for item in out["entries"])
    retained_pdf = next(item for item in out["entries"] if item["name"] == "a.pdf")
    assert retained_pdf["duplicate_paths"] == [str(duplicate)]
    assert retained_pdf["sha256"]


def test_unreadable_source_survives_extraction_with_provenance():
    from extract_entries import handle_extract_entries
    out = handle_extract_entries({
        "entries": [{"path": "/tmp/rotto.pdf", "name": "rotto.pdf",
                     "readable": False, "sha256": "abc", "file_type": "pdf",
                     "parse_diagnostic": "invalid_pdf", "content": ""}],
        "fields": ["origine", "data", "readable", "content_hash"],
        "instruction": "estrai e segnala illeggibili",
    })
    assert out["ok"] is True
    assert len(out["entries"]) == 1
    row = out["entries"][0]
    assert row["origine"] == "/tmp/rotto.pdf"
    assert row["readable"] is False
    assert row["content_hash"] == "abc"
    assert row["_parse_diagnostic"] == "invalid_pdf"


def test_audit_fields_enrich_without_changing_primary_cardinality(monkeypatch):
    import extract_entries

    responses = iter([
        ('[{"voce":"A"},{"voce":"B"}]',
         {"in_tokens": 10, "out_tokens": 10, "latency_ms": 10}),
    ])
    monkeypatch.setattr(extract_entries, "call_llm",
                        lambda *args, **kwargs: next(responses))
    out = extract_entries.handle_extract_entries({
        "entries": [{"name": "budget.pdf", "path": "/tmp/budget.pdf",
                     "content": ("Due voci.\nFornitore selezionato: Orion\n"
                                 "Stato: APPROVATO")}],
        "fields": ["voce"],
        "audit_fields": ["fornitore", "stato"],
        "instruction": "estrai le voci e verifica contraddizioni",
        "drill_down": False,
    })
    assert out["ok"] is True
    assert out["used"] == 2
    assert [row["voce"] for row in out["entries"]] == ["A", "B"]
    assert all(row["fornitore"] == "Orion" for row in out["entries"])
    assert all(row["stato"] == "APPROVATO" for row in out["entries"])
    assert out["audit_fields"] == ["fornitore", "stato"]


def test_local_spreadsheet_is_create_only_and_has_data(tmp_path):
    from backends.files import local
    path = tmp_path / "report.xlsx"
    args = {"path": str(path), "entries": [
        {"origine": "a.pdf", "data": "2026-08-10", "importo": 42000}],
        "columns": ["origine", "data", "importo"]}
    first = local.create_spreadsheet(args)
    assert first["ok"] is True
    with zipfile.ZipFile(path) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "origine" in sheet and "a.pdf" in sheet and "42000" in sheet
    second = local.create_spreadsheet(args)
    assert second["ok"] is False
    assert second["error_code"] == "ERR_DST_EXISTS"


def test_local_spreadsheet_maps_plural_source_headers_to_canonical_fields():
    from backends.files import local

    values = local._entries_to_values([{
        "dominio": "files; email",
        "origine": "file:a.pdf; email:m1",
        # Empty planner-created aliases must not mask canonical data.
        "domini": "",
        "origini": "",
    }], ["domini", "origini"])

    assert values == [
        ["domini", "origini"],
        ["files; email", "file:a.pdf; email:m1"],
    ]


def test_spreadsheet_executor_materializes_aliases_for_stale_remote_backend():
    executor_dir = (Path(__file__).resolve().parents[3] / "executors" /
                    "create_files_spreadsheet")
    sys.path.insert(0, str(executor_dir))
    import create_files_spreadsheet as spreadsheet

    original = {
        "entries": [{"dominio": "files; calendar", "origine": "f; e"}],
        "columns": ["domini", "origini"],
    }
    normalized = spreadsheet._materialize_source_column_aliases(original)

    assert normalized["entries"][0]["domini"] == "files; calendar"
    assert normalized["entries"][0]["origini"] == "f; e"
    assert "domini" not in original["entries"][0]


def test_remote_data_plane_uses_cross_language_decimal_strings():
    from agent_runtime import _canonicalize_remote_data_plane
    args = {
        "path": r"C:\Atlas\report.xlsx",
        "threshold": 0.75,
        "entries": [
            {"origine": "a.pdf", "livello confidenza": 0.95,
             "nested": {"values": [0.1, 42]}},
        ],
        "columns": ["origine", "livello confidenza"],
    }
    out = _canonicalize_remote_data_plane(args)
    assert out["entries"][0]["livello confidenza"] == "0.95"
    assert out["entries"][0]["nested"]["values"] == ["0.1", 42]
    # Structural/control floats are not silently coerced: signing still
    # rejects them fail-closed.
    assert out["threshold"] == 0.75
    assert args["entries"][0]["livello confidenza"] == 0.95


def test_group_wildcard_template_aggregates_equal_rendered_paths():
    from backends.files import local

    specs, error = local._collect_write_specs({
        "entries": [
            {"domain": "a.test", "title": "Uno"},
            {"domain": "a.test", "title": "Due"},
            {"domain": "b.test", "title": "Tre"},
        ],
        "path_template": "/tmp/report_{domain}.txt",
        "content_template": "${entry.entries.*.title}",
        "mode": "fail_if_exists",
    })

    assert error is None
    assert [spec["path"] for spec in specs] == [
        "/tmp/report_a.test.txt", "/tmp/report_b.test.txt"]
    assert specs[0]["content"] == "Uno\nDue"
    assert specs[1]["content"] == "Tre"


def test_compact_group_wildcard_template_is_supported():
    from backends.files import local

    specs, error = local._collect_write_specs({
        "entries": [
            {"domain": "a.test", "title": "Uno"},
            {"domain": "a.test", "title": "Due"},
        ],
        "path_template": "/tmp/report_{domain}.txt",
        "content_template": "${entries.*.title}",
        "mode": "fail_if_exists",
    })
    assert error is None
    assert len(specs) == 1
    assert specs[0]["content"] == "Uno\nDue"


def test_explicit_record_template_materializes_then_groups_targets():
    from backends.files import local

    specs, error = local._collect_write_specs({
        "entries": [
            {"domain": "a.test", "title": "Uno", "issue": "redirect"},
            {"domain": "a.test", "title": "Due", "issue": ""},
        ],
        "path_template": "/tmp/report_{domain}.txt",
        "set_fields": {"notes": "${entry.issue}"},
        "content_template": "${entry.title} — ${entry.notes}",
        "mode": "fail_if_exists",
    })

    assert error is None
    assert len(specs) == 1
    assert specs[0]["path"] == "/tmp/report_a.test.txt"
    assert specs[0]["content"] == "Uno — redirect\nDue — "


def test_nested_relative_spreadsheet_path_uses_common_data_root(
        tmp_path, monkeypatch):
    import config
    from backends.files import local

    monkeypatch.setattr(config, "PATH_USER_DATA", tmp_path)
    monkeypatch.setattr(
        local, "_normalize_input_path",
        lambda path: tmp_path / Path(path),
    )
    nested = local._resolve_spreadsheet_output_path(
        "Documenti/Output/dati.xlsx")
    bare = local._resolve_spreadsheet_output_path("dati.xlsx")

    assert nested == tmp_path / "Documenti" / "Output" / "dati.xlsx"
    assert bare == tmp_path / "spreadsheets" / "dati.xlsx"


def test_document_audit_overrides_false_coherence_and_lists_all_anomalies(
        monkeypatch):
    import describe_entries

    messages = {
        "MSG_DOCUMENT_AUDIT_HEADER": "### Verifiche deterministiche",
        "MSG_DOCUMENT_AUDIT_CONTRADICTION": (
            "- Dati contraddittori — {left} vs {right}: {details}."),
        "MSG_DOCUMENT_AUDIT_UNREADABLE": "- File illeggibili — {files}.",
        "MSG_DOCUMENT_AUDIT_DUPLICATES": (
            "- Duplicati eliminati logicamente — {details}; "
            "nessun file è stato cancellato."),
    }
    monkeypatch.setattr(
        describe_entries, "_msg",
        lambda key, **values: messages[key].format(**values))
    entries = [
        {"origine": r"C:\Atlas\Budget_Atlas_approvato.pdf",
         "importo": "120000", "scadenze": "2026-09-30",
         "fornitore": "Orion", "stato": "APPROVATO",
         "_duplicate_paths": [r"C:\Atlas\Budget_Atlas_copia.pdf"]},
        {"origine": r"C:\Atlas\Budget_Atlas_revisione.docx",
         "importo": "135000", "scadenze": "2026-09-15",
         "fornitore": "Vega", "stato": "BOZZA NON APPROVATA"},
        {"origine": r"C:\Atlas\Allegato_corrotto.pdf",
         "readable": False},
    ]
    out = describe_entries._append_document_audit(
        "Tutti i dati estratti sono coerenti e non ci sono contraddizioni.\n"
        "Riepilogo utile.\n"
        "* Errori: file illeggibile. Nessun dato contraddittorio critico "
        "tra file sani.", entries, QUERY, "markdown")

    assert "Tutti i dati estratti sono coerenti" not in out
    assert "Nessun dato contraddittorio" not in out
    assert "Errori: file illeggibile" in out
    assert "Budget_Atlas_approvato.pdf vs Budget_Atlas_revisione.docx" in out
    assert "120000 ↔ 135000" in out
    assert "2026-09-30 ↔ 2026-09-15" in out
    assert "Orion ↔ Vega" in out
    assert "APPROVATO ↔ BOZZA NON APPROVATA" in out
    assert "Allegato_corrotto.pdf" in out
    assert "Budget_Atlas_copia.pdf" in out
    assert "nessun file è stato cancellato" in out


def test_sink_columns_are_clause_scoped():
    from compound_decomposer import derive_sink_fields
    assert derive_sink_fields(QUERY) == [
        "origine", "data", "responsabile", "importo", "livello confidenza"]


def test_sink_columns_strip_completeness_schema_marker():
    from compound_decomposer import derive_extract_fields, derive_sink_fields
    query = (
        "cerca su google drive il file KAKEBO SPESE 2026 e crea uno "
        "spreadsheet con tutti i dati: data, descrizione, importo")
    expected = ["data", "descrizione", "importo"]
    assert derive_sink_fields(query) == expected
    assert derive_extract_fields(query) == expected


def test_sink_columns_survive_markdown_line_wrap_and_stop_at_next_artifact():
    from compound_decomposer import derive_sink_fields
    query = (
        "> Crea un rapporto e un foglio con entità, tipo, valore normalizzato, valore\n"
        "  > originale, origine, responsabile, confidenza e conflitto, e un archivio\n"
        "  > compresso dei soli risultati creati. Non sovrascrivere nulla.")
    assert derive_sink_fields(query) == [
        "entità", "tipo", "valore normalizzato", "valore originale",
        "origine", "responsabile", "confidenza", "conflitto",
    ]


def _catalog_names():
    names = {
        "find_files", "filter_entries", "read_files", "extract_entries",
        "sort_entries", "describe_entries", "create_dirs", "write_files",
        "create_files_spreadsheet", "compress_files", "find_entries",
    }
    return [SimpleNamespace(name=name, args_schema={"properties": {}}, affinity=[])
            for name in names]


def _intent():
    from engine.types import Intent
    return Intent(verb="find", object="files", actions=[
        {"verb": "find", "object": "files"},
        {"verb": "filter", "object": "files"},
        {"verb": "extract", "object": "entries"},
        {"verb": "sort", "object": "entries"},
        {"verb": "write", "object": "files"},
        {"verb": "compress", "object": "files"},
        {"verb": "get", "object": "approval"},
    ])


def test_entries_transform_never_synthesizes_find_entries():
    from engine import dispatch
    from engine.types import Framework, StepSpec
    fw = Framework(steps=[
        StepSpec("find_files", {"base_path": "Documenti/Progetto Atlas"}),
        StepSpec("extract_entries", {"from_step": 1, "fields": ["data"]}),
        StepSpec("final_answer", {}),
    ])
    out = dispatch._enforce_missing_objects(fw, _intent(), QUERY, _catalog_names())
    assert "find_entries" not in [step.tool for step in out.steps]


def test_document_workflow_normalizes_to_single_create_only_dataflow():
    from engine import dispatch
    from engine.types import Framework, StepSpec
    stale = Framework(steps=[
        StepSpec("find_files", {"base_path": "Documenti/Progetto Atlas",
                                "patterns": ["*.pdf", "*.docx", "*.xlsx", "*.csv"],
                                "recursive": True}),
        StepSpec("filter_entries", {"kind": "file",
                                    "where_field": "modified_time",
                                    "where_value": "now_minus_60d"}),
        StepSpec("read_files", {"parse": "json", "from_step": 2}),
        StepSpec("find_files", {"base_path": "Documenti/Progetto Atlas"}),
        StepSpec("find_entries", {}),
        StepSpec("final_answer", {}),
    ])
    out = dispatch._normalize_document_report_pipeline(
        stale, _intent(), QUERY, _catalog_names())
    assert [step.tool for step in out.steps] == [
        "find_files", "filter_entries", "read_files", "extract_entries",
        "sort_entries", "describe_entries", "create_dirs", "write_files",
        "create_files_spreadsheet", "find_files", "compress_files",
        "final_answer",
    ]
    assert out.steps[2].args["parse"] == "auto"
    assert out.steps[2].args["deduplicate_content"] is True
    assert out.steps[1].args.get("type") == "file"
    assert "kind" not in out.steps[1].args
    assert out.steps[1].args["where_field"] == "path"
    assert "Risultati_Metnos_" in out.steps[1].args["where_regex"]
    assert out.steps[7].args["mode"] == "fail_if_exists"
    assert "fornitore" not in out.steps[3].args["fields"]
    assert "stato" not in out.steps[3].args["fields"]
    assert out.steps[3].args["audit_fields"] == ["fornitore", "stato"]
    assert out.steps[8].args["columns"] == [
        "origine", "data", "responsabile", "importo", "livello confidenza"]
    assert all(not step.tool.startswith(("delete_", "move_")) for step in out.steps)
    assert "${step7.results.0.path}" in out.final_message
    assert "${step9.results.0.rows}" in out.final_message
    assert "${step11.results.0.path}" in out.final_message


def test_document_workflow_filter_excludes_prior_result_folders():
    from engine import dispatch
    from engine.types import Framework, StepSpec
    fw = Framework(steps=[
        StepSpec("find_files", {"base_path": "Documenti/Progetto Atlas",
                                "patterns": ["*.pdf", "*.xlsx"]}),
        StepSpec("filter_entries", {"kind": "file"}),
        StepSpec("final_answer", {}),
    ])
    normalized = dispatch._normalize_document_report_pipeline(
        fw, _intent(), QUERY, _catalog_names())
    args = dict(normalized.steps[1].args)
    pattern = re.compile(args["where_regex"], re.IGNORECASE)
    assert pattern.search(r"C:\Atlas\Dati\Scadenze_Atlas.xlsx")
    assert not pattern.search(
        r"C:\Atlas\Risultati_Metnos_old\dati_estratti.xlsx")


def test_full_finalizer_repairs_exact_stale_plan_with_signed_catalog():
    from engine import dispatch
    from engine.types import Framework, StepSpec
    from loader import load_catalog
    stale = Framework(steps=[
        StepSpec("find_files", {"base_path": "Documenti/Progetto Atlas",
                                "patterns": ["*.pdf", "*.docx", "*.xlsx", "*.csv"],
                                "recursive": True}),
        StepSpec("filter_entries", {"where_field": "modified_time",
                                    "where_value": "now_minus_60d"}),
        StepSpec("read_files", {"parse": "json"}),
        StepSpec("find_entries", {}),
        StepSpec("get_approval", {}),
        StepSpec("final_answer", {}),
    ])
    out = dispatch._finalize_framework_for_run(
        stale, _intent(), QUERY, load_catalog(),
        {"turn_id": "test-turn", "actor": "host", "lang": "it"})
    tools = [step.tool for step in out.steps]
    assert len(tools) == 12
    assert tools[:5] == ["find_files", "filter_entries", "read_files",
                         "extract_entries", "sort_entries"]
    assert tools[-3:] == ["find_files", "compress_files", "final_answer"]
    assert "find_entries" not in tools
    assert "get_approval" not in tools


def test_logical_duplicate_wording_survives_delete_intent_noise():
    from engine import dispatch
    from engine.types import Framework, StepSpec
    noisy = _intent()
    noisy.actions.insert(2, {"verb": "delete", "object": "files"})
    fw = Framework(steps=[
        StepSpec("find_files", {"base_path": "Documenti/Progetto Atlas",
                                "patterns": ["*.pdf"]}),
        StepSpec("final_answer", {}),
    ])
    out = dispatch._normalize_document_report_pipeline(
        fw, noisy, QUERY, _catalog_names())
    assert out.steps[2].tool == "read_files"
    assert out.steps[2].args["deduplicate_content"] is True
    assert not any(step.tool.startswith("delete_") for step in out.steps)


def test_value_sinks_may_transfer_server_records_to_device():
    from engine import executor
    from engine.types import StepRun
    history = [StepRun(1, "sort_entries", {}, {"entries": [{"x": 1}]},
                       True, 1, host="server")]
    spreadsheet = SimpleNamespace(
        tool="create_files_spreadsheet", args={"from_step": 1})
    path_reader = SimpleNamespace(tool="read_files", args={"from_step": 1})
    assert executor._references_server_producer(spreadsheet, history) is False
    assert executor._references_server_producer(path_reader, history) is True


def test_pure_filter_preserves_windows_path_authority():
    from engine import executor
    from engine.types import StepRun
    source = StepRun(1, "find_files", {}, {"entries": [
        {"path": r"C:\Atlas\Dati\Scadenze_Atlas.xlsx"}]}, True, 1,
        host="PC-ROBERTO", data_host="PC-ROBERTO")
    filter_step = SimpleNamespace(
        tool="filter_entries", args={"from_step": 1})
    data_host = executor._data_host_for_step(filter_step, [source], "server")
    assert data_host == "PC-ROBERTO"
    filtered = StepRun(2, "filter_entries", {}, {"entries": [
        {"path": r"C:\Atlas\Dati\Scadenze_Atlas.xlsx"}]}, True, 1,
        host="server", data_host=data_host)
    reader = SimpleNamespace(tool="read_files", args={"from_step": 2})
    assert executor._references_server_producer(reader, [source, filtered]) is False
