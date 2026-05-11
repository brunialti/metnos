"""Test post-processing append link/path section in describe_entries (ADR 0119)."""
from __future__ import annotations

import sys
from pathlib import Path

if "/opt/myclaw/runtime" not in sys.path:
    sys.path.insert(0, "/opt/myclaw/runtime")

from describe_entries import _maybe_append_link_section  # type: ignore


_WEB_ENTRIES = [
    {"url": "https://example.com/a.pdf", "title": "Document A", "snippet": "..."},
    {"url": "https://example.com/b.pdf", "title": "Document B", "snippet": "..."},
    {"url": "https://example.com/c.html", "title": "Page C", "snippet": "..."},
]

_FILE_ENTRIES = [
    {"path": "/tmp/foo.txt", "name": "foo.txt"},
    {"path": "/tmp/bar.txt", "name": "bar.txt"},
]


def test_appends_link_section_when_llm_omits_urls():
    """LLM riassume in prosa senza URL → append elenco markdown."""
    text = "Sono stati trovati 3 documenti relativi al topic richiesto."
    out = _maybe_append_link_section(text, _WEB_ENTRIES, fmt="markdown",
                                      kind="web_result")
    assert "**Link diretti**" in out
    assert "[Document A](https://example.com/a.pdf)" in out
    assert "[Document B](https://example.com/b.pdf)" in out
    assert text.startswith("Sono stati trovati") and out.startswith(text)


def test_skips_when_llm_already_cited_majority():
    """LLM ha gia' citato 2/3 URL nel testo → no append (60% coverage)."""
    text = (
        "Documenti: https://example.com/a.pdf "
        "+ https://example.com/b.pdf"
    )
    out = _maybe_append_link_section(text, _WEB_ENTRIES, fmt="markdown",
                                      kind="web_result")
    # LLM ha citato 2/3 dei top → coverage = 0.66 >= 0.6 → no link section
    assert "**Link diretti**" not in out
    assert out == text


def test_appends_paths_for_file_kind():
    """Entries con `path` → append `Path` section."""
    text = "Trovati 2 file di testo."
    out = _maybe_append_link_section(text, _FILE_ENTRIES, fmt="markdown",
                                      kind="file")
    # Quando ci sono solo path (no url), il titolo cambia a "Path" (IT) o "Paths"
    assert ("**Path**" in out) or ("**Path**:\n" in out) or ("**Paths**" in out)
    assert "/tmp/foo.txt" in out
    assert "/tmp/bar.txt" in out


def test_skips_when_no_url_or_path():
    """Entries senza url/path → nessun append."""
    entries = [{"subject": "Hello", "from": "x@y.com"}]
    text = "Email summary."
    out = _maybe_append_link_section(text, entries, fmt="markdown", kind="email")
    assert out == text


def test_skips_when_fmt_json_or_bullet_list():
    """fmt='json' o 'bullet_list' → no append (formato strutturato)."""
    text = "..."
    out = _maybe_append_link_section(text, _WEB_ENTRIES, fmt="json",
                                      kind="web_result")
    assert out == text
    out2 = _maybe_append_link_section(text, _WEB_ENTRIES, fmt="bullet_list",
                                       kind="web_result")
    assert out2 == text


def test_html_format_uses_html_anchor():
    """fmt='html' → block <ul><li><a>."""
    text = "Riassunto"
    out = _maybe_append_link_section(text, _WEB_ENTRIES, fmt="html",
                                      kind="web_result")
    assert "<b>Link diretti</b>" in out
    assert '<a href="https://example.com/a.pdf">' in out


def test_label_sanitize_brackets():
    """Titoli con [ ] vengono sanitizzati per non rompere markdown."""
    entries = [
        {"url": "https://example.com/x", "title": "Article [updated]"},
    ]
    out = _maybe_append_link_section("Riassunto", entries, fmt="markdown",
                                      kind="web_result")
    # Le parentesi quadre vengono sostituite con tonde
    assert "Article (updated)" in out
    assert "[updated]" not in out  # non deve restare


def test_empty_entries_no_change():
    out = _maybe_append_link_section("text", [], fmt="markdown", kind="web_result")
    assert out == "text"


def test_max_appended_links_cap():
    """Cap a 10 link anche se entries > 10."""
    big = [
        {"url": f"https://example.com/{i}.pdf", "title": f"Doc{i}"}
        for i in range(20)
    ]
    text = "..."
    out = _maybe_append_link_section(text, big, fmt="markdown", kind="web_result")
    # Conta righe `- [Doc...]` nel link block
    n_links = out.count("https://example.com/")
    assert n_links == 10


def test_nontext_input_passthrough():
    out = _maybe_append_link_section(None, _WEB_ENTRIES, fmt="markdown",
                                      kind="web_result")
    assert out is None
