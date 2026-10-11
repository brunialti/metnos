# SPDX-License-Identifier: MIT
"""Google Sheets create adapter using the existing OAuth skill client.

The create request contains typed cells; no USER_ENTERED parsing, inferred
formulas or empty placeholder spreadsheet. The skill verifies typed readback.
Mutating creation is never automatically retried.
"""
from __future__ import annotations

from datetime import date, datetime
from contextlib import suppress
import json
from pathlib import Path
import tempfile

from tabular.model import Cell, TableError, Workbook


def _google_cell(cell: Cell, currency: str = "") -> dict:
    if cell.type == "blank":
        return {}
    if cell.type == "boolean":
        return {"userEnteredValue": {"boolValue": cell.value}}
    if cell.type in {"number", "currency"}:
        out = {"userEnteredValue": {"numberValue": cell.value}}
        if cell.type == "currency":
            out["userEnteredFormat"] = {"numberFormat": {
                "type": "NUMBER", "pattern": (f'"{currency}" ' if currency else "") + "#,##0.00"}}
        return out
    if cell.type in {"date", "datetime"}:
        epoch = datetime(1899, 12, 30)
        value = (datetime.combine(date.fromisoformat(cell.value), datetime.min.time())
                 if cell.type == "date" else datetime.fromisoformat(cell.value))
        serial = (value - epoch).total_seconds() / 86400
        return {"userEnteredValue": {"numberValue": serial}, "userEnteredFormat": {"numberFormat": {
            "type": "DATE" if cell.type == "date" else "DATE_TIME",
            "pattern": "yyyy-mm-dd" if cell.type == "date" else "yyyy-mm-dd hh:mm:ss"}}}
    # Includes headers and Note text. stringValue never grants formula authority.
    return {"userEnteredValue": {"stringValue": cell.value}}


def create_body(workbook: Workbook) -> dict:
    sheets = []
    for index, sheet in enumerate(workbook.sheets):
        rows = ([tuple(Cell("text", col.header) for col in sheet.columns)]
                if sheet.has_header and sheet.columns else []) + list(sheet.rows)
        sheets.append({"properties": {"sheetId": index, "title": sheet.name,
                                      "gridProperties": {"rowCount": max(1, len(rows)),
                                                         "columnCount": max(1, len(sheet.columns)),
                                                         "frozenRowCount": int(sheet.has_header and bool(sheet.columns))}},
                       "data": [{"startRow": 0, "startColumn": 0, "rowData": [
                           {"values": [_google_cell(cell, sheet.columns[position].currency)
                                       for position, cell in enumerate(row)]} for row in rows]}]})
    return {"properties": {"title": workbook.title}, "sheets": sheets}


def write(workbook: Workbook, destination: dict, mode: str) -> dict:
    if mode != "create":
        raise TableError("google_mode_not_supported")
    workbook = Workbook.from_dict(workbook.to_dict())
    from backends._google_auth_common import ensure_fresh_token, auth_needs_inputs
    from backends._google_api_runner import run_with_retry
    args_base = {"client": "google_workspace", "title": workbook.title}
    if not ensure_fresh_token():
        return auth_needs_inputs(args_base, executor="create_files_spreadsheet", result_kind="results")
    # A private temporary file avoids OS argv size limits and exposing source
    # cells in process listings. Existing skill/OAuth transport owns networking.
    temporary = tempfile.TemporaryDirectory(prefix="metnos-table-")
    try:
        payload = Path(temporary.name) / "create.json"
        payload.write_text(json.dumps(create_body(workbook), ensure_ascii=False, allow_nan=False), encoding="utf-8")
        payload.chmod(0o600)
        data, error = run_with_retry(
            ["sheets", "create-table", "--body-file", str(payload)],
            executor="create_files_spreadsheet", args_base=args_base,
            auth_handler=lambda args: auth_needs_inputs(args, executor="create_files_spreadsheet", result_kind="results"),
            max_retries=0,
        )
    finally:
        # Cleanup failure must never erase a known created ID or its undo.
        with suppress(OSError):
            temporary.cleanup()
    if error is not None:
        if error.get("decision") == "needs_inputs":
            return {**error, "results": [], "used": 0, "n_created": 0, "outcome_unknown": False}
        # Provider stderr may quote source cells. Preserve only a stable class,
        # never the shared runner's raw error/detail or arbitrary reason text.
        return {"ok": False, "error_code": "ERR_TABLE_WRITE", "error_reason": "google_create_failed",
                "results": [], "used": 0, "n_created": 0, "outcome_unknown": True}
    if not isinstance(data, dict) or not isinstance(data.get("spreadsheetId"), str) or not data["spreadsheetId"]:
        return {"ok": False, "error_code": "ERR_TABLE_WRITE", "error_reason": "google_missing_id",
                "results": [], "used": 0, "n_created": 0, "outcome_unknown": True}
    sid = data["spreadsheetId"]
    expected_cells = sum((len(sheet.rows) + int(sheet.has_header)) * len(sheet.columns) for sheet in workbook.sheets)
    verified = data.get("verified") is True and data.get("verified_cells") == expected_cells
    row = {"ok": verified, "created": True, "spreadsheet_id": sid, "file_id": sid, "id": sid,
           "title": workbook.title, "kind": "spreadsheet", "verified": verified,
           "updated_cells": data.get("verified_cells", 0),
           "web_view_url": data.get("spreadsheetUrl", "")}
    out = {"ok": verified, "results": [row], "used": 1, "n_created": 1,
           "n_written": int(verified), "spreadsheet_id": sid, "web_view_url": row["web_view_url"],
           "files_source": "google_workspace", "verified": verified,
           "_undo": {"reverse_pattern": "delete_files_by_id", "ids": [sid],
                     "scope": {"client": "google_workspace"}}}
    if not verified:
        out.update(partial=True, partial_reason="google_readback_failed", error_code="ERR_TABLE_WRITE",
                   error_reason="google_readback_mismatch")
    return out
