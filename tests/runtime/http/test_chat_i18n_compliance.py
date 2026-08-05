"""Contratto i18n della finestra chat.

La chat produce testo sia con Jinja sia con JavaScript. Questo test impedisce
che una futura modifica aggiunga una label direttamente in italiano/inglese
nei principali sink visibili, aggirando il catalogo i18n.
"""
from __future__ import annotations

import re
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATE = _ROOT / "runtime" / "templates" / "chat.html"
_DIALOG_TEMPLATE = _ROOT / "runtime" / "templates" / "dialog_form.html"
_BASE_BARE_TEMPLATE = _ROOT / "runtime" / "templates" / "base_bare.html"


def _source() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def _dialog_source() -> str:
    return _DIALOG_TEMPLATE.read_text(encoding="utf-8")


def _catalog_declarations(source: str, name: str) -> dict[str, str]:
    block = re.search(
        rf"const {name}\s*=\s*\{{(?P<body>.*?)\n\s*\}};",
        source,
        re.S,
    )
    assert block, f"catalogo JavaScript {name} mancante"
    return dict(re.findall(
        r"^\s*([A-Za-z][A-Za-z0-9]*):\s*(.+?),\s*$",
        block.group("body"),
        re.M,
    ))


def test_chat_language_and_javascript_catalog_are_runtime_driven():
    source = _source()
    assert '<html lang="{{ ui_lang|e }}">' in source
    assert "const CHAT_LOCALE = {{ ui_lang|tojson }};" in source
    assert "toLocaleTimeString('it-IT'" not in source

    declarations = _catalog_declarations(source, "CHAT_I18N")
    assert declarations
    for name, expression in declarations.items():
        assert re.fullmatch(
            r'\{\{\s*msg\(["\'][A-Z0-9_.-]+["\']\)\|tojson\s*\}\}',
            expression,
        ), f"CHAT_I18N.{name} non proviene da msg(): {expression}"

    used_helpers = set(re.findall(r"chatText\(['\"]([A-Za-z0-9]+)['\"]", source))
    used_direct = set(re.findall(r"CHAT_I18N\.([A-Za-z0-9]+)", source))
    missing = (used_helpers | used_direct) - set(declarations)
    assert not missing, f"chiavi JavaScript i18n non dichiarate: {sorted(missing)}"


def test_embedded_dialog_language_and_javascript_catalog_are_runtime_driven():
    source = _dialog_source()
    base = _BASE_BARE_TEMPLATE.read_text(encoding="utf-8")
    assert '<html lang="{{ ui_lang|e }}">' in base
    declarations = _catalog_declarations(source, "DIALOG_I18N")
    assert declarations
    for name, expression in declarations.items():
        assert re.fullmatch(
            r'\{\{\s*msg\(["\'][A-Z0-9_.-]+["\']\)\|tojson\s*\}\}',
            expression,
        ), f"DIALOG_I18N.{name} non proviene da msg(): {expression}"
    used_helpers = set(re.findall(
        r"dialogText\(['\"]([A-Za-z0-9]+)['\"]", source,
    ))
    used_direct = set(re.findall(r"DIALOG_I18N\.([A-Za-z0-9]+)", source))
    missing = (used_helpers | used_direct) - set(declarations)
    assert not missing, f"chiavi dialogo i18n non dichiarate: {sorted(missing)}"


def test_no_literal_prose_in_chat_javascript_visible_sinks():
    source = _source() + "\n" + _dialog_source()

    # Assegnazioni dirette a textContent/title/alt: sono ammessi soltanto
    # simboli, stringhe vuote o espressioni Jinja i18n. Il testo dinamico
    # (CHAT_I18N/chatText/dati server) non e' una stringa letterale e non matcha.
    literal_sink = re.compile(
        r"(?:textContent|title|alt)\s*=\s*(['\"])(?P<text>.*?)\1",
    )
    violations = []
    for match in literal_sink.finditer(source):
        text = match.group("text")
        if "{{ msg(" in text:
            continue
        if re.fullmatch(r"\{\{.*?\}\}", text, re.S):
            continue
        if text.startswith("@keyframes "):
            continue
        if not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", text):
            continue
        line = source.count("\n", 0, match.start()) + 1
        violations.append((line, text))
    assert not violations, f"testo letterale in sink visibile: {violations}"

    # Banner e bolle locali sono altri sink user-facing. Un primo argomento
    # letterale con prosa sarebbe una regressione anche se non usa textContent.
    direct_calls = re.compile(
        r"(?:(?:showBanner|enterReadOnly)\(\s*|"
        r"add\(\s*['\"](?:err|tool-info|bot|me)['\"]\s*,\s*)"
        r"\s*(['\"])(?P<text>[^'\"\n]*)\1",
    )
    bad_calls = []
    for match in direct_calls.finditer(source):
        text = match.group("text")
        if re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", text):
            line = source.count("\n", 0, match.start()) + 1
            bad_calls.append((line, text))
    assert not bad_calls, f"prosa hardcoded in banner/bolla chat: {bad_calls}"

    # Anche alert/confirm dei form incorporati sono testo della chat.
    literal_dialogs = re.findall(
        r"\b(?:alert|confirm)\(\s*(['\"])(?P<text>[^'\"\n]+)\1",
        source,
    )
    assert not [text for _quote, text in literal_dialogs
                if re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", text)]


def test_static_chat_labels_use_msg_calls():
    source = _source() + "\n" + _dialog_source()
    # Attributi accessibili/tooltip/placeholder non possono essere stringhe
    # naturali fisse. Valori tecnici HTML (role, id, enterkeyhint) non entrano.
    attrs = re.findall(
        r"\b(?:aria-label|title|placeholder)=([\"'])(.*?)\1",
        source,
        re.S,
    )
    for _quote, value in attrs:
        if not value:
            continue
        # Prompt, etichette delle opzioni e placeholder di dominio sono dati
        # runtime; le label possedute dalla UI devono invece passare da msg().
        if re.fullmatch(r"\{\{.*?\}\}", value, re.S):
            continue
        assert "{{ msg(" in value, f"attributo chat non i18n: {value!r}"


def test_no_static_prose_outside_i18n_calls_in_chat_surfaces():
    """Ogni nodo testuale fisso della chat (form iframe incluso) e' i18n."""
    for path, allowed in ((_TEMPLATE, {"Metnos"}), (_DIALOG_TEMPLATE, set())):
        source = path.read_text(encoding="utf-8")
        source = re.sub(r"<script\b.*?</script>", "", source,
                        flags=re.S | re.I)
        source = re.sub(r"<style\b.*?</style>", "", source,
                        flags=re.S | re.I)
        source = re.sub(r"\{#.*?#\}|<!--.*?-->", "", source, flags=re.S)
        source = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", "", source, flags=re.S)
        source = re.sub(r"<[^>]+>", "\n", source)
        prose = {
            " ".join(line.split())
            for line in source.splitlines()
            if re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", line)
        }
        prose.discard("")
        assert prose <= allowed, (
            f"testo HTML statico non i18n in {path.name}: {sorted(prose - allowed)}"
        )
