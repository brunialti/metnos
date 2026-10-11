# SPDX-License-Identifier: MIT
"""Create a staged XLSX using only the standard library and the table model.

This is a physical writer, with no projection, parsing, publication or formula
authority. ISO date cells avoid serial-date arithmetic. Every written row,
header, type and Note marker is read back before the caller may publish it.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from tabular.model import CAPABILITIES, Cell, Sheet, TableError, Workbook

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"
_XML = "http://www.w3.org/XML/1998/namespace"
_VML = "urn:schemas-microsoft-com:vml"
_OFFICE = "urn:schemas-microsoft-com:office:office"
_EXCEL = "urn:schemas-microsoft-com:office:excel"
_NOTE_MARKER = "metnos.tabular.notes.v1"


def _q(name: str, namespace: str = _MAIN) -> str:
    return f"{{{namespace}}}{name}"


def _xml(node: ET.Element) -> bytes:
    # XML parsers normalize literal CR to LF. Character references preserve
    # a user's exact CR, including CRLF, in inline text and comment text.
    return ET.tostring(node, encoding="utf-8", xml_declaration=True).replace(b"\r", b"&#13;")


def _valid_text(value: str) -> None:
    if any(not (code in {9, 10, 13} or 0x20 <= code <= 0xD7FF
                or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF)
           for code in map(ord, value)):
        raise TableError("xlsx_invalid_xml_character")


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _rows(sheet: Sheet):
    if sheet.has_header and sheet.columns:
        yield tuple(Cell("text", column.header) for column in sheet.columns)
    yield from sheet.rows


def _number_format(cell: Cell, currency: str) -> str:
    if cell.type == "date":
        return "yyyy-mm-dd"
    if cell.type == "datetime":
        return "yyyy-mm-dd hh:mm:ss"
    if cell.type == "currency":
        return (f'"{currency}" ' if currency else "") + "#,##0.00"
    return "General"


def _encoded(cell: Cell) -> tuple[str, str | None]:
    if cell.type == "blank":
        return "n", None
    if cell.type in {"text", "url"}:
        return "inlineStr", cell.value
    if cell.type == "boolean":
        return "b", "1" if cell.value else "0"
    if cell.type in {"number", "currency"}:
        return "n", str(cell.value)
    if cell.type == "datetime":
        native = datetime.fromisoformat(cell.value)
        return "d", native.isoformat(timespec="milliseconds" if native.microsecond else "seconds")
    if cell.type == "date":
        return "d", cell.value
    raise TableError("invalid_cell_type")


def _validate(workbook: Workbook) -> int:
    capabilities = CAPABILITIES["local_xlsx"]
    _valid_text(workbook.title)
    written = 0
    for sheet in workbook.sheets:
        _valid_text(sheet.name)
        if len(sheet.columns) > capabilities.max_columns:
            raise TableError("xlsx_column_limit")
        header_rows = int(sheet.has_header and bool(sheet.columns))
        if len(sheet.rows) + header_rows > capabilities.max_rows:
            raise TableError("xlsx_row_limit")
        if sheet.rows and not sheet.columns:
            raise TableError("empty_rows_without_schema")
        for column in sheet.columns:
            _valid_text(column.header)
        for row in _rows(sheet):
            written += len(row)
            for cell in row:
                if isinstance(cell.value, str):
                    _valid_text(cell.value)
                    if len(cell.value) > capabilities.max_cell_chars:
                        raise TableError("xlsx_cell_character_limit")
    if written > capabilities.max_cells:
        raise TableError("xlsx_cell_limit")
    return written


def _formats(workbook: Workbook) -> tuple[str, ...]:
    formats = ["General"]
    for sheet in workbook.sheets:
        for row in sheet.rows:
            for column, cell in zip(sheet.columns, row):
                value = _number_format(cell, column.currency)
                if value not in formats:
                    formats.append(value)
    return tuple(formats)


def _styles(formats: tuple[str, ...]) -> ET.Element:
    root = ET.Element(_q("styleSheet"))
    custom = ET.SubElement(root, _q("numFmts"), count=str(len(formats) - 1))
    for index, value in enumerate(formats[1:], 164):
        ET.SubElement(custom, _q("numFmt"), numFmtId=str(index), formatCode=value)
    fonts = ET.SubElement(root, _q("fonts"), count="1")
    font = ET.SubElement(fonts, _q("font"))
    ET.SubElement(font, _q("sz"), val="11")
    ET.SubElement(font, _q("name"), val="Calibri")
    fills = ET.SubElement(root, _q("fills"), count="2")
    for pattern in ("none", "gray125"):
        ET.SubElement(ET.SubElement(fills, _q("fill")), _q("patternFill"), patternType=pattern)
    border = ET.SubElement(ET.SubElement(root, _q("borders"), count="1"), _q("border"))
    for edge in ("left", "right", "top", "bottom", "diagonal"):
        ET.SubElement(border, _q(edge))
    base = {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"}
    ET.SubElement(ET.SubElement(root, _q("cellStyleXfs"), count="1"), _q("xf"), base)
    cell_xfs = ET.SubElement(root, _q("cellXfs"), count=str(len(formats)))
    for index in range(len(formats)):
        attributes = {**base, "numFmtId": str(163 + index if index else 0), "xfId": "0"}
        if index:
            attributes["applyNumberFormat"] = "1"
        ET.SubElement(cell_xfs, _q("xf"), attributes)
    ET.SubElement(ET.SubElement(root, _q("cellStyles"), count="1"), _q("cellStyle"),
                  name="Normal", xfId="0", builtinId="0")
    return root


def _worksheet(sheet: Sheet, formats: tuple[str, ...]) -> ET.Element:
    root = ET.Element(_q("worksheet"))
    has_header = bool(sheet.has_header and sheet.columns)
    row_count = len(sheet.rows) + int(has_header)
    if sheet.columns and row_count:
        last = f"{_column_name(len(sheet.columns))}{row_count}"
        ET.SubElement(root, _q("dimension"), ref=f"A1:{last}")
    view = ET.SubElement(ET.SubElement(root, _q("sheetViews")), _q("sheetView"), workbookViewId="0")
    if has_header:
        ET.SubElement(view, _q("pane"), ySplit="1", topLeftCell="A2", activePane="bottomLeft", state="frozen")
        ET.SubElement(view, _q("selection"), pane="bottomLeft", activeCell="A2", sqref="A2")
    data = ET.SubElement(root, _q("sheetData"))
    for row_index, values in enumerate(_rows(sheet), 1):
        row = ET.SubElement(data, _q("row"), r=str(row_index))
        for position, cell in enumerate(values, 1):
            kind, value = _encoded(cell)
            style = formats.index(_number_format(cell, sheet.columns[position - 1].currency))
            element = ET.SubElement(row, _q("c"), r=f"{_column_name(position)}{row_index}", t=kind, s=str(style))
            if kind == "inlineStr":
                text = ET.SubElement(ET.SubElement(element, _q("is")), _q("t"), {_q("space", _XML): "preserve"})
                text.text = value
            elif value is not None:
                ET.SubElement(element, _q("v")).text = value
    if has_header and sheet.rows:
        ET.SubElement(root, _q("autoFilter"), ref=f"A1:{_column_name(len(sheet.columns))}{row_count}")
    if sheet.notes:
        ET.SubElement(root, _q("legacyDrawing"), {_q("id", _DOC_REL): "rIdVml"})
    return root


def _relationship(root: ET.Element, identifier: str, kind: str, target: str) -> None:
    ET.SubElement(root, _q("Relationship", _PKG_REL), Id=identifier,
                  Type=f"{_DOC_REL}/{kind}", Target=target)


def _note_parts() -> tuple[ET.Element, ET.Element, ET.Element]:
    comments = ET.Element(_q("comments"))
    ET.SubElement(ET.SubElement(comments, _q("authors")), _q("author")).text = "Metnos"
    comment = ET.SubElement(ET.SubElement(comments, _q("commentList")), _q("comment"), ref="A1", authorId="0")
    ET.SubElement(ET.SubElement(comment, _q("text")), _q("t")).text = _NOTE_MARKER
    vml = ET.Element("xml")
    layout = ET.SubElement(vml, _q("shapelayout", _OFFICE), {_q("ext", _VML): "edit"})
    ET.SubElement(layout, _q("idmap", _OFFICE), {_q("ext", _VML): "edit", "data": "1"})
    shape_type = ET.SubElement(vml, _q("shapetype", _VML),
                               {"id": "_x0000_t202", "coordsize": "21600,21600",
                                _q("spt", _OFFICE): "202", "path": "m,l,21600r21600,l21600,xe"})
    ET.SubElement(shape_type, _q("stroke", _VML), joinstyle="miter")
    ET.SubElement(shape_type, _q("path", _VML), {"gradientshapeok": "t", _q("connecttype", _OFFICE): "rect"})
    shape = ET.SubElement(vml, _q("shape", _VML),
                          {"id": "_x0000_s1026", "type": "#_x0000_t202", "fillcolor": "#ffffe1",
                           _q("insetmode", _OFFICE): "auto",
                           "style": "position:absolute;margin-left:59.25pt;margin-top:1.5pt;width:144px;height:79px;z-index:1;visibility:hidden"})
    ET.SubElement(shape, _q("fill", _VML), color2="#ffffe1")
    ET.SubElement(shape, _q("shadow", _VML), color="black", obscured="t")
    ET.SubElement(shape, _q("path", _VML), {_q("connecttype", _OFFICE): "none"})
    ET.SubElement(ET.SubElement(shape, _q("textbox", _VML), style="mso-direction-alt:auto"), "div", style="text-align:left")
    client = ET.SubElement(shape, _q("ClientData", _EXCEL), ObjectType="Note")
    for name, value in (("MoveWithCells", None), ("SizeWithCells", None), ("AutoFill", "False"), ("Row", "0"), ("Column", "0")):
        ET.SubElement(client, _q(name, _EXCEL)).text = value
    relationships = ET.Element(_q("Relationships", _PKG_REL))
    return comments, vml, relationships


def _package(workbook: Workbook, formats: tuple[str, ...]):
    content = ET.Element(_q("Types", _CONTENT))
    for extension, media in (("rels", "application/vnd.openxmlformats-package.relationships+xml"),
                             ("xml", "application/xml")):
        ET.SubElement(content, _q("Default", _CONTENT), Extension=extension, ContentType=media)
    if any(sheet.notes for sheet in workbook.sheets):
        ET.SubElement(content, _q("Default", _CONTENT), Extension="vml", ContentType="application/vnd.openxmlformats-officedocument.vmlDrawing")
    overrides = [("/xl/workbook.xml", "sheet.main"), ("/xl/styles.xml", "styles")]
    root_rels = ET.Element(_q("Relationships", _PKG_REL))
    _relationship(root_rels, "rId1", "officeDocument", "xl/workbook.xml")
    book = ET.Element(_q("workbook"))
    views = ET.SubElement(book, _q("bookViews"))
    ET.SubElement(views, _q("workbookView"), activeTab="0")
    sheets = ET.SubElement(book, _q("sheets"))
    rels = ET.Element(_q("Relationships", _PKG_REL))
    for index, sheet in enumerate(workbook.sheets, 1):
        ET.SubElement(sheets, _q("sheet"), {"name": sheet.name, "sheetId": str(index), _q("id", _DOC_REL): f"rId{index}"})
        _relationship(rels, f"rId{index}", "worksheet", f"worksheets/sheet{index}.xml")
        overrides.append((f"/xl/worksheets/sheet{index}.xml", "worksheet"))
        if sheet.notes:
            overrides.append((f"/xl/comments/comment{index}.xml", "comments"))
    _relationship(rels, f"rId{len(workbook.sheets) + 1}", "styles", "styles.xml")
    for path, kind in overrides:
        ET.SubElement(content, _q("Override", _CONTENT), PartName=path,
                      ContentType=f"application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml")
    yield "[Content_Types].xml", content
    yield "_rels/.rels", root_rels
    yield "xl/workbook.xml", book
    yield "xl/_rels/workbook.xml.rels", rels
    yield "xl/styles.xml", _styles(formats)
    for index, sheet in enumerate(workbook.sheets, 1):
        yield f"xl/worksheets/sheet{index}.xml", _worksheet(sheet, formats)
        if sheet.notes:
            comments, vml, note_rels = _note_parts()
            _relationship(note_rels, "rIdComments", "comments", f"../comments/comment{index}.xml")
            _relationship(note_rels, "rIdVml", "vmlDrawing", f"../drawings/commentsDrawing{index}.vml")
            yield f"xl/comments/comment{index}.xml", comments
            yield f"xl/drawings/commentsDrawing{index}.vml", vml
            yield f"xl/worksheets/_rels/sheet{index}.xml.rels", note_rels


def _check(condition: bool) -> None:
    if not condition:
        raise TableError("xlsx_readback_mismatch")


def _readback(staged: Path, workbook: Workbook, formats: tuple[str, ...], members: set[str]) -> None:
    with ZipFile(staged, "r") as archive:
        _check(len(archive.namelist()) == len(members) and set(archive.namelist()) == members)
        # Parse and CRC-check every part, including package/Note relationships.
        # Comparing structural trees avoids depending on namespace prefixes.
        for path, expected in _package(workbook, formats):
            actual = ET.fromstring(archive.read(path))
            _check(_tree(actual) == _tree(expected))
            _check(not any(node.tag == _q("f") for node in actual.iter()))


def _tree(element: ET.Element):
    return element.tag, dict(element.attrib), element.text or "", tuple(_tree(child) for child in element)


def stage_xlsx_create(staged: Path, workbook: Workbook) -> int:
    """Write and verify only ``staged``; publication belongs to the caller.

    Cell/row/header equality is strict, including empty inline text versus a
    blank cell. A failure leaves at most the private staged file for cleanup.
    Existing destination files are never opened by this function.
    """
    # Revalidate even a Workbook instance altered through object.__setattr__.
    workbook = Workbook.from_dict(workbook.to_dict())
    written = _validate(workbook)
    formats = _formats(workbook)
    members = set()
    with ZipFile(staged, "w", compression=ZIP_DEFLATED) as archive:
        for path, element in _package(workbook, formats):
            archive.writestr(path, _xml(element))
            members.add(path)
    _readback(staged, workbook, formats, members)
    return written
