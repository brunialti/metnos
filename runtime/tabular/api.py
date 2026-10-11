# SPDX-License-Identifier: MIT
"""In-process table API shared by executor backends.

Callers retain their existing file/OAuth consent boundary. This is not a new
executor or a route that bypasses authorization. Existing signed executors
are adopted separately, after registered parity tests and package delivery.
"""
from __future__ import annotations

from dataclasses import replace

from tabular.compile import compile_workbook, _source_is_partial
from tabular.model import CAPABILITIES, Cell, Column, Sheet, TableError
from tabular_projection import TabularProjectionError


def _message(code: str, **parameters) -> str:
    from messages import get
    text = get(code, **parameters)
    if text.startswith("<missing:"):
        raise TableError("i18n_key_unavailable")
    return text


def _notice_message(notice: dict) -> str:
    defaults = {"sheet": "", "column": "", "count": 0, "detail": "",
                "truncated_what": "", "used": 0, "available_total": "",
                "cap_field": "", "cap_value": ""}
    parameters = defaults | {key: value for key, value in notice.items()
                             if key not in {"code", "message", "reason_code"}}
    if notice["code"] == "WARN_TABLE_PARTIAL_SOURCE" and "reason_code" in notice:
        # The navigation reason registry is shared with the sites presenter;
        # neither the sheet nor the row payload selects a translation key.
        from sites_observed import REASON_MSG
        key = REASON_MSG.get(notice.get("reason_code"))
        parameters["detail"] = _message(key, **parameters) if key else ""
    return _message(notice["code"], **parameters)


def _notes(workbook, notices: list[dict]):
    if not notices:
        return workbook
    from tabular.compile import _sheet_name
    name = _sheet_name("Note", {sheet.name.casefold() for sheet in workbook.sheets})
    columns = (Column(_message("MSG_TABLE_NOTE_KIND"), "code"),
               Column(_message("MSG_TABLE_NOTE_SHEET"), "sheet"),
               Column(_message("MSG_TABLE_NOTE_DETAIL"), "detail"))
    rows = tuple((Cell("text", notice["code"]), Cell("text", notice.get("sheet", "")),
                  Cell("text", notice["message"] if "message" in notice else _notice_message(notice))) for notice in notices)
    return replace(workbook, sheets=workbook.sheets + (Sheet(name, columns, rows, notes=True),))


def _resolved_spec(source, spec):
    """Keep existing semantic projection at the runtime/lexicon boundary."""
    from tabular.compile import _clean, _sensitive_fields
    from tabular_projection import compile_projection
    spec = dict(spec or {})
    envelope = {"entries": source} if isinstance(source, list) else source
    if not isinstance(envelope, dict):
        return spec
    tables = envelope.get("sheets", [envelope])
    if not isinstance(tables, list):
        return spec
    table_specs = spec.get("sheets", [spec] * len(tables))
    if not isinstance(table_specs, list) or len(table_specs) != len(tables):
        return spec
    resolved = []
    for table, table_spec in zip(tables, table_specs):
        if not isinstance(table, dict) or not isinstance(table_spec, dict):
            raise TableError("invalid_sheet_source")
        item = dict(table_spec)
        rows = table.get("values", table.get("entries", []))
        if (isinstance(rows, list) and rows and all(isinstance(row, dict) for row in rows)
                and item.get("columns") and not item.get("column_specs")):
            sensitive = _sensitive_fields(envelope, spec, table, item)
            records = [_clean(row, sensitive) for row in rows]
            if any(not isinstance(row, dict) for row in records):
                raise TableError("sensitive_record_requested")
            projections = compile_projection(records, item["columns"], field_roles=item.get("field_roles"))
            item["column_specs"] = [{"header": col.header, "source": col.source,
                                     "transform": col.transform} for col in projections]
        resolved.append(item)
    if "sheets" in envelope:
        spec["sheets"] = resolved
        return spec
    return resolved[0] if resolved else spec


def write_table(source, spec: dict | None, destination: dict, mode: str = "create") -> dict:
    """Compile and write records/matrices with one explicit physical backend.

    ``destination.backend``: local_xlsx/csv/google_sheets (local is inferred
    from an explicit suffix). Device dispatch is rejected until a negotiated
    table-writer capability and verified package exist. Google supports create
    only; unsupported modes fail before OAuth, filesystem or network I/O.

    Type conversion is opt-in through column_specs.type. Existing native
    numbers/bools remain native by default; string values remain text. Numeric
    strings require an explicit locale (it/en) or canonical decimal grammar.
    No source row is deduplicated and no identity policy is added here.
    """
    try:
        if not isinstance(destination, dict):
            raise TableError("invalid_destination")
        destination = dict(destination)
        backend = destination.get("backend", "local_xlsx")
        if backend == "local":
            backend = "csv" if str(destination.get("path", "")).lower().endswith(".csv") else "local_xlsx"
        if backend == "device" or destination.get("device_id") or destination.get("target") == "device":
            raise TableError("device_table_writer_unverified")
        if backend not in CAPABILITIES:
            raise TableError("unsupported_backend")
        capabilities = CAPABILITIES[backend]
        if mode not in capabilities.modes:
            raise TableError("unsupported_backend_mode")
        destination["backend"] = backend
        resolved_spec = _resolved_spec(source, spec)
        table_specs = resolved_spec.get("sheets", [resolved_spec])
        needs_month_names = (isinstance(table_specs, list) and any(
            isinstance(table_spec, dict) and isinstance(table_spec.get("column_specs"), list)
            and any(isinstance(column, dict) and column.get("type") in {"date", "datetime"}
                    for column in table_spec["column_specs"])
            for table_spec in table_specs))
        months = {}
        if needs_month_names:
            try:
                import detection_lexicon_seed_parsers as parser_lexicon
            except ImportError:
                # The device package has the pure observed-date normalizer,
                # not the server's lexicon/DB infrastructure. Numeric dates
                # still parse; unrecognized dates stay text with a notice.
                parser_lexicon = None
            if parser_lexicon is not None:
                lexicon = parser_lexicon.load_family("time_resolver")
                if lexicon:
                    months = {str(form).casefold(): int(number)
                              for number, forms in lexicon["parser.time.month"].items() for form in forms}
        compiled = compile_workbook(source, resolved_spec, capabilities, month_names=months)
        notices = list(compiled.notices)
        # Localize every notice before publishing, so Note never silently lacks
        # the same warning/provenance returned to the conversation.
        for notice in notices:
            notice["message"] = _notice_message(notice)
        workbook = _notes(compiled.workbook, notices)
        if backend == "csv":
            from spreadsheet_cells import is_active_text
            def active_count(book):
                return (sum(is_active_text(column.header) for sheet in book.sheets if sheet.has_header for column in sheet.columns)
                        + sum(is_active_text(cell.value) for sheet in book.sheets for row in sheet.rows for cell in row))
            if active_count(workbook):
                prefix_notice = {"code": "WARN_TABLE_TEXT_PREFIX", "count": 0}
                notices.append(prefix_notice)
                # Include Note headers/cells, including the extra warning row.
                prefix_notice["count"] = active_count(_notes(compiled.workbook, notices))
                prefix_notice["message"] = _notice_message(prefix_notice)
                workbook = _notes(compiled.workbook, notices)
        # Note text can carry source metadata; it obeys the same physical cap.
        if any(len(cell.value) > capabilities.max_cell_chars
               for sheet in workbook.sheets if sheet.notes for row in sheet.rows
               for cell in row if isinstance(cell.value, str)):
            raise TableError("notes_exceed_cell_limit")
        if sum((len(sheet.rows) + int(sheet.has_header)) * len(sheet.columns) for sheet in workbook.sheets) > capabilities.max_cells:
            raise TableError("notes_exceed_workbook_limit")
        # All required messages must exist before any mutation. A missing
        # translation must never hide an already created artifact afterwards.
        success_hint = _message("MSG_TABLE_WRITTEN", title=workbook.title,
                                rows=compiled.data_rows, notices=len(notices))
        incomplete_hint = _message("WARN_TABLE_WRITE_INCOMPLETE", title=workbook.title,
                                   rows=compiled.data_rows, notices=len(notices))
        write_error = _message("ERR_TABLE_WRITE", reason="write_failed")
        if backend == "google_sheets":
            from backends.files.tabular_google import write
        else:
            from backends.files.tabular_local import write
        source_receipt = {"source_metadata": list(compiled.source_metadata),
                          "source_partial": any(_source_is_partial(item) for item in compiled.source_metadata),
                          "source_truncated": any(item.get("truncated") is True for item in compiled.source_metadata)}
        source_notices = [item["message"] for item in notices if item["code"] == "WARN_TABLE_PARTIAL_SOURCE"]
        result = write(workbook, destination, mode)
        # OAuth needs_inputs is not a written-file receipt.
        if result.get("decision") == "needs_inputs":
            return {**result, **source_receipt, "notices": notices, "data_rows": 0, "verified": False}
        verified_rows = (compiled.data_rows if result.get("verified") is True else
                         sum(row.get("data_rows", 0) for row in result.get("results", [])))
        result.update(notices=notices, data_rows=verified_rows,
                      compiled_rows=compiled.data_rows, available_total=compiled.available_total,
                      contract_version=workbook.version, backend=backend,
                      sheets=[{"name": sheet.name, "data_rows": len(sheet.rows), "notes": sheet.notes,
                               "has_header": sheet.has_header}
                              for sheet in workbook.sheets])
        result.update(source_receipt)
        result["writer_truncated"] = bool(compiled.truncations)
        result["truncated"] = result["writer_truncated"] or result["source_truncated"]
        if compiled.truncations:
            result["truncations"] = list(compiled.truncations)
            result.update({key: value for key, value in compiled.truncations[0].items()
                           if key in {"truncated_what", "cap_field", "cap_value"}})
        result["final_message_hint"] = success_hint if result.get("ok") and result.get("verified") else incomplete_hint
        if source_notices:
            result["partial"] = True
            # Keep any writer failure reason; source warnings already use the
            # same localized text that was written into the Note artifact.
            reasons = ([result["partial_reason"]] if result.get("partial_reason") else []) + source_notices
            result["partial_reason"] = "\n".join(reasons)
            result["final_message_hint"] += "\n" + "\n".join(source_notices)
        if not result.get("ok"):
            result["error"] = write_error
        return result
    except TabularProjectionError as exc:
        return {"ok": False, "error_code": "ERR_TABULAR_PROJECTION_AMBIGUOUS",
                "error": _message("ERR_TABULAR_PROJECTION_AMBIGUOUS", columns=", ".join(exc.unresolved), fields=", ".join(exc.fields)),
                "error_class": "invalid_args", "results": [], "used": 0, "data_rows": 0}
    except (TableError, ValueError, TypeError, OSError, ImportError) as exc:
        from messages import get
        reason = ("destination_exists" if isinstance(exc, FileExistsError) else
                  getattr(exc, "reason", "io_error" if isinstance(exc, OSError) else
                          "table_writer_unavailable" if isinstance(exc, ImportError) else "invalid_input"))
        return {"ok": False, "error_code": "ERR_TABLE_WRITE", "error": get("ERR_TABLE_WRITE", reason=reason),
                "error_reason": reason, "results": [], "used": 0, "data_rows": 0,
                "error_class": "conflict" if reason == "destination_exists" else
                               "invalid_args" if isinstance(exc, (ValueError, TypeError)) else "io_error",
                "verified": False}
