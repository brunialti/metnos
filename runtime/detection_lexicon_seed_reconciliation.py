#!/usr/bin/env python3
"""Versioned field bindings for reconciliation and structured evidence.

Canonical field identifiers remain technical runtime data.  Natural-language
surfaces are resolved only when a consumer builds an executor boundary.  The
complete bundle is manual-review and native-ready: a partial or stale target
language never produces a hybrid schema or an apparently complete artefact.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Mapping

import detection_lexicon as _dl


FIELD_CONCEPT = "reconciliation.field"
TEMPORAL_CONCEPT = "reconciliation.temporal_field"
SOURCE_CONCEPT = "reconciliation.source_field"
AUDIT_CONCEPT = "reconciliation.audit_field"
LABEL_CONCEPT = "reconciliation.output_label"
CANCELLATION_CONCEPT = "reconciliation.cancellation_state"
VARIANT_CONCEPT = "reconciliation.document_variant"
RELEVANCE_GENERIC_CONCEPT = "reconciliation.relevance_generic"
RELEVANCE_PERSON_TYPE_CONCEPT = "reconciliation.relevance_person_type"
RELEVANCE_ORGANIZATION_TYPE_CONCEPT = (
    "reconciliation.relevance_organization_type"
)
GATE_CONCEPTS = {
    "logical_dedup": "reconciliation.gate.logical_dedup",
    "contradiction": "reconciliation.gate.contradiction",
    "spreadsheet": "reconciliation.gate.spreadsheet",
    "analysis": "reconciliation.gate.analysis",
    "conflict_severity": "reconciliation.gate.conflict_severity",
    "archive": "reconciliation.gate.archive",
    "focused": "reconciliation.gate.focused",
    "date_only_field": "reconciliation.gate.date_only_field",
    "datetime_field": "reconciliation.gate.datetime_field",
}

CONCEPTS = (
    FIELD_CONCEPT,
    TEMPORAL_CONCEPT,
    SOURCE_CONCEPT,
    AUDIT_CONCEPT,
    LABEL_CONCEPT,
    CANCELLATION_CONCEPT,
    VARIANT_CONCEPT,
    RELEVANCE_GENERIC_CONCEPT,
    RELEVANCE_PERSON_TYPE_CONCEPT,
    RELEVANCE_ORGANIZATION_TYPE_CONCEPT,
    *GATE_CONCEPTS.values(),
)

MULTISOURCE_DEFAULT_FIELD_IDS = (
    "entity", "type", "normalized_value", "original_value", "domain",
    "origin", "responsible", "confidence", "conflict",
)
MESSAGE_EVENT_DEFAULT_FIELD_IDS = (
    "entity", "normalized_value", "original_value", "origin",
    "responsible", "confidence", "conflict",
)
MULTISOURCE_COMMON_FIELD_IDS = (
    "entity", "type", "normalized_value", "original_value", "project",
    "organization", "role", "email", "phone", "amount", "deadline",
    "decision", "status", "origin", "responsible", "confidence", "domain",
    "readable", "duplicates", "diagnostic",
)
MESSAGE_EVENT_COMMON_FIELD_IDS = (
    "entity", "type", "normalized_value", "original_value", "origin",
    "responsible", "confidence", "deadline", "status", "domain",
)
MESSAGE_EVENT_FOCUSED_FIELD_IDS = (
    "entity", "commitment_type", "person", "organization",
    "normalized_date", "normalized_time", "timezone", "location",
    "original_value", "status", "responsible", "origin", "confidence",
    "domain",
)

# Producer keys and their precedence are protocol structure, not language.
SOURCE_BINDINGS = {
    "sender": ("from",),
    "subject": ("subject",),
    "body": ("body_preview", "body"),
    "summary": ("summary",),
    "title": ("title", "summary", "subject"),
    "final_url": ("final_url", "url"),
    "language": ("language", "lang"),
    "text_length": ("text_length",),
    "start": ("start",),
    "end": ("end",),
    "location": ("location",),
    "description": ("description",),
    "status": ("status",),
    "redirect": ("redirected",),
    "iframe": ("iframe_count", "iframe_urls"),
    "javascript_required": ("js_required",),
    "attendees": ("attendees",),
    "responsible": ("organizer", "attendees", "from"),
    "date": ("date",),
}

_FIELD_IDS = frozenset({
    *MULTISOURCE_COMMON_FIELD_IDS,
    *MESSAGE_EVENT_FOCUSED_FIELD_IDS,
    "conflict", "match", "content_hash", "file_type",
})
_TEMPORAL_IDS = frozenset({"modified_time"})
_SOURCE_IDS = frozenset(SOURCE_BINDINGS)
_AUDIT_IDS = frozenset({"supplier", "status"})
_LABEL_IDS = frozenset({
    "missing", "exact", "probable", "email_only", "calendar_only",
    "cancelled", "unmatched", "state_cancelled",
})

_registered_target: tuple[str, int] | None = None


def register_all() -> None:
    """Register the historical IT/EN bindings idempotently."""

    R = _dl.register
    R(FIELD_CONCEPT, "mapping", match_mode="word", review_policy="manual",
      it={
          "entity": ["entità"],
          "type": ["tipo"],
          "normalized_value": ["valore normalizzato"],
          "original_value": ["valore originale"],
          "domain": ["dominio", "domini", "source_domain"],
          "origin": [
              "origine", "origini", "source", "origine_file",
              "source_file", "file_path", "percorso", "path",
          ],
          "responsible": ["responsabile"],
          "confidence": [
              "confidenza", "confidence_level", "livello_confidenza",
          ],
          "conflict": ["conflitto"],
          "project": ["progetto", "progetti"],
          "organization": ["organizzazione", "organizzazioni"],
          "role": ["ruolo"],
          "email": ["email"],
          "phone": ["telefono"],
          "amount": ["importo"],
          "deadline": [
              "scadenza", "scadenze", "data", "data_scadenza", "due_date",
          ],
          "decision": ["decisione"],
          "status": ["stato"],
          "readable": ["leggibile", "file_leggibile"],
          "duplicates": [
              "duplicati", "duplicate_paths", "percorsi_duplicati",
          ],
          "diagnostic": [
              "diagnostica", "parse_diagnostic", "errore_lettura",
          ],
          "content_hash": [
              "hash", "content_hash", "content_sha256", "signature", "firma",
              "firma_contenuto",
          ],
          "file_type": ["file_type", "tipo_file", "formato"],
          "commitment_type": ["tipo impegno"],
          "person": ["persona"],
          "normalized_date": ["data normalizzata"],
          "normalized_time": ["ora normalizzata"],
          "timezone": ["fuso orario"],
          "location": ["luogo"],
          "match": ["corrispondenza"],
      },
      en={
          "entity": ["entity", "entities"],
          "type": ["type"],
          "normalized_value": ["normalized value"],
          "original_value": ["original value"],
          "domain": ["domain", "domains", "source_domain"],
          "origin": [
              "origin", "origins", "source", "source_file", "file_path",
              "path",
          ],
          "responsible": ["responsible"],
          "confidence": ["confidence", "confidence_level"],
          "conflict": ["conflict"],
          "project": ["project", "projects"],
          "organization": ["organization", "organizations"],
          "role": ["role"],
          "email": ["email"],
          "phone": ["phone"],
          "amount": ["amount"],
          "deadline": ["deadline", "date"],
          "decision": ["decision"],
          "status": ["status"],
          "readable": ["readable"],
          "duplicates": ["duplicates", "duplicate_paths"],
          "diagnostic": ["diagnostic", "parse_diagnostic"],
          "content_hash": [
              "hash", "content_hash", "content_sha256", "signature",
          ],
          "file_type": ["file_type", "format"],
          "commitment_type": ["commitment type"],
          "person": ["person"],
          "normalized_date": ["normalized date"],
          "normalized_time": ["normalized time"],
          "timezone": ["time zone", "timezone"],
          "location": ["location"],
          "match": ["match"],
      })
    R(TEMPORAL_CONCEPT, "mapping", match_mode="word",
      review_policy="manual",
      it={"modified_time": [
          "mtime", "data_modifica", "ultima_modifica",
      ]},
      en={"modified_time": [
          "modified_time", "modification_time", "mtime", "modified",
          "last_modified",
      ]})
    R(SOURCE_CONCEPT, "mapping", match_mode="word", review_policy="manual",
      it={
          "sender": ["mittente", "from"],
          "subject": ["oggetto", "subject"],
          "body": ["corpo", "body"],
          "summary": ["summary"],
          "title": ["titolo", "title"],
          "final_url": ["url_finale", "final_url"],
          "language": ["lingua", "language"],
          "text_length": [
              "caratteri_estratti", "lunghezza_testo", "text_length",
          ],
          "start": ["inizio", "start"],
          "end": ["fine", "end"],
          "location": ["luogo", "location"],
          "description": ["descrizione", "description"],
          "status": ["stato", "status"],
          "redirect": ["is_redirect"],
          "iframe": ["has_iframe"],
          "javascript_required": ["needs_js_render"],
          "attendees": ["partecipanti", "attendees"],
          "responsible": ["responsabile", "responsible"],
          "date": ["data", "date"],
      },
      en={
          "sender": ["sender", "from"],
          "subject": ["subject"],
          "body": ["body"],
          "summary": ["summary"],
          "title": ["title"],
          "final_url": ["final_url"],
          "language": ["language"],
          "text_length": ["text_length"],
          "start": ["start"],
          "end": ["end"],
          "location": ["location"],
          "description": ["description"],
          "status": ["status"],
          "redirect": ["is_redirect"],
          "iframe": ["has_iframe"],
          "javascript_required": ["needs_js_render"],
          "attendees": ["attendees"],
          "responsible": ["responsible"],
          "date": ["date"],
      })
    R(AUDIT_CONCEPT, "mapping", match_mode="word", review_policy="manual",
      it={
          "supplier": ["fornitore"],
          "status": ["stato"],
      },
      en={
          "supplier": ["supplier", "vendor"],
          "status": ["status", "state"],
      })
    R(LABEL_CONCEPT, "mapping", match_mode="word", review_policy="manual",
      it={
          "missing": ["mancante"],
          "exact": ["corrispondenza esatta"],
          "probable": ["corrispondenza probabile"],
          "email_only": ["solo email"],
          "calendar_only": ["solo calendario"],
          "cancelled": ["cancellazione senza evento"],
          "unmatched": ["non riconciliato"],
          "state_cancelled": ["annullato"],
      },
      en={
          "missing": ["missing"],
          "exact": ["exact match"],
          "probable": ["probable match"],
          "email_only": ["email only"],
          "calendar_only": ["calendar only"],
          "cancelled": ["cancellation without event"],
          "unmatched": ["unmatched"],
          "state_cancelled": ["cancelled"],
      })
    R(CANCELLATION_CONCEPT, "phrases", match_mode="word",
      review_policy="manual",
      it=[
          "annullato", "annullata", "cancellato", "cancellata",
          "annullamento prenotazione", "e stato annullato",
          "e stata annullata", "appuntamento annullato",
          "appuntamento annullata", "cancellazione prenotazione",
      ],
      en=["cancelled", "canceled", "booking cancellation"])
    R(VARIANT_CONCEPT, "phrases", match_mode="word", review_policy="manual",
      it=[
          "approvato", "approvata", "finale", "revisione", "bozza",
          "proposta", "proposto", "copia", "duplicato",
      ],
      en=[
          "approved", "final", "revision", "revised", "draft", "proposal",
          "proposed", "copy", "duplicate",
      ])
    R(RELEVANCE_GENERIC_CONCEPT, "phrases", match_mode="word",
      review_policy="manual",
      it=[
          "documenti", "documento", "progetto", "programma", "cartella",
          "report", "visita", "policlinico", "file", "files",
      ],
      en=[
          "documents", "document", "project", "program", "folder", "report",
          "visit", "hospital", "file", "files",
      ])
    R(RELEVANCE_PERSON_TYPE_CONCEPT, "phrases", match_mode="word",
      review_policy="manual",
      it=["persona", "contatto"],
      en=["person", "contact"])
    R(RELEVANCE_ORGANIZATION_TYPE_CONCEPT, "phrases", match_mode="word",
      review_policy="manual",
      it=["organizzazione", "azienda", "fornitore"],
      en=["organization", "company", "supplier"])
    regexes = {
        "logical_dedup": {
            "it": [
                r"\bdeduplic\w*\b|(?=.*\bduplicat\w*\b)"
                r"(?=.*(?:\blogic\w*\b|\bsenza\s+cancell\w*\b))",
            ],
            "en": [
                r"\bdeduplic\w*\b|(?=.*\bduplicat\w*\b)"
                r"(?=.*(?:\blogic\w*\b|\bwithout\s+delet\w*\b))",
            ],
        },
        "contradiction": {
            "it": [r"\b(?:contradditt\w*|inconsisten\w*|conflitt\w*)\b"],
            "en": [r"\b(?:contradict\w*|inconsisten\w*|conflict\w*)\b"],
        },
        "spreadsheet": {
            "it": [r"\b(?:foglio|xlsx)\b"],
            "en": [r"\b(?:spreadsheet|workbook|xlsx)\b"],
        },
        "analysis": {
            "it": [
                r"\b(?:analizz\w*|incroci\w*|riconcili\w*|estrai\w*|"
                r"individua\w*|normalizz\w*|conflitt\w*|deduplic\w*)\b",
            ],
            "en": [
                r"\b(?:analy[sz]\w*|cross[- ]?reference\w*|reconcil\w*|"
                r"extract\w*|identify\w*|normaliz\w*|conflict\w*|"
                r"deduplic\w*)\b",
            ],
        },
        "conflict_severity": {
            "it": [
                r"\bgravit[aà]\s+(?:(?:del|della|dei|delle|di)\s+)?"
                r"conflitt\w*\b",
            ],
            "en": [
                r"\b(?:conflict\s+severity|severity\s+(?:of\s+)?conflicts?)\b",
            ],
        },
        "archive": {
            "it": [r"\b(?:zip|archivio\s+compress\w*)\b"],
            "en": [r"\b(?:zip|compressed\s+archive)\b"],
        },
        "focused": {
            "it": [
                r"\b(?:corrispondenz\w*\s+(?:esatt\w*|probabil\w*)|"
                r"solo\s+(?:email|calendario)|cancellazion\w*\s+priv\w*)\b",
            ],
            "en": [
                r"\b(?:exact\s+match|probable\s+match|"
                r"only\s+(?:email|calendar))\b",
            ],
        },
        "date_only_field": {
            "it": [
                r"(^|_)(?:data|scadenza|emiss\w*|fattur\w*)($|_)",
            ],
            "en": [
                r"(^|_)(?:date|deadline|due|issue|invoice)($|_)",
            ],
        },
        "datetime_field": {
            "it": [r"(^|_)(?:inizio|fine|ora)($|_)"],
            "en": [
                r"(^|_)(?:start|end|datetime|when|begin|finish)($|_)",
            ],
        },
    }
    for gate, by_language in regexes.items():
        R(GATE_CONCEPTS[gate], "regex", match_mode="word",
          review_policy="manual", it=by_language["it"], en=by_language["en"])


def _all_seed_rows_ready() -> bool:
    for concept in CONCEPTS:
        for language in ("it", "en"):
            if not _dl.native_resource_status(concept, language)["ok"]:
                return False
    return True


def _ensure_registered() -> None:
    global _registered_target
    target = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    if _registered_target == target:
        return
    register_all()
    refreshed = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    _registered_target = refreshed if _all_seed_rows_ready() else None


def _mapping_from_resources(resources, expected: frozenset[str]):
    mapping: dict[str, list[str]] = {}
    surface_owners: dict[str, str] = {}
    for resource in resources:
        payload = resource.get("payload")
        if not isinstance(payload, dict):
            return None
        for canonical, forms in payload.items():
            if not isinstance(forms, list):
                return None
            bucket = mapping.setdefault(canonical, [])
            for form in forms:
                if not isinstance(form, str) or not form.strip():
                    return None
                normalized = form.strip()
                surface = normalized.casefold()
                previous = surface_owners.get(surface)
                if previous is not None and previous != canonical:
                    return None
                surface_owners[surface] = canonical
                if normalized not in bucket:
                    bucket.append(normalized)
    if set(mapping) != set(expected):
        return None
    return mapping


def _phrases_from_resources(resources) -> tuple[str, ...] | None:
    values: list[str] = []
    seen: set[str] = set()
    for resource in resources:
        payload = resource.get("payload")
        if not isinstance(payload, list) or not payload:
            return None
        for value in payload:
            if not isinstance(value, str) or not value.strip():
                return None
            normalized = value.strip()
            folded = normalized.casefold()
            if folded not in seen:
                seen.add(folded)
                values.append(normalized)
    return tuple(values) or None


def _patterns_from_resources(resources) -> tuple[re.Pattern, ...] | None:
    phrases = _phrases_from_resources(resources)
    if not phrases:
        return None
    try:
        return tuple(re.compile(pattern, re.IGNORECASE) for pattern in phrases)
    except re.error:
        return None


def _surface_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value).casefold())
    normalized = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    value = "".join(
        character if character.isalnum() else "_"
        for character in normalized
    )
    return re.sub(r"_+", "_", value).strip("_")


def _owners(mapping: dict[str, list[str]]) -> dict[str, str] | None:
    owners: dict[str, str] = {}
    for canonical, forms in mapping.items():
        for form in forms:
            folded = _surface_key(form)
            previous = owners.get(folded)
            if previous is not None and previous != canonical:
                return None
            owners[folded] = canonical
    return owners


@dataclass(frozen=True, slots=True)
class ReconciliationLexicon:
    fields: dict[str, list[str]]
    temporal: dict[str, list[str]]
    source: dict[str, list[str]]
    audit: dict[str, list[str]]
    labels: dict[str, list[str]]
    cancellation_states: tuple[str, ...]
    variant_tokens: frozenset[str]
    relevance_generic_tokens: frozenset[str]
    relevance_person_types: frozenset[str]
    relevance_organization_types: frozenset[str]
    gates: dict[str, tuple[re.Pattern, ...]]
    field_owners: dict[str, str]
    source_owners: dict[str, str]
    audit_owners: dict[str, str]

    def surface(self, canonical: str) -> str:
        return self.fields[canonical][0]

    def surfaces(self, canonicals) -> list[str]:
        return [self.surface(str(canonical)) for canonical in canonicals]

    def canonical_field(self, surface: str) -> str | None:
        if str(surface) in _FIELD_IDS:
            return str(surface)
        return self.field_owners.get(_surface_key(surface))

    def canonical_audit(self, surface: str) -> str | None:
        if str(surface) in _AUDIT_IDS:
            return str(surface)
        return self.audit_owners.get(_surface_key(surface))

    def record_field(self, surface: str) -> str:
        canonical = self.canonical_field(surface)
        return self.surface(canonical) if canonical is not None else str(surface)

    def source_keys(self, surface: str) -> tuple[str, ...] | None:
        canonical = self.source_identity(surface)
        return SOURCE_BINDINGS.get(canonical) if canonical is not None else None

    def source_identity(self, surface: str) -> str | None:
        return self.source_owners.get(_surface_key(surface))

    def audit_forms(self, surface: str) -> tuple[str, ...] | None:
        canonical = self.audit_owners.get(_surface_key(surface))
        return tuple(self.audit.get(canonical, ())) if canonical is not None else None

    def label(self, canonical: str) -> str:
        return self.labels[canonical][0]

    def matches_gate(self, gate: str, text: str) -> bool:
        return any(pattern.search(text or "") for pattern in self.gates.get(gate, ()))

    def is_variant_token(self, token: str) -> bool:
        return _surface_key(token) in self.variant_tokens


def family_kinds() -> dict[str, str]:
    """Kinds required to decode one complete reconciliation snapshot."""

    return {
        FIELD_CONCEPT: "mapping", TEMPORAL_CONCEPT: "mapping",
        SOURCE_CONCEPT: "mapping", AUDIT_CONCEPT: "mapping",
        LABEL_CONCEPT: "mapping", CANCELLATION_CONCEPT: "phrases",
        VARIANT_CONCEPT: "phrases", RELEVANCE_GENERIC_CONCEPT: "phrases",
        RELEVANCE_PERSON_TYPE_CONCEPT: "phrases",
        RELEVANCE_ORGANIZATION_TYPE_CONCEPT: "phrases",
        **{concept: "regex" for concept in GATE_CONCEPTS.values()},
    }


def from_resources(
    snapshot: Mapping[str, tuple[dict, ...]],
) -> ReconciliationLexicon | None:
    """Decode a caller-owned atomic family snapshot without further I/O.

    This seam lets consumers combine reconciliation with other reviewed
    concepts in one ``native_ready_family_resources`` call.  Extra concepts are
    ignored, while a missing or malformed reconciliation member fails closed.
    """

    kinds = family_kinds()
    if not isinstance(snapshot, Mapping) or any(
            concept not in snapshot for concept in kinds):
        return None
    fields = _mapping_from_resources(snapshot[FIELD_CONCEPT], _FIELD_IDS)
    temporal = _mapping_from_resources(snapshot[TEMPORAL_CONCEPT], _TEMPORAL_IDS)
    source = _mapping_from_resources(snapshot[SOURCE_CONCEPT], _SOURCE_IDS)
    audit = _mapping_from_resources(snapshot[AUDIT_CONCEPT], _AUDIT_IDS)
    labels = _mapping_from_resources(snapshot[LABEL_CONCEPT], _LABEL_IDS)
    cancellation_states = _phrases_from_resources(
        snapshot[CANCELLATION_CONCEPT]
    )
    variant_tokens = _phrases_from_resources(snapshot[VARIANT_CONCEPT])
    relevance_generic = _phrases_from_resources(
        snapshot[RELEVANCE_GENERIC_CONCEPT]
    )
    relevance_person_types = _phrases_from_resources(
        snapshot[RELEVANCE_PERSON_TYPE_CONCEPT]
    )
    relevance_organization_types = _phrases_from_resources(
        snapshot[RELEVANCE_ORGANIZATION_TYPE_CONCEPT]
    )
    gates = {
        gate: _patterns_from_resources(snapshot[concept])
        for gate, concept in GATE_CONCEPTS.items()
    }
    if (not all((fields, temporal, source, audit, labels,
                 cancellation_states, variant_tokens))
            or not relevance_generic
            or not relevance_person_types
            or not relevance_organization_types
            or not all(gates.values())):
        return None
    field_owners = _owners(fields)
    source_owners = _owners(source)
    audit_owners = _owners(audit)
    if field_owners is None or source_owners is None or audit_owners is None:
        return None
    return ReconciliationLexicon(
        fields=fields,
        temporal=temporal,
        source=source,
        audit=audit,
        labels=labels,
        cancellation_states=cancellation_states,
        variant_tokens=frozenset(
            _surface_key(token) for token in variant_tokens
        ),
        relevance_generic_tokens=frozenset(
            _surface_key(token) for token in relevance_generic
        ),
        relevance_person_types=frozenset(
            _surface_key(token) for token in relevance_person_types
        ),
        relevance_organization_types=frozenset(
            _surface_key(token) for token in relevance_organization_types
        ),
        gates={gate: patterns for gate, patterns in gates.items() if patterns},
        field_owners=field_owners,
        source_owners=source_owners,
        audit_owners=audit_owners,
    )


def load() -> ReconciliationLexicon | None:
    """Return the complete native-ready bundle, or ``None`` fail-closed."""

    _ensure_registered()
    snapshot = _dl.native_ready_family_resources(
        family_kinds(),
        require_manual=True,
        include_reviewed_baselines=True,
    )
    return from_resources(snapshot) if snapshot is not None else None


def temporal_aliases() -> frozenset[str] | None:
    """Ready temporal-field surfaces used by the document-report guard."""

    _ensure_registered()
    snapshot = _dl.native_ready_family_resources(
        {TEMPORAL_CONCEPT: "mapping"},
        require_manual=True,
        include_reviewed_baselines=True,
    )
    mapping = (
        _mapping_from_resources(snapshot[TEMPORAL_CONCEPT], _TEMPORAL_IDS)
        if snapshot is not None else None
    )
    if not mapping:
        return None
    return frozenset(form.casefold() for form in mapping["modified_time"])


__all__ = [
    "AUDIT_CONCEPT",
    "CONCEPTS",
    "FIELD_CONCEPT",
    "GATE_CONCEPTS",
    "LABEL_CONCEPT",
    "RELEVANCE_GENERIC_CONCEPT",
    "RELEVANCE_ORGANIZATION_TYPE_CONCEPT",
    "RELEVANCE_PERSON_TYPE_CONCEPT",
    "MESSAGE_EVENT_COMMON_FIELD_IDS",
    "MESSAGE_EVENT_DEFAULT_FIELD_IDS",
    "MESSAGE_EVENT_FOCUSED_FIELD_IDS",
    "MULTISOURCE_COMMON_FIELD_IDS",
    "MULTISOURCE_DEFAULT_FIELD_IDS",
    "ReconciliationLexicon",
    "SOURCE_CONCEPT",
    "TEMPORAL_CONCEPT",
    "family_kinds",
    "from_resources",
    "load",
    "register_all",
    "temporal_aliases",
]
