"""Copia testo messaggio nella chat UI (bolle utente) — fix live 10/6/2026.

Non esisteva alcun modo di copiare il testo di una query inviata. Aggiunto
bottone copia discreto (riga azioni sotto la bolla 'me'), con fallback
execCommand per HTTP LAN (navigator.clipboard richiede secure context).
Chiavi i18n MSG_CHAT_COPY_HINT/MSG_CHAT_COPY_DONE nel seed bundled.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parents[1]
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

_TEMPLATE = _RUNTIME / "templates" / "chat.html"
_SEED_DB = _RUNTIME.parent / "install" / "data" / "i18n_seed.sqlite"


def test_template_ha_bottone_copia():
    src = _TEMPLATE.read_text()
    # CSS riga azioni + bottone
    assert ".msg .msg-actions" in src
    assert ".msg .msg-copy-btn" in src
    # JS: helper con fallback non-secure-context + factory bottone
    assert "function copyMsgText" in src
    assert "execCommand('copy')" in src
    assert "function makeCopyBtn" in src
    # Il bottone viene aggiunto SOLO alle bolle utente ('me')
    assert re.search(r"if\(cls === 'me'\)\{[^}]*makeCopyBtn", src, re.S)
    # Title/aria via i18n (§11: mai stringhe user-facing hardcoded)
    assert 'msg("MSG_CHAT_COPY_HINT")' in src
    assert 'msg("MSG_CHAT_COPY_DONE")' in src


def test_comportamenti_esistenti_non_regrediti():
    src = _TEMPLATE.read_text()
    # Marker dei comportamenti chiave: autoscroll, lightbox, composer,
    # feedback. Presenti e invariati nel nome.
    for marker in ("function scrollToEnd", "function nearBottom",
                   "closeLightbox", "msg-fb-btn", "sendFeedback",
                   "requestSubmit"):
        assert marker in src, f"marker mancante: {marker}"


def test_seed_i18n_ha_le_chiavi_copia():
    assert _SEED_DB.is_file()
    conn = sqlite3.connect(str(_SEED_DB))
    try:
        rows = dict(
            ((k, lang), text) for k, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n "
                "WHERE key IN ('MSG_CHAT_COPY_HINT','MSG_CHAT_COPY_DONE')"))
    finally:
        conn.close()
    assert rows[("MSG_CHAT_COPY_HINT", "it")]
    assert rows[("MSG_CHAT_COPY_HINT", "en")]
    assert rows[("MSG_CHAT_COPY_DONE", "it")]
    assert rows[("MSG_CHAT_COPY_DONE", "en")]


@pytest.mark.skipif(shutil.which("node") is None, reason="node assente")
def test_js_sintassi_node_check(tmp_path):
    src = _TEMPLATE.read_text()
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", src, re.S)
    assert blocks
    js = "\n;\n".join(
        re.sub(r"\{%.*?%\}", "", re.sub(r"\{\{.*?\}\}", "X", b))
        for b in blocks)
    f = tmp_path / "chat_check.js"
    f.write_text(js)
    r = subprocess.run(["node", "--check", str(f)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
