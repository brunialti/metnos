"""General plan invariants exposed by live turn 94d748bc."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from engine.dispatch import (  # noqa: E402
    _enforce_create_only_artifact_policy,
    _normalize_grouped_artifact_templates,
    _normalize_filter_operation_values,
    _propagate_sink_schema_to_extract,
)
from engine.types import Framework, StepSpec  # noqa: E402


def _framework(*steps):
    return Framework(steps=[StepSpec(tool=tool, args=dict(args))
                            for tool, args in steps])


def test_operation_marker_cannot_erase_carrier_before_group_dedup():
    fw = _framework(
        ("extract_entries", {"fields": ["url"]}),
        ("filter_entries", {"from_step": 1, "kind": "dedup",
                            "where_field": "status",
                            "where_not_in": ["timeout"]}),
        ("group_entries", {"from_step": 2, "dedup_key": "url"}),
    )
    out = _normalize_filter_operation_values(fw)
    assert "kind" not in out.steps[1].args
    assert out.steps[1].args["where_field"] == "status"
    assert out.steps[1].args["where_not_in"] == ["timeout"]


def test_unique_predicate_marker_cannot_erase_carrier_before_group_dedup():
    fw = _framework(
        ("extract_entries", {"fields": ["url"]}),
        ("filter_entries", {"from_step": 1, "where_field": "url",
                            "where_value": "unique"}),
        ("group_entries", {"from_step": 2, "dedup_key": "url"}),
    )
    out = _normalize_filter_operation_values(fw)
    assert out.steps[1].args == {"from_step": 1}


def test_real_filter_category_and_unpaired_marker_are_not_rewritten():
    category = _framework(
        ("filter_entries", {"entries": [], "kind": "document"}),
        ("group_entries", {"dedup_key": "path"}),
    )
    assert (_normalize_filter_operation_values(category)
            .steps[0].args["kind"] == "document")

    no_group = _framework(
        ("filter_entries", {"entries": [], "kind": "dedup"}),
        ("final_answer", {}),
    )
    assert (_normalize_filter_operation_values(no_group)
            .steps[0].args["kind"] == "dedup")


def test_create_only_policy_is_domain_neutral_and_idempotent():
    fw = _framework(
        ("read_anything", {}),
        ("create_dirs", {"paths": ["Documenti/Output"],
                         "parents": True, "exist_ok": True}),
        ("write_files", {
                         "path": "Documenti/Output/report.md",
                         "path_template": "Documenti/Output/report_{domain}.md",
                         "content": "report", "mode": "overwrite"}),
        ("write_files_spreadsheet", {
            "from_step": 1, "columns": ["a"],
            "spreadsheet_id": "Documenti/Output/data.xlsx",
            "mode": "overwrite", "client": "local",
        }),
        ("final_answer", {}),
    )
    catalog = [SimpleNamespace(name="create_files_spreadsheet")]
    query = "Crea una nuova cartella e non sovrascrivere file esistenti."
    out = _enforce_create_only_artifact_policy(fw, query, catalog)

    directory = out.steps[1]
    report = out.steps[2]
    sheet = out.steps[3]
    assert directory.args["paths"] == [
        "Documenti/Output/Run_${RUNTIME:turn_id}"]
    assert directory.args["exist_ok"] is False
    assert report.args["path"] == (
        "Documenti/Output/Run_${RUNTIME:turn_id}/report.md")
    assert report.args["path_template"] == (
        "Documenti/Output/Run_${RUNTIME:turn_id}/report_{domain}.md")
    assert report.args["mode"] == "fail_if_exists"
    assert sheet.tool == "create_files_spreadsheet"
    assert sheet.args["path"] == (
        "Documenti/Output/Run_${RUNTIME:turn_id}/data.xlsx")
    assert "mode" not in sheet.args

    snapshot = out.to_dict()
    again = _enforce_create_only_artifact_policy(out, query, catalog)
    assert again.to_dict() == snapshot


def test_create_only_policy_noops_without_explicit_constraint():
    fw = _framework(
        ("create_dirs", {"paths": ["Documenti/Output"]}),
        ("write_files", {"path": "Documenti/Output/report.md",
                         "content": "report", "mode": "overwrite"}),
    )
    before = fw.to_dict()
    out = _enforce_create_only_artifact_policy(
        fw, "Crea il rapporto.", [SimpleNamespace(name="write_files")])
    assert out.to_dict() == before


def test_tabular_sink_schema_propagates_through_entry_transforms():
    fw = _framework(
        ("read_source", {}),
        ("extract_entries", {"from_step": 1,
                             "fields": ["url", "title"]}),
        ("filter_entries", {"from_step": 2, "where_field": "status",
                            "where_not_in": ["timeout"]}),
        ("group_entries", {"from_step": 3, "dedup_key": "url"}),
        ("create_files_spreadsheet", {
            "from_step": 4,
            "columns": ["origin", "final_url", "title", "confidence"],
        }),
    )
    out = _propagate_sink_schema_to_extract(fw)
    assert out.steps[1].args["fields"] == [
        "url", "title", "origin", "final_url", "confidence"]
    snapshot = out.to_dict()
    assert _propagate_sink_schema_to_extract(out).to_dict() == snapshot


def test_grouped_report_virtual_key_is_resolved_from_declared_schema():
    fw = _framework(
        ("read_source", {}),
        ("extract_entries", {"from_step": 1,
                             "fields": ["url", "domain", "title"]}),
        ("write_files", {
            "from_step": 2,
            "path_template": "/tmp/report_{group_key}.txt",
            "content_template": (
                "Dominio: ${group_key}\n${entries.*.url} — "
                "${entries.*.title}"),
        }),
    )
    query = "Raggruppa i risultati per dominio e crea un rapporto."
    out = _normalize_grouped_artifact_templates(fw, query)
    writer = out.steps[2].args
    assert writer["path_template"] == "/tmp/report_{domain}.txt"
    assert writer["content_template"] == (
        "Dominio: ${entry.domain}\n${entry.entries.*.url} — "
        "${entry.entries.*.title}")
    snapshot = out.to_dict()
    assert _normalize_grouped_artifact_templates(out, query).to_dict() == snapshot


def test_grouped_report_virtual_key_is_not_guessed_without_schema_proof():
    fw = _framework(
        ("extract_entries", {"fields": ["url", "title"]}),
        ("write_files", {"path_template": "/tmp/{group_key}.txt"}),
    )
    before = fw.to_dict()
    out = _normalize_grouped_artifact_templates(
        fw, "Raggruppa i risultati per dominio.")
    assert out.to_dict() == before


def test_artifact_receipt_requires_matching_sink_categories(tmp_path):
    import time
    import agent_runtime
    from agent_runtime import (
        StepLog, TurnLog, _detect_unbacked_artifact_claim,
    )

    directory = StepLog(step_num=1, chosen_tool="create_dirs",
                        result={"ok": True, "ok_count": 1,
                                "results": [{"path": "/tmp/out"}]})
    report = StepLog(step_num=2, chosen_tool="write_files",
                     result={"ok": True, "ok_count": 1,
                             "results": [{"path": "/tmp/out/report.md"}]})
    message = "Rapporto e foglio di calcolo salvati nella nuova cartella."
    assert _detect_unbacked_artifact_claim(message, [directory]) == {
        "document", "spreadsheet"}
    assert _detect_unbacked_artifact_claim(message, [directory, report]) == {
        "spreadsheet"}

    original_dir = agent_runtime.TURN_LOG_DIR
    agent_runtime.TURN_LOG_DIR = tmp_path
    try:
        log = TurnLog(
            ts_start=time.time(), ts_end=time.time(), user_query="q",
            turn_id="artifact-honesty", actor="artifact-test", channel="test",
        )
        log.steps = [directory]
        log.final_kind = "answer"
        log.final_message = message
        log.write()
        assert log.false_success_detected is True
        assert "salvati" not in log.final_message
        assert "write/create artifacts" in log.final_message
    finally:
        agent_runtime.TURN_LOG_DIR = original_dir


def test_read_only_listing_is_not_an_unfulfilled_artifact_claim(tmp_path):
    """Elencare contenuti non e' promettere di averli creati.

    Caso vivo (E2E 29/7, `elenca i file in /opt/metnos/decisions`): la cartella
    contiene documenti con «creazione» nel titolo, e l'espressione regolare
    della guardia leggeva quelle parole come una promessa dell'assistente. La
    risposta corretta veniva SOSTITUITA da «l'azione write/create artifacts non
    e' stata completata». Un turno di sola lettura non puo' avere un artefatto
    mancante: non ne ha chiesto nessuno.
    """
    import time
    import agent_runtime
    from agent_runtime import StepLog, TurnLog

    listing = StepLog(step_num=1, chosen_tool="list_dirs",
                      result={"ok": True, "entries": [
                          {"name": "0156-naming-descriptor.md"},
                          {"name": "0177-engine-architecture-review.md"}]})
    message = ("Nella cartella ci sono i documenti sulla creazione degli "
               "executor e il report di revisione del motore.")

    original_dir = agent_runtime.TURN_LOG_DIR
    agent_runtime.TURN_LOG_DIR = tmp_path
    try:
        log = TurnLog(
            ts_start=time.time(), ts_end=time.time(),
            user_query="elenca i file in /opt/metnos/decisions",
            turn_id="artifact-readonly", actor="artifact-test",
            channel="test",
        )
        log.steps = [listing]
        log.final_kind = "answer"
        log.final_message = message
        log.write()
        assert log.false_success_detected is False
        assert log.final_message == message
    finally:
        agent_runtime.TURN_LOG_DIR = original_dir
