#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Lessici RM-0005 residui dei consumer runtime N-Z.

I route-id, i concetti tabellari e il verbo canonico ``send`` sono identita'
tecniche.  Soltanto le rispettive superfici vivono nel catalogo traducibile.
I tre riconoscitori che possono indirizzare una mutazione richiedono una
risorsa nativa pronta e con revisione manuale; i suggerimenti non mutanti
mantengono invece la normale unione additiva con i baseline editoriali.
"""
from __future__ import annotations

import logging

import detection_lexicon as _dl


log = logging.getLogger("metnos.detection_lexicon.seed_residual_nz")

PATH_ALIAS_PREFIX = "residual.path_alias.route."
PATH_ALIAS_ROUTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("00", ("immagini", "Pictures", "Immagini", "Foto", "Images", "images")),
    ("01", ("foto", "Pictures", "Foto", "Immagini", "images")),
    ("02", ("documenti", "Documents", "Documenti", "Docs")),
    ("03", ("musica", "Music", "Musica")),
    ("04", ("video", "Videos", "Video", "Movies")),
    ("05", ("scaricati", "Downloads", "Scaricati", "Download")),
    ("06", ("scrivania", "Desktop", "Scrivania")),
    ("07", ("modelli", "Templates", "Modelli")),
    ("08", ("pubblici", "Public", "Pubblici")),
    ("09", ("pictures", "Pictures", "Immagini", "Foto")),
    ("10", ("documents", "Documents", "Documenti")),
    ("11", ("music", "Music", "Musica")),
    ("12", ("videos", "Videos", "Video", "Movies")),
    ("13", ("movies", "Movies", "Videos", "Video")),
    ("14", ("downloads", "Downloads", "Scaricati")),
    ("15", ("desktop", "Desktop", "Scrivania")),
    ("16", ("templates", "Templates", "Modelli")),
    ("17", ("public", "Public", "Pubblici")),
    ("18", ("images", "Pictures", "Immagini", "images")),
)
PATH_ALIAS_CONCEPTS = tuple(
    PATH_ALIAS_PREFIX + route_id for route_id, _payload in PATH_ALIAS_ROUTES
)

TABULAR_CONCEPT_ALIASES = "residual.tabular_projection.concept_aliases"
TABULAR_ORDINALS = "residual.tabular_projection.ordinals"
IMPLICIT_SEND_BIGRAMS = "residual.vocab.implicit_send_bigrams"
PROMPTS_LINT_HEDGES = "residual.prompts_lint.hedges"
SYNT_HINT_STOP_WORDS = "residual.synt.hint_stop_words"
TELOS_OVERLAP_STOP_WORDS = "residual.telos.overlap_stop_words"
TOOL_SCHEMA_BOUNDARIES = "residual.tool_schema.boundaries"

TABULAR_CONCEPT_PAYLOAD = {
    "path": [
        "path", "paths", "percorso", "percorsi", "filepath", "filepaths",
        "file_path", "file_paths", "image_path", "local_path",
    ],
    "directory": [
        "directory", "directories", "folder", "folders", "cartella",
        "cartelle", "directorio", "directorios", "carpeta", "carpetas",
        "ordner",
    ],
    "name": [
        "name", "names", "nome", "nomi", "filename", "filenames",
        "file_name", "file_names", "basename", "title", "titles",
        "titolo", "titoli",
    ],
    "description": [
        "description", "descriptions", "descrizione", "descrizioni",
        "desc", "caption", "captions",
    ],
    "size": [
        "size", "sizes", "size_bytes", "bytes", "dimensione", "dimensioni",
    ],
    "hash": [
        "hash", "hashes", "sha", "sha256", "checksum", "checksums",
        "digest", "digests", "impronta", "impronte",
    ],
    "score": [
        "score", "scores", "punteggio", "punteggi", "relevance",
        "rilevanza", "confidence", "confidenza",
    ],
    "keywords": ["keywords", "keyword", "parole_chiave", "tags", "tag"],
    "date": [
        "date", "dates", "data", "datetime", "timestamp", "created_at",
        "updated_at", "modified_at", "mtime",
    ],
    "domain": ["domain", "domains", "dominio", "domini"],
    "origin": [
        "origin", "origins", "original", "originals", "origine", "origini",
        "originale", "originali", "source", "sources", "sorgente", "sorgenti",
    ],
    "duplicate": [
        "duplicate", "duplicates", "duplicato", "duplicati", "copia",
        "copie", "copy", "copies", "duplicado", "duplicados", "duplikat",
        "duplikate",
    ],
    "url": ["url", "urls", "link", "links", "web_url", "web_view_url"],
    "count": ["count", "counts", "conteggio", "numero", "total", "totale"],
}

TABULAR_ORDINAL_PAYLOAD = {
    "1": ["first", "one", "primo", "prima"],
    "2": ["second", "two", "secondo", "seconda"],
    "3": ["third", "three", "terzo", "terza"],
    "4": ["fourth", "four", "quarto", "quarta"],
    "5": ["fifth", "five", "quinto", "quinta"],
}

IMPLICIT_SEND_PAYLOAD = [
    "email me", "mail me", "message me", "text me", "tell me",
    "let me know", "ping me", "shoot me",
    "mandami una email", "mandami una mail", "mandami un messaggio",
    "mandami un'email", "mandami un'e-mail", "fammi sapere",
    "tienimi al corrente", "tienimi informato",
]

PROMPTS_LINT_HEDGE_PAYLOAD = [
    "preferibilmente", "se possibile", "cerca di", "prova a",
    "preferably", "if possible", "try to", "perhaps", "maybe",
]

SYNT_HINT_STOP_PAYLOAD = [
    "a", "an", "the", "to", "of", "in", "for", "on", "and", "or",
    "is", "are", "be", "with", "from", "il", "la", "lo", "i", "gli",
    "le", "un", "una", "di", "da", "per", "su", "con", "e", "o",
]

TELOS_OVERLAP_STOP_PAYLOAD = [
    "il", "la", "i", "gli", "le", "un", "una", "di", "da", "del",
    "della", "dei", "delle", "a", "al", "alla", "ai", "alle", "in",
    "con", "su", "per", "tra", "fra", "e", "o", "ma", "che", "mi",
    "ci", "ti", "si", "ho", "ha", "hai", "the", "a", "an", "of",
    "to", "in", "is", "it", "for", "on", "with", "and", "or", "but",
    "this", "that", "una", "sono", "ci",
]

TOOL_SCHEMA_BOUNDARY_PAYLOAD = [
    "USO CORRETTO", "NON CONFONDERE", "DEVI:", "NON DEVI:",
    "USE CORRECT", "DO NOT CONFUSE", "MUST:", "MUST NOT:",
]

MANUAL_CONCEPTS = frozenset({
    *PATH_ALIAS_CONCEPTS,
    TABULAR_CONCEPT_ALIASES,
    TABULAR_ORDINALS,
    IMPLICIT_SEND_BIGRAMS,
})
AUTOMATIC_CONCEPTS = frozenset({
    PROMPTS_LINT_HEDGES,
    SYNT_HINT_STOP_WORDS,
    TELOS_OVERLAP_STOP_WORDS,
    TOOL_SCHEMA_BOUNDARIES,
})
CONCEPTS = MANUAL_CONCEPTS | AUTOMATIC_CONCEPTS

_registered_target: tuple[str, int] | None = None


def register_all() -> None:
    """Registra il corpus storico senza dipendere dal seed centrale."""
    for route_id, route in PATH_ALIAS_ROUTES:
        _dl.register(
            PATH_ALIAS_PREFIX + route_id,
            "phrases",
            it=list(route),
            en=list(route),
            review_policy="manual",
        )
    _dl.register(
        TABULAR_CONCEPT_ALIASES,
        "mapping",
        it=TABULAR_CONCEPT_PAYLOAD,
        en=TABULAR_CONCEPT_PAYLOAD,
        review_policy="manual",
    )
    _dl.register(
        TABULAR_ORDINALS,
        "mapping",
        it=TABULAR_ORDINAL_PAYLOAD,
        en=TABULAR_ORDINAL_PAYLOAD,
        review_policy="manual",
    )
    _dl.register(
        IMPLICIT_SEND_BIGRAMS,
        "phrases",
        it=IMPLICIT_SEND_PAYLOAD,
        en=IMPLICIT_SEND_PAYLOAD,
        review_policy="manual",
    )
    _dl.register(
        PROMPTS_LINT_HEDGES,
        "phrases",
        it=PROMPTS_LINT_HEDGE_PAYLOAD,
        en=PROMPTS_LINT_HEDGE_PAYLOAD,
    )
    _dl.register(
        SYNT_HINT_STOP_WORDS,
        "phrases",
        it=SYNT_HINT_STOP_PAYLOAD,
        en=SYNT_HINT_STOP_PAYLOAD,
    )
    _dl.register(
        TELOS_OVERLAP_STOP_WORDS,
        "phrases",
        it=TELOS_OVERLAP_STOP_PAYLOAD,
        en=TELOS_OVERLAP_STOP_PAYLOAD,
    )
    _dl.register(
        TOOL_SCHEMA_BOUNDARIES,
        "phrases",
        it=TOOL_SCHEMA_BOUNDARY_PAYLOAD,
        en=TOOL_SCHEMA_BOUNDARY_PAYLOAD,
    )


def _all_seed_rows_ready() -> bool:
    for concept in CONCEPTS:
        expected_kind = "mapping" if concept in {
            TABULAR_CONCEPT_ALIASES, TABULAR_ORDINALS,
        } else "phrases"
        expected_policy = "manual" if concept in MANUAL_CONCEPTS else "automatic"
        for language in ("it", "en"):
            resource = _dl.resource_for_language(
                concept, language, fallback=False, ready_only=True,
            )
            if (not resource or resource.get("kind") != expected_kind
                    or resource.get("review_policy") != expected_policy):
                return False
    return True


def _ensure_registered() -> None:
    """Registra lazy, anche dopo lo scambio del DB effettuato dai test."""
    global _registered_target
    target = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    if _registered_target == target:
        return
    try:
        register_all()
    except Exception:
        log.warning("residual N-Z lexicon seed non disponibile", exc_info=True)
        _registered_target = None
        return
    refreshed = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    _registered_target = refreshed if _all_seed_rows_ready() else None
    if _registered_target is not None:
        try:
            _dl.enqueue_language(_dl.current_lang())
        except Exception:
            log.warning("residual N-Z translation enqueue fallito", exc_info=True)


def _exact_resource(concept: str, language: str, *, manual: bool) -> dict | None:
    if not _dl.native_resource_status(concept, language).get("ok"):
        return None
    resource = _dl.resource_for_language(
        concept, language, fallback=False, ready_only=True,
    )
    if (not resource or (manual and resource.get("review_policy") != "manual")):
        return None
    return resource


def path_user_dir_aliases() -> dict[str, list[str]] | None:
    """Route localizzate pronte, oppure ``None`` per il gate mutating."""
    _ensure_registered()
    snapshot = _dl.native_ready_family_resources(
        {concept: "phrases" for concept in PATH_ALIAS_CONCEPTS},
        require_manual=True,
        include_reviewed_baselines=True,
    )
    if snapshot is None:
        return None
    aliases: dict[str, list[str]] = {}
    for concept in PATH_ALIAS_CONCEPTS:
        for resource in snapshot[concept]:
            payload = resource.get("payload") if resource else None
            if (not isinstance(payload, list) or len(payload) < 2
                    or not all(isinstance(item, str) and item.strip() for item in payload)):
                return None
            trigger = payload[0].strip().casefold()
            candidates = [item.strip() for item in payload[1:]]
            previous = aliases.get(trigger)
            if previous is not None and previous != candidates:
                return None
            aliases.setdefault(trigger, candidates)
    return aliases or None


def tabular_concept_aliases() -> dict[str, list[str]]:
    _ensure_registered()
    return _dl.native_ready_mapping(
        TABULAR_CONCEPT_ALIASES,
        require_manual=True,
        include_reviewed_baselines=True,
    )


def tabular_ordinals() -> dict[str, list[str]]:
    _ensure_registered()
    return _dl.native_ready_mapping(
        TABULAR_ORDINALS,
        require_manual=True,
        include_reviewed_baselines=True,
    )


def implicit_send_request(text: str) -> bool:
    """Rileva il bigramma mutante soltanto con corpus nativo revisionato."""
    if not isinstance(text, str) or not text:
        return False
    _ensure_registered()
    forms = _dl.native_ready_forms(
        IMPLICIT_SEND_BIGRAMS,
        require_manual=True,
        include_reviewed_baselines=True,
    )
    folded = text.casefold()
    return bool(forms) and any(form.casefold() in folded for form in forms)


def forms(concept: str) -> tuple[str, ...]:
    """Unione runtime per i lessici P2 non mutanti."""
    if concept not in AUTOMATIC_CONCEPTS:
        raise KeyError(f"unknown automatic residual concept: {concept}")
    _ensure_registered()
    return tuple(_dl.forms(concept))


def forms_for_language(concept: str, language: str) -> tuple[str, ...] | None:
    """Unione per lingua esplicita, con gate nativo (linter prompt)."""
    if concept not in AUTOMATIC_CONCEPTS:
        raise KeyError(f"unknown automatic residual concept: {concept}")
    _ensure_registered()
    active = _exact_resource(concept, language, manual=False)
    payload = active.get("payload") if active else None
    if (not active or active.get("kind") != "phrases"
            or not isinstance(payload, list) or not payload
            or not all(isinstance(item, str) and item.strip() for item in payload)):
        return None
    languages = [language] + [
        baseline for baseline in _dl.baseline_languages(concept)
        if baseline != language
    ]
    result: list[str] = []
    seen: set[str] = set()
    for candidate in languages:
        resource = _exact_resource(concept, candidate, manual=False)
        candidate_payload = resource.get("payload") if resource else None
        if not isinstance(candidate_payload, list):
            continue
        for raw in candidate_payload:
            if not isinstance(raw, str) or not raw.strip():
                continue
            value = raw.strip()
            folded = value.casefold()
            if folded not in seen:
                seen.add(folded)
                result.append(value)
    return tuple(result) or None


__all__ = [
    "AUTOMATIC_CONCEPTS",
    "CONCEPTS",
    "IMPLICIT_SEND_BIGRAMS",
    "MANUAL_CONCEPTS",
    "PATH_ALIAS_CONCEPTS",
    "PROMPTS_LINT_HEDGES",
    "SYNT_HINT_STOP_WORDS",
    "TABULAR_CONCEPT_ALIASES",
    "TABULAR_ORDINALS",
    "TELOS_OVERLAP_STOP_WORDS",
    "TOOL_SCHEMA_BOUNDARIES",
    "forms",
    "forms_for_language",
    "implicit_send_request",
    "path_user_dir_aliases",
    "register_all",
    "tabular_concept_aliases",
    "tabular_ordinals",
]
