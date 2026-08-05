"""Pagina statica della risposta Reddit a Swafra."""
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_PAGE = _ROOT / "runtime" / "static" / "swafra-reddit-response.html"
_ROUTES = _ROOT / "runtime" / "http_routes_agent.py"
_EXPECTED_REPLY = """Metnos is a self-hosted architecture for governed AI agents, built around signed, typed executors with explicit authority.

Thanks — this gave me a useful direction. I would not plug the MCP server into Metnos as-is. I would turn the idea into a small local Memory Compiler: hybrid retrieval and typed links first, with Leiden clustering only where it proves useful. The core would handle provenance, conflicts, per-user isolation and forgetting; an executor or MCP endpoint would stay a thin, optional interface for explicit remember, inspect, search and forget operations.

P.S. More on Metnos: https://metnos.com"""


def _reply_from_page(source: str) -> str:
    start = source.index('<textarea id="reply" readonly>') + len(
        '<textarea id="reply" readonly>')
    end = source.index("</textarea>", start)
    return source[start:end].strip()


def _canonical_paragraphs(source: str) -> str:
    return "\n\n".join(
        " ".join(paragraph.split())
        for paragraph in source.split("\n\n")
        if paragraph.strip()
    )


def test_pagina_contiene_la_risposta_pubblicata_integrale():
    page = _PAGE.read_text(encoding="utf-8")
    # La roadmap RM-0001 è un record chiuso e non è una sorgente mutabile per
    # una pagina operativa. La pagina conserva autonomamente il testo approvato.
    assert _canonical_paragraphs(_reply_from_page(page)) == \
        _canonical_paragraphs(_EXPECTED_REPLY)


def test_pagina_ha_copia_con_fallback_http():
    page = _PAGE.read_text(encoding="utf-8")
    assert 'id="copy"' in page
    assert "navigator.clipboard.writeText(reply.value)" in page
    assert 'document.execCommand("copy")' in page
    assert 'role="status"' in page


def test_static_handler_serve_html_con_content_type_corretto():
    routes = _ROUTES.read_text(encoding="utf-8")
    assert '".html": "text/html"' in routes
    assert '("GET",  "/static/{name}",' in routes
