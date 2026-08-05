"""La guida pubblica all'interfaccia resta fresca e resta una MAPPA.

Il doc deriva dal registro delle superfici: se qualcuno cambia una pagina in
`ui_surfaces.py` senza rigenerare, questo test e' rosso PRIMA del deploy (che
rigenera e valida, ma solo al momento della pubblicazione).

Confine deliberato: campi visibili, comandi, passi e condizioni di arresto NON
si ripubblicano qui. Vivono una volta sola nel registro e raggiungono l'utente
attraverso il Tutor con autorita' di registro; duplicarli in un documento
pubblico creerebbe due copie della stessa evidenza con autorita' diverse, in
concorrenza per lo stesso posto nel retrieval.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from ui_surfaces import SURFACES


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "generate_ui_reference.py"


def _require_outputs(module) -> None:
    """Gli HTML generati non sono versionati (`docs/*` in .gitignore): su un
    clone fresco si materializzano al primo deploy. Qui si salta, non si
    fallisce: la freschezza ha senso solo dove l'output esiste."""

    if not all(path.is_file() for path in module.OUTPUTS.values()):
        pytest.skip("output non generato: eseguire scripts/generate_ui_reference.py")


def _module():
    spec = importlib.util.spec_from_file_location("ui_reference_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_checked_in_ui_reference_is_fresh_and_bilingual():
    module = _module()
    _require_outputs(module)
    for lang, output in module.OUTPUTS.items():
        content = module.render(lang)
        assert output.read_text(encoding="utf-8") == content, (
            f"{output} stantio: esegui python3 scripts/generate_ui_reference.py")
        assert content.count('<li>') == len(SURFACES)
        assert f'<html lang="{lang}">' in content
        assert f'<link rel="canonical" href="https://metnos.com/{lang}/interface">' in content


def test_every_surface_appears_with_its_path_and_route():
    import html

    module = _module()
    for lang in module.OUTPUTS:
        content = module.render(lang)
        for surface in SURFACES:
            assert surface.label(lang) in content, (lang, surface.key)
            # Il percorso e' un letterale dell'interfaccia, quindi va scritto
            # con `>` ESCAPED: se comparisse grezzo il documento sarebbe rotto.
            assert html.escape(surface.breadcrumb(lang)) in content, (
                lang, surface.key)
            assert f"<code>{surface.route}</code>" in content, (lang, surface.key)


def test_the_map_does_not_republish_the_registry_detail():
    """Un doc pubblico che ricopia campi e comandi duplicherebbe l'evidenza."""

    module = _module()
    detailed = next(s for s in SURFACES if s.controls("it") and s.stop_it)
    for lang in module.OUTPUTS:
        content = module.render(lang)
        for item in (*detailed.controls(lang), *detailed.stop_conditions(lang),
                     *detailed.procedure(lang)):
            assert item not in content, (lang, item)


def test_generated_page_is_admitted_by_the_public_inventory():
    from published_docs import catalog

    _require_outputs(_module())

    published = {doc.canonical_url for doc in catalog()}
    assert "https://metnos.com/it/interface" in published
    assert "https://metnos.com/en/interface" in published


def test_ui_reference_teaches_a_natural_request_and_telegram_boundary():
    module = _module()
    italian = module.render("it")
    english = module.render("en")

    assert "Chiedi a Metnos con una richiesta come quella di questo esempio" in italian
    assert "Ask Metnos with a request like this example" in english
    assert "Settings &gt; Sistema &gt; Modelli" in italian
    assert "Settings &gt; System &gt; Models" in english
    assert "si apre sempre nella chat web" in italian
    assert "always opens in the web chat" in english


def test_major_public_docs_link_to_the_ui_reference():
    for lang in ("it", "en"):
        paths = tuple(
            path for path in (
                ROOT / "docs" / lang / "index.html",
                ROOT / "docs" / lang / "domains.html",
                ROOT / "docs" / lang / "architecture" / "index.html",
                ROOT / "docs" / lang / "architecture" / "tutor.html",
            ) if path.is_file()
        )
        assert paths, lang
        for path in paths:
            assert "interface.html" in path.read_text(encoding="utf-8"), path
