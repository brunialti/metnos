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
    ("it", "Traccia degli errori dopo la conclusione", (
        "nitems", "anche dopo un riavvio", "non i tentativi ripetuti",
        "totale le comprende tutte", "ogni tipo di lavoro", "conservato lo storico",
    )),
    ("en", "Error history after completion", (
        "nitems", "after a restart", "not repeated attempts", "total includes them all",
        "any workload", "job history is retained",
    )),
    ("it", "Errori dei tentativi, anche se superati", (
        "storico", "un tentativo successivo riesce", "non si somma a nitems",
        "senza essere dichiarato fallito",
    )),
    ("en", "Attempt errors, including recovered errors", (
        "history", "a later attempt succeeds", "not added to nitems",
        "without being declared failed",
    )),
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
    ("it", "Integrità e limiti della ripresa", (
        "non è necessariamente danneggiata", "continua sulle altre",
        "IMAGE_NOT_INDEXED:image_decode_failed", "fuori dal sistema di traduzione",
    )),
    ("en", "Integrity and recovery limits", (
        "not necessarily damaged", "continues with the others",
        "IMAGE_NOT_INDEXED:image_decode_failed", "outside the translation system",
    )),
    ("it", "Conclusione con errori espliciti", (
        "Terminato", "badge è arancione", "ciascun batch", "senza essere ricontati", "non diventano foto da saltare",
    )),
    ("en", "Finishing with explicit errors", (
        "Finished", "badge is orange", "each batch", "without being counted again", "do not become skippable photos",
    )),
    ("it", "Identificare e riprovare le foto non indicizzate", (
        "IMAGE_NOT_INDEXED", "non viene avviata", "ciclo automatico", "non garantisce", "non vengono cancellati",
    )),
    ("en", "Identifying and retrying photos not indexed", (
        "IMAGE_NOT_INDEXED", "not start indexing", "automatic retry loop", "does not guarantee", "not deleted",
    )),
    ("it", "Verifica dei file pubblicati", (
        "cinque file", "precedenti restano leggibili", "priva di queste prove",
    )),
    ("en", "Verifying published files", (
        "five index files", "Previous indexes remain readable", "without these proofs",
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


@pytest.mark.parametrize("language,phrases", [
    ("it", ("Fase x/y", "Valore", "Significato", "Che cos'è un batch", "non ha una dimensione", "solo la fase indicata", "non misurano il tempo rimanente")),
    ("en", ("Phase x/y", "Value", "Meaning", "What is a batch?", "no fixed size", "only the indicated phase", "do not measure time remaining")),
])
def test_generic_phase_and_batch_explanation_survives_tutor_source_compilation(language, phrases):
    text = " ".join(unit.text for unit in _guide_units(language)).lower()
    for phrase in phrases:
        assert phrase.lower() in text


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


@pytest.mark.parametrize("language,heading,phrases", [
    ("it", "Enrollment delle persone", ("due operazioni separate", "prima o dopo",
        "non richiede", "registro attuale", "non erano stati rilevati")),
    ("en", "Person enrollment", ("separate operations", "before or after",
        "does not require", "current registry", "were not detected")),
])
def test_enrollment_order_and_limits_are_available_to_tutor(language, heading, phrases):
    units = [unit for unit in _guide_units(language) if unit.title.startswith(heading)]
    assert units
    text = " ".join(unit.text for unit in units)
    for phrase in phrases:
        assert phrase in text
    assert all(unit.visible_to("user") for unit in units)


@pytest.mark.parametrize("language,heading,phrases", [
    ("it", "Come faccio a fare il mount", ("SMB/CIFS", "sola lettura",
        "utente del servizio", "campo password protetto", "percorso esistente",
        "non la garantisce", "non la indicizza", "non monta nulla")),
    ("en", "How do I mount", ("SMB/CIFS", "read-only", "service account",
        "protected password field", "existing path", "does not guarantee",
        "does not index", "does not mount anything")),
])
def test_quicktour_nas_example_is_real_public_tutor_evidence(language, heading, phrases):
    document = next(item for item in catalog()
                    if item.relative_path == f"{language}/Metnos_QuickTour.html")
    units = [unit for unit in _document_units(
        source_id=document.source_id, lang=language, path=document.path,
        audience="user", source_kind="manual", priority=80,
        public_url=document.canonical_url,
    ) if unit.title.startswith(heading)]
    assert units
    text = " ".join(unit.text for unit in units)
    assert "//storage.example.org/Photos" in text
    for phrase in phrases:
        assert phrase in text
    assert all(unit.visible_to("user") and unit.public_url == document.canonical_url
               and not unit.observation_ref for unit in units)
    assert document.path.read_text().count('class="scene"') == 6
