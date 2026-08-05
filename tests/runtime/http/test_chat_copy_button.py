"""Copia dell'intera chat e selezione nativa dei messaggi nella UI."""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

_TEMPLATE = _RUNTIME / "templates" / "chat.html"
_SEED_DB = _RUNTIME.parent / "install" / "data" / "i18n_seed.sqlite"


def test_template_usa_selezione_nativa_senza_bottone_per_messaggio():
    src = _TEMPLATE.read_text()
    # Il testo delle bolle deve poter essere selezionato come normale testo
    # della pagina, senza un'azione di copia aggiuntiva per ogni messaggio.
    assert "-webkit-user-select:text;user-select:text" in src
    assert ".msg .msg-actions" not in src
    assert ".msg .msg-copy-btn" not in src
    assert "function makeCopyBtn" not in src
    # Il comando esplicito per copiare l'intera conversazione resta disponibile
    # anche su HTTP LAN, dove il Clipboard API puo' non essere disponibile.
    assert 'id="copyChatBtn"' in src
    assert "function copyMsgText" in src
    assert "execCommand('copy')" in src
    assert 'msg("MSG_CHAT_COPY_DONE")' in src


def test_comportamenti_esistenti_non_regrediti():
    src = _TEMPLATE.read_text()
    # Marker dei comportamenti chiave: autoscroll, lightbox, composer,
    # feedback. Presenti e invariati nel nome.
    for marker in ("function scrollToEnd", "function nearBottom",
                   "closeLightbox", "msg-fb-btn", "sendFeedback",
                   "requestSubmit"):
        assert marker in src, f"marker mancante: {marker}"


def test_consenso_annidato_tiene_la_cattura_dentro_il_modulo():
    src = _TEMPLATE.read_text()
    assert ".dialog-context-preview" in src
    assert "inlineFormOwnsImages = true" in src
    assert "imgs.length && !inlineFormOwnsImages" in src
    assert "a && a.download_url && !a.thumb_url" in src
    # Metadata prima; poi anteprima e iframe, in quest'ordine.
    assert src.index("if(inlineFormContext) d.appendChild(inlineFormContext)") < src.index(
        "if(inlineFormWrap) d.appendChild(inlineFormWrap)")


def test_dialog_lifecycle_e_metadata_non_regrediti():
    src = _TEMPLATE.read_text()
    assert "function removeDialogHistory" in src
    assert "function mergeDialogCompletionMeta" in src
    assert "const response = await fetch(formPath" in src
    assert "ifr.srcdoc = formHtml" in src
    assert "metnos.dialog.terminal" in src
    assert "resumedPath.length ? resumedPath : originPath" in src


def test_seed_i18n_ha_la_conferma_copia_chat():
    assert _SEED_DB.is_file()
    conn = sqlite3.connect(str(_SEED_DB))
    try:
        rows = dict(
            ((k, lang), text) for k, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n "
                "WHERE key = 'MSG_CHAT_COPY_DONE'"))
    finally:
        conn.close()
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
