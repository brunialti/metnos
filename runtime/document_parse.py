"""Document interpretation from bounded, already-authorized bytes only.

This module owns no path or file authority. Complex formats must be interpreted
in a resource-limited worker when the caller is a long-lived service.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET

import pdf_extract

MAX_BYTES = pdf_extract.DEFAULT_MAX_BYTES
MAX_CHARS = pdf_extract.DEFAULT_TRIM_CHARS
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4096
MAX_CONTENT_JSON_BYTES = 1024 * 1024


def _decode_document_text(raw):
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _name(tag):
    return tag.rsplit("}", 1)[-1]


class _Text:
    def __init__(self, limit):
        self.limit, self.size, self.pieces, self.truncated = limit, 0, [], False

    def add(self, value):
        remaining = self.limit - self.size
        self.pieces.append(value[:remaining])
        self.size += min(len(value), remaining)
        self.truncated |= len(value) > remaining
        return not self.truncated

    def value(self):
        return "".join(self.pieces)


def _archive(raw):
    archive = zipfile.ZipFile(io.BytesIO(raw))
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS or any(len(i.filename) > 1024 for i in members):
        archive.close()
        raise ValueError("document_archive_limit")
    return archive


def _xml(archive, name, budget):
    info = archive.getinfo(name)
    if info.file_size > budget[0]:
        raise ValueError("document_xml_bytes_limit")
    with archive.open(info) as member:
        raw = member.read(budget[0] + 1)
    if len(raw) > budget[0]:
        raise ValueError("document_xml_bytes_limit")
    budget[0] -= len(raw)
    # OOXML has no DTD. Refuse expansion before ElementTree can construct it;
    # the compact view also detects declarations in UTF-16/32 XML documents.
    declarations = raw.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise ValueError("document_xml_declaration_unsupported")
    return ET.fromstring(raw)


def _docx(raw, limit):
    with _archive(raw) as archive:
        root = _xml(archive, "word/document.xml", [MAX_XML_BYTES])
    out = _Text(limit)
    for node in root.iter():
        name = _name(node.tag)
        if name in {"t", "tab", "br"}:
            if not out.add("\t" if name == "tab" else "\n" if name == "br" else (node.text or "")):
                break
        elif name == "p" and out.size and not out.add("\n"):
            break
    return out.value().strip(), out.truncated


def _cell(cell, shared):
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(n.text or "" for n in cell.iter() if _name(n.tag) == "t")
    value = next((n.text or "" for n in cell if _name(n.tag) == "v"), "")
    if kind == "s" and value:
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return value
    return ("TRUE" if value == "1" else "FALSE") if kind == "b" else value


def _xlsx(raw, limit):
    out, budget = _Text(limit), [MAX_XML_BYTES]
    with _archive(raw) as archive:
        names = set(archive.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = _xml(archive, "xl/sharedStrings.xml", budget)
            shared = ["".join(n.text or "" for n in item.iter() if _name(n.tag) == "t")
                      for item in root.iter() if _name(item.tag) == "si"]
        sheets = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        for number, name in enumerate(sheets, 1):
            root = _xml(archive, name, budget)
            if not out.add(("\n\n" if number > 1 else "") + f"[Sheet {number}]\n"):
                break
            for row in root.iter():
                if _name(row.tag) != "row":
                    continue
                first = True
                for cell in row:
                    if _name(cell.tag) != "c":
                        continue
                    if not out.add(("" if first else "\t") + _cell(cell, shared)):
                        break
                    first = False
                if out.truncated or not out.add("\n"):
                    break
            if out.truncated:
                break
    return out.value().strip(), out.truncated


def parse_bytes(raw, *, suffix, file_size=None, max_bytes=MAX_BYTES, max_chars=MAX_CHARS):
    """Return content and explicit completeness without opening a supplied path."""
    if (not isinstance(raw, bytes) or type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES
            or type(max_chars) is not int or not 0 < max_chars <= MAX_CHARS
            or not isinstance(suffix, str) or len(suffix) > 32):
        raise ValueError("document_input_invalid")
    size = len(raw) if file_size is None else file_size
    if type(size) is not int or size < len(raw):
        raise ValueError("document_input_invalid")
    suffix = suffix.casefold()
    complete = len(raw) == size and len(raw) <= max_bytes
    signature = hashlib.sha256(raw).hexdigest() if complete else ""
    entry = {"ok": True, "file_type": suffix.lstrip(".") or "text", "file_size": size,
             "content": "", "readable": False, "signature": signature, "sha256": signature,
             "content_sha256": "", "input_complete": complete, "parse_complete": False,
             "truncated": False, "limits": []}
    if not complete:
        state = pdf_extract.pdf_input_limit(max_bytes, available_total=size)
        if suffix != ".pdf":
            state["error"] = "document_input_bytes_limit"
        entry.update(state)
        entry["parse_diagnostic"] = state["error"]
        return entry
    content, truncated, content_limits = "", False, []
    try:
        if suffix == ".docx":
            content, truncated = _docx(raw, max_chars)
        elif suffix == ".xlsx":
            content, truncated = _xlsx(raw, max_chars)
        elif suffix == ".pdf":
            info = pdf_extract.extract_pdf_text(raw, max_bytes=max_bytes, trim_chars=max_chars)
            content = info["body_text"]
            entry.update(pdf_extract.pdf_extraction_state(info))
            entry["pdf_metadata"] = {key: info[key] for key in
                                     ("title", "author", "n_pages", "n_pages_read", "used_lib")}
        elif suffix == ".xls":
            entry["parse_diagnostic"] = "legacy_xls_not_supported_without_converter"
            return entry
        else:
            decoded = _decode_document_text(raw)
            content, truncated = decoded[:max_chars], len(decoded) > max_chars
        if truncated:
            content_limits.append({"cap_field": "trim_chars", "cap_value": max_chars,
                                   "used": len(content)})
        # JSON's ASCII escaping can cost twelve bytes per Unicode character.
        # Leave space for the operation envelope inside the 2 MiB IPC frame.
        if len(json.dumps(content, ensure_ascii=True)) > MAX_CONTENT_JSON_BYTES:
            low, high = 0, len(content)
            while low < high:
                mid = (low + high + 1) // 2
                if len(json.dumps(content[:mid], ensure_ascii=True)) <= MAX_CONTENT_JSON_BYTES:
                    low = mid
                else:
                    high = mid - 1
            content, truncated = content[:low], True
            content_limits.append({"cap_field": "content_json_bytes", "cap_value": MAX_CONTENT_JSON_BYTES,
                                   "used": len(json.dumps(content, ensure_ascii=True))})
        for key in ("title", "author"):
            if key in entry.get("pdf_metadata", {}):
                original = str(entry["pdf_metadata"][key])
                entry["pdf_metadata"][key] = original[:4096]
                if len(original) > 4096:
                    entry["parse_complete"] = False
                    entry.update(pdf_extract.pdf_limit_state(entry["limits"] + [{
                        "cap_field": "metadata_chars", "cap_value": 4096, "used": 4096,
                        "available_total": len(original)}]))
        entry["content"] = content
        entry["readable"] = bool(content.strip()) if suffix == ".pdf" else True
        if suffix == ".pdf" and not entry["readable"]:
            entry["parse_diagnostic"] = "pdf_text_unavailable"
        if suffix != ".pdf":
            entry["parse_complete"] = not truncated
        if truncated:
            entry["parse_complete"] = False
            entry.update(pdf_extract.pdf_limit_state(entry["limits"] + content_limits))
        if not entry["truncated"]:
            normalized = re.sub(r"\s+", " ", content).strip().casefold()
            entry["content_sha256"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""
    except ImportError:
        entry["parse_diagnostic"] = "pdf_parser_unavailable" if suffix == ".pdf" else "document_parser_unavailable"
    except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError, UnicodeError, OSError):
        entry["parse_diagnostic"] = "document_content_invalid"
    return entry


def merge_entry(parsed, source=None):
    """Preserve caller annotations, replacing all evidence from an earlier read."""
    entry = dict(source or {})
    for key in ("parse_diagnostic", "cap_field", "cap_value", "used", "available_total",
                "truncated_what", "error", "error_class", "error_code", "pdf_metadata",
                "extraction_complete", "n_pages", "n_pages_read", "used_lib"):
        entry.pop(key, None)
    entry.update(parsed)
    return entry


def summarize(entries, *, deduplicate=False):
    """Aggregate automatic-read metadata without treating incomplete text as equal."""
    original_count = len(entries)
    unique, duplicates, signatures, contents = [], [], {}, {}
    for entry in entries:
        raw_key, content_key = entry.get("sha256"), entry.get("content_sha256")
        previous = (signatures.get(raw_key) if raw_key else None) or (contents.get(content_key) if content_key else None)
        if deduplicate and previous is not None:
            duplicates.append({"path": entry["path"], "duplicate_of": previous["path"],
                               "signature": raw_key or "", "content_sha256": content_key or ""})
            previous.setdefault("duplicate_paths", []).append(entry["path"])
            previous["duplicate_count"] = len(previous["duplicate_paths"])
            continue
        unique.append(entry)
        if raw_key:
            signatures[raw_key] = entry
        if content_key:
            contents[content_key] = entry
    result = {"entries": unique, "duplicates": duplicates, "deduped_count": len(duplicates),
              "input_ok_count": original_count, "ok_count": len(unique),
              "readable_count": sum(bool(e.get("readable")) for e in unique),
              "unreadable_count": sum(not e.get("readable") for e in unique)}
    return pdf_extract.aggregate_pdf_limits(result, unique)
