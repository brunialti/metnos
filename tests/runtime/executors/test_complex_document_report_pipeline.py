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


class _AuditLexicon:
    fields = {
        "origin": ["origine-fr"], "readable": ["lisible-fr"],
        "duplicates": ["doublons-fr"], "amount": ["montant-fr"],
        "deadline": ["echeance-fr"], "status": ["etat-fr"],
    }
    audit = {"supplier": ["fournisseur-fr"], "status": ["etat-fr"]}
    relevance_generic_tokens = frozenset({"file", "document"})
    _field_owner = {
        "origin": "origin", "origine": "origin", "origine-fr": "origin",
        "readable": "readable", "lisible-fr": "readable",
        "duplicates": "duplicates", "duplicate_paths": "duplicates",
        "doublons-fr": "duplicates", "amount": "amount",
        "importo": "amount", "montant-fr": "amount",
        "deadline": "deadline", "scadenza": "deadline",
        "echeance-fr": "deadline", "status": "status", "stato": "status",
        "etat-fr": "status",
    }
    _audit_owner = {
        "supplier": "supplier", "fornitore": "supplier",
        "fournisseur-fr": "supplier", "status": "status",
        "stato": "status", "etat-fr": "status",
    }
    _variants = frozenset({
        "approved", "approvato", "final", "draft", "bozza", "revisione",
        "revision", "copia", "copy", "brouillon-fr", "final-fr",
    })

    @staticmethod
    def _key(value):
        return str(value).casefold().replace("_", " ").strip()

    def canonical_field(self, surface):
        return self._field_owner.get(self._key(surface))

    def canonical_audit(self, surface):
        return self._audit_owner.get(self._key(surface))

    def is_variant_token(self, token):
        return self._key(token) in self._variants

    def surface(self, canonical):
        return self.fields[canonical][0]


def _audit_snapshot(describe_entries):
    import detection_lexicon_seed_runtime_safety as safety

    return describe_entries._DocumentAuditSnapshot(
        lexicon=_AuditLexicon(),
        patterns={
            safety.DOCUMENT_AUDIT_CONFLICT_INTENT: (
                re.compile(r"contradditt|contradict|conflict|incoherent", re.I),
            ),
            safety.DOCUMENT_AUDIT_UNREADABLE_INTENT: (
                re.compile(r"illeggibil|unreadable|illisible", re.I),
            ),
            safety.DOCUMENT_AUDIT_DUPLICATE_INTENT: (
                re.compile(r"duplicat|deduplic|doublon", re.I),
            ),
            safety.DOCUMENT_NO_CONTRADICTION_CLAIM: (
                re.compile(r"(?im)^.*(?:all coherent|tutti .* coerenti).*(?:\n|$)"),
            ),
        },
        templates={
            "ERR_EXT_SVC_UNAVAILABLE": "audit unavailable",
            "MSG_DOCUMENT_AUDIT_HEADER": "### Deterministic checks",
            "MSG_DOCUMENT_AUDIT_CONTRADICTION": (
                "- Conflicting data — {left} vs {right}: {details}."
            ),
            "MSG_DOCUMENT_AUDIT_UNREADABLE": "- Unreadable files — {files}.",
            "MSG_DOCUMENT_AUDIT_DUPLICATES": (
                "- Duplicates — {details}; no file was deleted."
            ),
        },
    )


def test_document_audit_compact_uses_original_entries_and_canonical_aliases(
        monkeypatch):
    import describe_entries

    snapshot = _audit_snapshot(describe_entries)
    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot", lambda: snapshot,
    )
    entries = [
        {"origine-fr": "/a/Budget.pdf", "montant-fr": "10"},
        {"origine-fr": "/b/Budget.pdf", "montant-fr": "20"},
        {"origine-fr": "/a/Atlas_final.pdf", "montant-fr": "30",
         "doublons-fr": ["/copies/Atlas_copy.pdf"]},
        {"origine-fr": "/a/Atlas_draft.pdf", "montant-fr": "40"},
        {"origine-fr": "/a/Annexe.pdf", "lisible-fr": False},
        {"origine-fr": "/a/file_final.pdf", "montant-fr": "50"},
        {"origine-fr": "/a/file_draft.pdf", "montant-fr": "60"},
    ]
    out = describe_entries.handle_describe_entries({
        "entries": entries, "style": "compact", "data_kind": "entries",
        "format": "markdown",
        "context": "check conflicts, fichiers illisibles et doublons",
    })

    assert out["ok"] is True
    assert out["document_audit"]["state"] == "completed"
    conflicts = out["document_audit"]["conflicts"]
    assert any(item["left"] == "/a/Budget.pdf" for item in conflicts)
    assert any("Atlas_final.pdf" in item["left"] for item in conflicts)
    assert not any("file_final.pdf" in item["left"] for item in conflicts)
    assert out["document_audit"]["unreadable"] == ["Annexe.pdf"]
    assert out["document_audit"]["duplicates"] == [
        "/a/Atlas_final.pdf ← /copies/Atlas_copy.pdf",
    ]
    assert "### Deterministic checks" in out["summary"]


def test_document_audit_map_reduce_runs_once_on_original_entries(monkeypatch):
    import describe_entries

    captures = 0
    snapshot = _audit_snapshot(describe_entries)

    def capture_once():
        nonlocal captures
        captures += 1
        return snapshot

    seen = []
    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot", capture_once,
    )
    monkeypatch.setattr(
        describe_entries, "_pack_entries", lambda entries: (entries[:1], True),
    )
    monkeypatch.setattr(describe_entries, "_DESCRIBE_MAPREDUCE", True)
    monkeypatch.setattr(
        describe_entries, "_describe_map_reduce",
        lambda entries, **_kwargs: seen.extend(entries) or {
            "ok": True, "summary": "all coherent", "item_count": len(entries),
        },
    )
    original = [
        {"origin": "/a/Atlas_final.pdf", "amount": "10"},
        {"origin": "/a/Atlas_draft.pdf", "amount": "20"},
    ]
    out = describe_entries.handle_describe_entries({
        "entries": original, "context": "check conflicts", "format": "plain",
    })

    assert captures == 1
    assert seen == original
    assert out["ok"] is True
    assert "all coherent" not in out["summary"]
    assert "10 ↔ 20" in out["summary"]


@pytest.mark.parametrize("fmt", ["json", "html"])
def test_document_audit_structured_formats_fail_closed(monkeypatch, fmt):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: _audit_snapshot(describe_entries),
    )
    out = describe_entries.handle_describe_entries({
        "entries": [{"origin": "Budget_final.pdf", "amount": "10"}],
        "style": "compact", "data_kind": "entries", "format": fmt,
        "context": "check conflicts",
    })

    assert out["ok"] is False
    assert out["error_code"] == "ERR_EXT_SVC_UNAVAILABLE"
    assert "summary" not in out
    assert out["document_audit"]["state"] == "unsupported_format"


def test_document_audit_invalid_format_is_denied_before_capture(monkeypatch):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: pytest.fail("invalid format must not acquire audit authority"),
    )
    out = describe_entries.handle_describe_entries({
        "entries": [], "format": "yaml", "context": "check conflicts",
    })
    assert out["ok"] is False
    assert out["error_code"] == "ERR_FMT_INVALID"


def test_document_audit_composite_family_is_read_once(monkeypatch):
    import describe_entries
    import detection_lexicon as detection_lexicon
    import detection_lexicon_seed_runtime_safety as safety

    snapshot = _audit_snapshot(describe_entries)
    family_reads = []
    template_reads = 0
    concepts = (
        safety.DOCUMENT_AUDIT_CONFLICT_INTENT,
        safety.DOCUMENT_AUDIT_UNREADABLE_INTENT,
        safety.DOCUMENT_AUDIT_DUPLICATE_INTENT,
        safety.DOCUMENT_NO_CONTRADICTION_CLAIM,
    )
    resources = {
        concept: ({"payload": [patterns[0].pattern]},)
        for concept, patterns in snapshot.patterns.items()
    }
    resources["reconciliation.stub"] = ({"payload": {"stub": ["stub"]}},)

    def family_once(kinds, **options):
        family_reads.append((dict(kinds), dict(options)))
        if len(family_reads) > 1:
            raise AssertionError("composite family was reacquired")
        return resources

    def templates_after_cutover():
        nonlocal template_reads
        template_reads += 1
        if template_reads > 1:
            raise AssertionError("audit templates were reacquired")
        # Simulate a commit after the family snapshot.  Compiled intent must
        # remain frozen and no later decision may consult this mutable source.
        resources[safety.DOCUMENT_AUDIT_CONFLICT_INTENT][0]["payload"] = [
            "never-match",
        ]
        return dict(snapshot.templates)

    monkeypatch.setattr(describe_entries._reconciliation_lex,
                        "_ensure_registered", lambda: None)
    monkeypatch.setattr(describe_entries._reconciliation_lex,
                        "family_kinds", lambda: {"reconciliation.stub": "mapping"})
    monkeypatch.setattr(describe_entries._reconciliation_lex,
                        "from_resources", lambda _resources: snapshot.lexicon)
    monkeypatch.setattr(safety, "_ensure_registered", lambda: None)
    monkeypatch.setattr(detection_lexicon, "native_ready_family_resources",
                        family_once)
    monkeypatch.setattr(describe_entries, "_ready_audit_templates",
                        templates_after_cutover)

    out = describe_entries.handle_describe_entries({
        "entries": [], "context": "check conflicts", "format": "plain",
    })
    assert out["ok"] is True
    assert len(family_reads) == 1
    assert template_reads == 1
    assert out["document_audit"]["request"]["conflicts"] is True
    assert set(family_reads[0][0]) == {"reconciliation.stub", *concepts}
    assert family_reads[0][1] == {
        "require_manual": True, "include_reviewed_baselines": True,
    }


def test_document_audit_family_unavailable_is_typed_failure(monkeypatch):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot", lambda: None,
    )
    out = describe_entries.handle_describe_entries({
        "entries": [], "context": "check conflicts", "format": "markdown",
    })
    assert out == {
        "ok": False,
        "error_code": "ERR_EXT_SVC_UNAVAILABLE",
        "error": "ERR_EXT_SVC_UNAVAILABLE",
        "document_audit": {
            "state": "unavailable",
            "request": {"conflicts": False, "unreadable": False,
                        "duplicates": False},
            "conflicts": [], "unreadable": [], "duplicates": [],
            "error_code": "ERR_EXT_SVC_UNAVAILABLE",
        },
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "partial", "pending", "source_pending", "source_lang",
        "version_hash", "source_hash", "placeholder",
    ],
)
def test_document_audit_template_family_is_atomic_ready_and_provenanced(
        monkeypatch, mutation):
    import describe_entries

    templates = _audit_snapshot(describe_entries).templates

    def digest(text):
        return "sha256:" + describe_entries.hashlib.sha256(
            text.encode("utf-8"),
        ).hexdigest()

    rows = []
    for key, text in templates.items():
        source_hash = digest(text)
        rows.append((key, "en", text, 0, "", source_hash, ""))
        translated = "FR " + text
        rows.append((
            key, "fr", translated, 0, "en", digest(translated), source_hash,
        ))

    target_key = "MSG_DOCUMENT_AUDIT_CONTRADICTION"
    target = next(
        index for index, row in enumerate(rows)
        if row[0] == target_key and row[1] == "fr"
    )
    row = list(rows[target])
    if mutation == "partial":
        rows.pop(target)
    elif mutation == "pending":
        row[3] = 1
        rows[target] = tuple(row)
    elif mutation == "source_pending":
        source_index = next(
            index for index, source_row in enumerate(rows)
            if source_row[0] == target_key and source_row[1] == "en"
        )
        source_row = list(rows[source_index])
        source_row[3] = 1
        rows[source_index] = tuple(source_row)
    elif mutation == "source_lang":
        row[4] = "it"
        rows[target] = tuple(row)
    elif mutation == "version_hash":
        row[5] = "sha256:wrong"
        rows[target] = tuple(row)
    elif mutation == "source_hash":
        row[6] = "sha256:wrong"
        rows[target] = tuple(row)
    else:
        row[2] = str(row[2]) + " {unexpected}"
        row[5] = digest(str(row[2]))
        rows[target] = tuple(row)

    class Connection:
        def execute(self, _sql, _keys):
            return SimpleNamespace(fetchall=lambda: list(rows))

    monkeypatch.setattr(describe_entries._i18n, "_open", Connection)
    monkeypatch.setattr(describe_entries._i18n, "current_lang", lambda: "fr")
    monkeypatch.setattr(
        describe_entries._i18n, "language_chain", lambda _lang: ("fr", "en"),
    )
    monkeypatch.setattr(
        describe_entries._i18n._C, "BOOTSTRAP_LANGUAGE", "en",
    )

    assert describe_entries._ready_audit_templates() is None


def test_document_audit_real_template_family_is_whole_and_ready(monkeypatch):
    import sqlite3
    import describe_entries

    connection = sqlite3.connect(
        Path(describe_entries.__file__).resolve().parents[1]
        / "install/data/i18n_seed.sqlite",
    )
    monkeypatch.setattr(describe_entries._i18n, "_open", lambda: connection)

    templates = describe_entries._ready_audit_templates()

    assert templates is not None
    assert set(templates) == set(describe_entries._AUDIT_TEMPLATE_FIELDS)


def test_document_audit_uses_meta_record_as_original_evidence(monkeypatch):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: _audit_snapshot(describe_entries),
    )
    out = describe_entries.handle_describe_entries({
        "entries": [
            {"_meta": True, "_source_path": "/a/Budget.pdf", "amount": "10",
             "style": "compact", "kind": "entries"},
            {"_source_path": "/b/Budget_draft.pdf", "amount": "20"},
        ],
        # The first mapping is deliberately malformed as a legacy descriptor,
        # so its style/kind fields are evidence rather than control metadata.
        "style": "compact", "data_kind": "entries",
        "context": "check conflicts", "format": "plain",
    })

    assert out["ok"] is True
    assert len(out["document_audit"]["conflicts"]) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_document_audit_conflicting_aliases_fail_closed_order_independently(
        monkeypatch, reverse):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: _audit_snapshot(describe_entries),
    )
    aliases = [("amount", "10"), ("montant-fr", "20")]
    if reverse:
        aliases.reverse()
    first = {"origin": "Budget_final.pdf", **dict(aliases)}
    out = describe_entries.handle_describe_entries({
        "entries": [first, {"origin": "Budget_draft.pdf", "amount": "30"}],
        "style": "compact", "data_kind": "entries", "format": "plain",
        "context": "check conflicts",
    })

    assert out["ok"] is False
    assert out["error_code"] == "ERR_ARG_INVALID"
    assert out["document_audit"]["state"] == "invalid_evidence"


def test_document_audit_path_identity_status_alias_and_unicode_family(
        monkeypatch):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: _audit_snapshot(describe_entries),
    )
    out = describe_entries.handle_describe_entries({
        "entries": [
            {"_source_path": "/a/预算_final.pdf",
             "_source_name": "预算_final.pdf", "status": "approved"},
            {"_source_path": "/b/预算_draft.pdf",
             "_source_name": "预算_draft.pdf", "etat-fr": "draft"},
        ],
        "style": "compact", "data_kind": "entries", "format": "plain",
        "context": "check conflicts",
    })

    assert out["ok"] is True
    conflict = out["document_audit"]["conflicts"][0]
    assert conflict["left"] == "预算_final.pdf"
    assert conflict["right"] == "预算_draft.pdf"
    assert "approved ↔ draft" in conflict["details"]


def test_document_audit_preserves_same_basename_path_collision(monkeypatch):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: _audit_snapshot(describe_entries),
    )
    out = describe_entries.handle_describe_entries({
        "entries": [
            {"_source_path": "/a/Budget.pdf", "_source_name": "Budget.pdf",
             "amount": "10"},
            {"_source_path": "/b/Budget.pdf", "_source_name": "Budget.pdf",
             "amount": "20"},
        ],
        "style": "compact", "data_kind": "entries", "format": "plain",
        "context": "check conflicts",
    })

    conflict = out["document_audit"]["conflicts"][0]
    assert (conflict["left"], conflict["right"]) == (
        "/a/Budget.pdf", "/b/Budget.pdf",
    )


def test_document_audit_compares_divergent_records_from_same_path(monkeypatch):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: _audit_snapshot(describe_entries),
    )
    out = describe_entries.handle_describe_entries({
        "entries": [
            {"_source_path": "/a/file.pdf", "amount": "10"},
            {"_source_path": "/a/file.pdf", "amount": "20"},
        ],
        "style": "compact", "data_kind": "entries", "format": "plain",
        "context": "check conflicts",
    })

    assert out["ok"] is True
    assert out["document_audit"]["conflicts"] == [{
        "left": "/a/file.pdf", "right": "/a/file.pdf",
        "details": "montant-fr: 10 ↔ 20",
    }]


def test_document_audit_same_path_identical_records_have_no_conflict(monkeypatch):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: _audit_snapshot(describe_entries),
    )
    record = {"_source_path": "/a/file.pdf", "amount": "10"}
    out = describe_entries.handle_describe_entries({
        "entries": [record, dict(record)],
        "style": "compact", "data_kind": "entries", "format": "plain",
        "context": "check conflicts",
    })

    assert out["ok"] is True
    assert out["document_audit"]["conflicts"] == []


def test_document_audit_duplicate_paths_keep_complete_normalized_identity(
        monkeypatch):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: _audit_snapshot(describe_entries),
    )
    out = describe_entries.handle_describe_entries({
        "entries": [{
            "_source_path": r"C:\docs\Budget.pdf",
            "_duplicate_paths": [
                r"C:\copies\Budget.pdf", r"D:\copies\Budget.pdf",
            ],
        }],
        "style": "compact", "data_kind": "entries", "format": "plain",
        "context": "check duplicates",
    })

    assert out["document_audit"]["duplicates"] == [
        "C:/docs/Budget.pdf ← C:/copies/Budget.pdf",
        "C:/docs/Budget.pdf ← D:/copies/Budget.pdf",
    ]


@pytest.mark.parametrize(
    "head",
    [
        {"_meta": {"truthy": True}, "style": "compact"},
        {"_meta": True, "style": "compact", "unknown": "evidence"},
    ],
)
def test_legacy_header_does_not_extract_truthy_or_open_shape(head):
    import describe_entries

    entries = [head, {"origin": "Budget.pdf"}]
    header, retained = describe_entries._extract_header(entries)

    assert header is None
    assert retained is entries


def test_legacy_header_extracts_exact_true_closed_shape():
    import describe_entries

    head = {"_meta": True, "style": "compact", "format": "plain"}
    tail = {"origin": "Budget.pdf"}
    header, retained = describe_entries._extract_header([head, tail])

    assert header is head
    assert retained == [tail]


@pytest.mark.parametrize(
    ("fmt", "escaped"),
    [("markdown", True), ("bullet_list", True), ("plain", False)],
)
def test_document_audit_untrusted_values_are_safe_for_output_format(
        monkeypatch, fmt, escaped):
    import describe_entries

    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: _audit_snapshot(describe_entries),
    )
    out = describe_entries.handle_describe_entries({
        "entries": [
            {"origin": "Budget_final.pdf", "amount": "*10*\r\n# injected"},
            {"origin": "Budget_draft.pdf", "amount": "20"},
        ],
        "style": "compact", "data_kind": "entries", "format": fmt,
        "context": "check conflicts",
    })

    detail = out["document_audit"]["conflicts"][0]["details"]
    assert "\r" not in detail and "\n" not in detail
    if escaped:
        assert "\\*10\\*" in out["summary"]
        assert "\\# injected" in out["summary"]
    else:
        assert "*10* # injected" in out["summary"]
        assert "\\*10\\*" not in out["summary"]


def test_describe_initial_validation_and_content_fetch_are_typed(monkeypatch):
    import describe_entries

    assert describe_entries.handle_describe_entries(None)["error_code"] == (
        "ERR_ARG_INVALID"
    )
    assert describe_entries.handle_describe_entries({
        "entries": [], "max_tokens": "not-an-int",
    })["error_code"] == "ERR_ARG_INVALID"
    assert describe_entries.handle_describe_entries({
        "entries": [], "format": {"unsafe": "shape"},
    })["error_code"] == "ERR_ARG_INVALID"
    assert describe_entries.handle_describe_entries({
        "entries": [], "max_tokens": 0,
    })["error_code"] == "ERR_ARG_INVALID"
    monkeypatch.setattr(
        describe_entries, "_capture_document_audit_snapshot",
        lambda: _audit_snapshot(describe_entries),
    )
    out = describe_entries.handle_describe_entries({
        "entries": [{"url": "https://example.test", "title": "Metadata"}],
        "context": "", "format": "plain",
    })
    assert out["ok"] is False
    assert out["error_code"] == "ERR_ARG_MISSING"
    assert out["error_class"] == "needs_content_fetch"
    assert "entries.content" in out["error"]


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
