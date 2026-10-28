"""One bounded-input PDF text contract for native local and URL readers.

pypdf is imported lazily and is required when inspection is requested. Execute
inspection in the native process runner: compressed bytes and output limits do
not bound expanded PDF streams, parser CPU, or memory.
"""
from __future__ import annotations

import io
import gzip
from importlib.util import find_spec
import zlib

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_PAGES = 100
DEFAULT_TRIM_CHARS = 200_000


class PDFInspectionError(ValueError):
    """Internal diagnostic code; never includes arbitrary parser/file content."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def has_pypdf() -> bool:
    try:
        return find_spec("pypdf") is not None
    except (ImportError, AttributeError, ValueError):
        return False


def has_pdf_parser() -> bool:
    return has_pypdf()


def pdf_limit_state(limits: list[dict]) -> dict:
    """Project native cap notification fields without losing multiple caps."""
    state = {"truncated": bool(limits), "limits": [dict(cap) for cap in limits]}
    if limits:
        state.update(limits[0])
        state["truncated_what"] = ",".join(dict.fromkeys(cap["cap_field"] for cap in limits))
    return state


def pdf_input_limit(max_bytes: int, *, available_total: int | None = None) -> dict:
    cap = {"cap_field": "max_bytes", "cap_value": max_bytes, "used": max_bytes}
    if available_total is not None:
        cap["available_total"] = available_total
    return {"error": "pdf_input_bytes_limit", "error_class": "input_limit",
            "parse_complete": False, "input_complete": False,
            **pdf_limit_state([cap])}


def pdf_extraction_state(info: dict) -> dict:
    return {"parse_complete": info["extraction_complete"], "input_complete": True,
            "extraction_complete": info["extraction_complete"],
            "n_pages": info["n_pages"], "n_pages_read": info["n_pages_read"],
            "used_lib": info["used_lib"], **pdf_limit_state(info["limits"])}


def aggregate_pdf_limits(result: dict, items: list[dict]) -> dict:
    """Expose cap notifications at the operation boundary as well as each item."""
    limits = [cap for item in items for cap in item.get("limits", [])]
    if limits:
        result.update(pdf_limit_state(limits))
    return result


def read_pdf_response(response, max_bytes: int) -> tuple[bytes | None, dict | None]:
    """Read a complete HTTP PDF body with caps before and after decoding.

    This function grants no network authority and performs no PDF inspection.
    The caller owns the response, deadline and native process resource limits.
    """
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        return None, {"error": "pdf_limit_invalid", "error_class": "invalid_input"}
    encoding = (response.headers.get("Content-Encoding") or "identity").strip().lower()
    if encoding not in ("identity", "gzip", "deflate"):
        return None, {"error": "pdf_transport_encoding_unsupported", "error_class": "unsupported"}
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        return None, pdf_input_limit(max_bytes)
    try:
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
                body = compressed.read(max_bytes + 1)
        elif encoding == "deflate":
            decoder = zlib.decompressobj()
            try:
                body = decoder.decompress(raw, max_bytes + 1)
            except zlib.error:
                decoder = zlib.decompressobj(-zlib.MAX_WBITS)
                body = decoder.decompress(raw, max_bytes + 1)
            if len(body) <= max_bytes and (not decoder.eof or decoder.unused_data):
                return None, {"error": "pdf_transport_invalid", "error_class": "invalid_content"}
        else:
            body = raw
    except (OSError, EOFError, zlib.error):
        return None, {"error": "pdf_transport_invalid", "error_class": "invalid_content"}
    if len(body) > max_bytes:
        return None, pdf_input_limit(max_bytes)
    return body, None


def extract_pdf_text(data: bytes, max_pages: int = DEFAULT_MAX_PAGES,
                     trim_chars: int = DEFAULT_TRIM_CHARS, *,
                     max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """Inspect a complete PDF and report every intentional extraction limit.

    Strict structure rejection and a page failure are distinct from partial
    extraction. A structurally readable PDF may legitimately contain no text.
    """
    for value in (max_bytes, max_pages, trim_chars):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise PDFInspectionError("pdf_limit_invalid")
    if not isinstance(data, bytes):
        raise PDFInspectionError("pdf_input_invalid")
    if len(data) > max_bytes:
        raise PDFInspectionError("pdf_input_bytes_limit")
    if not data.startswith(b"%PDF-"):
        raise PDFInspectionError("pdf_signature_invalid")
    if b"%%EOF" not in data[-2048:]:
        raise PDFInspectionError("pdf_eof_missing")
    if not has_pypdf():
        raise ImportError("pdf_parser_unavailable")
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise PDFInspectionError("pdf_encrypted")
        n_pages = len(reader.pages)
        if not n_pages:
            raise PDFInspectionError("pdf_no_pages")
        metadata = reader.metadata or {}
        title = str(metadata.get("/Title", "") or "").strip()
        author = str(metadata.get("/Author", "") or "").strip()
    except PDFInspectionError:
        raise
    except Exception:
        raise PDFInspectionError("pdf_structure_invalid") from None

    n_read = min(n_pages, max_pages)
    pieces: list[str] = []
    chars_observed = 0
    chars_retained = 0
    nonempty_pages = 0
    for index in range(n_read):
        try:
            text = (reader.pages[index].extract_text() or "").strip()
        except Exception:
            raise PDFInspectionError("pdf_page_extraction_failed") from None
        if text:
            fragment = ("\n\n" if nonempty_pages else "") + text
            nonempty_pages += 1
            chars_observed += len(fragment)
            remaining = trim_chars - chars_retained
            if remaining > 0:
                take = fragment[:remaining]
                pieces.append(take)
                chars_retained += len(take)

    limits = []
    if n_read < n_pages:
        limits.append({"cap_field": "max_pages", "cap_value": max_pages,
                       "used": n_read, "available_total": n_pages})
    if chars_observed > trim_chars:
        cap = {"cap_field": "trim_chars", "cap_value": trim_chars, "used": chars_retained}
        if n_read == n_pages:
            cap["available_total"] = chars_observed
        limits.append(cap)
    return {"title": title, "author": author, "body_text": "".join(pieces),
            "n_pages": n_pages, "n_pages_read": n_read, "used_lib": "pypdf",
            "extraction_complete": not limits, **pdf_limit_state(limits)}
