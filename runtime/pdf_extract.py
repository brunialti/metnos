"""pdf_extract — estrazione testo da bytes PDF (helper condiviso).

Punto UNICO di estrazione PDF (§7.3, no duplicazione per lo stesso
oggetto): usato da `executors/read_urls_pdf` (lettura diretta) e da
`executors/read_urls_html` (handoff su Content-Type application/pdf,
12/6/2026 — find_urls ritorna liste miste HTML+PDF e il planner non puo'
conoscere il tipo a priori).

Parser: pypdf preferito, pdfminer.six fallback. Parsing in-memory via
BytesIO (niente file temporanei). Import dei parser lazy: il modulo
carica anche senza librerie PDF installate (ImportError solo alla call).
"""
from __future__ import annotations

import io

DEFAULT_MAX_PAGES = 100
DEFAULT_TRIM_CHARS = 200_000


def has_pypdf() -> bool:
    try:
        import pypdf  # noqa: F401
        return True
    except ImportError:
        return False


def has_pdfminer() -> bool:
    try:
        import pdfminer.high_level  # noqa: F401
        return True
    except ImportError:
        return False


def has_pdf_parser() -> bool:
    return has_pypdf() or has_pdfminer()


def extract_pdf_text(data: bytes,
                     max_pages: int = DEFAULT_MAX_PAGES,
                     trim_chars: int = DEFAULT_TRIM_CHARS) -> dict:
    """Estrae testo + metadati da bytes PDF.

    Ritorna {title, author, body_text, n_pages, n_pages_read, used_lib}.
    Sceglie pypdf > pdfminer; se nessuna libreria disponibile, raise
    ImportError. Errori di parsing propagano al chiamante (che decide la
    error_class, §2.8 no silent failure).
    """
    if has_pypdf():
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        meta = reader.metadata or {}
        n_pages_total = len(reader.pages)
        n_to_read = min(n_pages_total, max_pages)
        text_parts: list[str] = []
        for i in range(n_to_read):
            try:
                t = reader.pages[i].extract_text() or ""
            except Exception:
                t = ""
            if t:
                text_parts.append(t.strip())
        return {
            "title": str(meta.get("/Title", "") or "").strip(),
            "author": str(meta.get("/Author", "") or "").strip(),
            "body_text": "\n\n".join(text_parts)[:trim_chars],
            "n_pages": n_pages_total,
            "n_pages_read": n_to_read,
            "used_lib": "pypdf",
        }
    if has_pdfminer():
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(data), maxpages=max_pages)
        # pdfminer non offre meta facilmente; title/author vuoti.
        return {
            "title": "",
            "author": "",
            "body_text": (text or "").strip()[:trim_chars],
            "n_pages": -1,  # unknown senza pass separato
            "n_pages_read": -1,
            "used_lib": "pdfminer",
        }
    raise ImportError(
        "no PDF parsing library installed: install 'pypdf' "
        "(`pip install pypdf`) or 'pdfminer.six'"
    )
