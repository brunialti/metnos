"""Keep the first-search lifecycle available to the real Tutor source compiler."""

from functools import lru_cache

import pytest

from published_docs import catalog
from tutor.sources import _document_units
from tutor.cards import load_published


@lru_cache(maxsize=2)
def _guide_units(language):
    document = next(
        item for item in catalog()
        if item.relative_path == f"{language}/system/lre.html"
    )
    return tuple(_document_units(
        source_id=document.source_id,
        lang=document.lang,
        path=document.path,
        audience="user",
        source_kind="manual",
        priority=80,
        concept_prefix=f"public-doc-{document.concept_key}",
        public_url=document.canonical_url,
    ))


@pytest.mark.parametrize("language,heading,required", [
    ("it", "Indicizzazione delle foto: primo utilizzo", (
        "contenuto delle fotografie", "indice persistente", "nome di file",
    )),
    ("en", "Photo indexing: first use", (
        "content of photographs", "persistent index", "filename",
    )),
    ("it", "Prima ricerca e avvio automatico", (
        "automaticamente", "LRE deve essere attivo", "accessibile",
        "configurati", "solo dopo l'ammissione", "non è iniziata",
        "riusa il lavoro attivo", "poi ripeti",
        "motore generale per i lavori lunghi", "background", "ore",
        "notificato", "canali associati disponibili", "più veloci",
    )),
    ("en", "First search and automatic indexing", (
        "automatically", "LRE must be enabled", "accessible",
        "configured", "only after admission", "has not started",
        "reuses the active workload", "then repeat",
        "long-running engine", "background", "hours", "notified",
        "available associated channels", "faster",
    )),
    ("it", "Piccoli passi riprendibili", (
        "piccoli gruppi", "risorse configurate", "sopravvivono", "riavvio",
        "quando serve", "generazione completa", "rimane visibile",
    )),
    ("en", "Small, resumable steps", (
        "small groups", "resource limits", "survive", "restart",
        "when needed", "complete generation", "remains visible",
    )),
    ("it", "Perché la prima preparazione", (
        "numero", "dimensione", "risorse", "ore", "non una scadenza",
    )),
    ("en", "Why initial preparation", (
        "count", "size", "resources", "hours", "not a guaranteed deadline",
    )),
    ("it", "Lavoro asincrono, avanzamento", (
        "realmente accettata", "ricevuta", "chiudere la pagina", "Telegram",
        "associazione valida", "non ammesso non è iniziato",
    )),
    ("en", "Asynchronous work, progress", (
        "actually accepted", "receipt", "close the page", "Telegram",
        "valid association", "not admitted has not started",
    )),
    ("it", "Perché le ricerche successive", (
        "Dopo il completamento", "più veloci", "Non basta",
        "nuove o modificate", "incrementale",
    )),
    ("en", "Why subsequent searches", (
        "After indexing has completed", "faster", "not enough",
        "New or modified", "incremental",
    )),
])
def test_lifecycle_conditions_survive_source_segmentation(language, heading, required):
    units = [unit for unit in _guide_units(language) if unit.title.startswith(heading)]
    # Keep every condition with its claim, not in a separate optional neighbour.
    assert len(units) == 1
    unit = units[0]
    for phrase in required:
        assert phrase in unit.text
    assert unit.lang == language
    assert unit.visible_to("user")
    assert unit.public_url == f"https://metnos.com/{language}/system/lre"
    assert unit.observation_ref == ""


@pytest.mark.parametrize("language", ["it", "en"])
def test_tutor_guide_links_to_the_indexing_source(language):
    documents = {item.relative_path: item for item in catalog()}
    tutor = documents[f"{language}/architecture/tutor.html"]
    lre = documents[f"{language}/system/lre.html"]
    assert '../system/lre.html#photo-indexing' in tutor.path.read_text(encoding="utf-8")
    assert 'id="photo-indexing"' in lre.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("language,required,obsolete", [
    ("it", ("automaticamente", "non occorre avviarla manualmente", "background",
            "ore", "canali associati disponibili", "più veloci"), "proporti di crearne"),
    ("en", ("automatically", "no manual start", "background", "hours",
            "available associated channels", "faster"), "propose creating"),
])
def test_photo_card_agrees_with_the_automatic_indexing_guide(language, required, obsolete):
    card = next(card for card in load_published() if card.card_id == "fotografie-dominio")
    text = card.body[language]
    for phrase in required:
        assert phrase in text
    assert obsolete not in text
