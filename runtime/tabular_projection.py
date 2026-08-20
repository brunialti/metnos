# SPDX-License-Identifier: AGPL-3.0-only
"""Projection of structured records into user-labelled tabular columns.

The labels shown in a spreadsheet are presentation text; record keys are a
machine contract.  Treating the two lists as positional peers can therefore
put valid values under false headings.  This module compiles an explicit,
deterministic projection and fails closed when the relationship is ambiguous.

Two inputs are supported:

* ``column_specs`` is the language-independent contract.  Each item names a
  display ``header``, a canonical record ``source`` and an optional closed-set
  ``transform`` (``identity``, ``basename`` or ``dirname``).
* legacy ``columns`` are resolved through exact keys and a small semantic
  vocabulary.  Ordinals select repeated concepts (for example the first and
  second path); a filename may be derived from the corresponding path.
* optional producer ``field_roles`` disambiguate fields without relying on
  their names or dictionary position; display labels remain localized text.

There is deliberately no positional whole-record fallback.  An unresolved
header is an error, because an incomplete sheet is safer than a plausible but
mislabelled one.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import ntpath
import posixpath
import re
import unicodedata
from typing import Any, Iterable


_TRANSFORMS = frozenset({"identity", "basename", "dirname"})

# Closed semantic vocabulary for legacy/free-form headers.  This is not tied
# to any workflow or query: concepts describe reusable tabular field families.
_CONCEPT_ALIASES: dict[str, frozenset[str]] = {
    "path": frozenset({
        "path", "paths", "percorso", "percorsi", "filepath", "filepaths",
        "file_path", "file_paths", "image_path", "local_path",
    }),
    "directory": frozenset({
        "directory", "directories", "folder", "folders", "cartella",
        "cartelle", "directorio", "directorios", "carpeta", "carpetas",
        "ordner",
    }),
    "name": frozenset({
        "name", "names", "nome", "nomi", "filename", "filenames",
        "file_name", "file_names", "basename", "title", "titles",
        "titolo", "titoli",
    }),
    "description": frozenset({
        "description", "descriptions", "descrizione", "descrizioni",
        "desc", "caption", "captions",
    }),
    "size": frozenset({
        "size", "sizes", "size_bytes", "bytes", "dimensione", "dimensioni",
    }),
    "hash": frozenset({
        "hash", "hashes", "sha", "sha256", "checksum", "checksums",
        "digest", "digests", "impronta", "impronte",
    }),
    "score": frozenset({
        "score", "scores", "punteggio", "punteggi", "relevance",
        "rilevanza", "confidence", "confidenza",
    }),
    "keywords": frozenset({
        "keywords", "keyword", "parole_chiave", "tags", "tag",
    }),
    "date": frozenset({
        "date", "dates", "data", "datetime", "timestamp", "created_at",
        "updated_at", "modified_at", "mtime",
    }),
    "domain": frozenset({
        "domain", "domains", "dominio", "domini",
    }),
    "origin": frozenset({
        "origin", "origins", "original", "originals", "origine", "origini",
        "originale", "originali", "source", "sources", "sorgente",
        "sorgenti",
    }),
    "duplicate": frozenset({
        "duplicate", "duplicates", "duplicato", "duplicati", "copia",
        "copie", "copy", "copies", "duplicado", "duplicados", "duplikat",
        "duplikate",
    }),
    "url": frozenset({
        "url", "urls", "link", "links", "web_url", "web_view_url",
    }),
    "count": frozenset({
        "count", "counts", "conteggio", "numero", "total", "totale",
    }),
}

_ORDINAL_WORDS = {
    "first": 1, "one": 1, "primo": 1, "prima": 1,
    "second": 2, "two": 2, "secondo": 2, "seconda": 2,
    "third": 3, "three": 3, "terzo": 3, "terza": 3,
    "fourth": 4, "four": 4, "quarto": 4, "quarta": 4,
    "fifth": 5, "five": 5, "quinto": 5, "quinta": 5,
}


class TabularProjectionError(ValueError):
    """Raised when a user-facing header cannot be mapped without guessing."""

    def __init__(self, unresolved: Iterable[str], fields: Iterable[str],
                 reason: str = "ambiguous_or_missing") -> None:
        self.unresolved = tuple(str(item) for item in unresolved)
        self.fields = tuple(str(item) for item in fields)
        self.reason = reason
        super().__init__(
            f"{reason}: columns={list(self.unresolved)!r}, "
            f"fields={list(self.fields)!r}"
        )


@dataclass(frozen=True)
class ColumnProjection:
    header: str
    source: str
    transform: str = "identity"


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "_".join(re.findall(r"[^\W_]+", text, re.UNICODE))


def _tokens(value: Any) -> tuple[str, ...]:
    normalized = _normalize(value)
    return tuple(token for token in normalized.split("_") if token)


def _concepts(value: Any) -> set[str]:
    normalized = _normalize(value)
    token_set = set(_tokens(value))
    found: set[str] = set()
    for concept, aliases in _CONCEPT_ALIASES.items():
        normalized_aliases = {_normalize(alias) for alias in aliases}
        if normalized in normalized_aliases or token_set & normalized_aliases:
            found.add(concept)
    return found


def _ordinal(value: Any) -> int | None:
    for token in _tokens(value):
        if token.isdigit() and int(token) > 0:
            return int(token)
        if token in _ORDINAL_WORDS:
            return _ORDINAL_WORDS[token]
    return None


def _usable(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _sample_values(entries: list[dict], field: str, limit: int = 12) -> list:
    values = []
    for entry in entries:
        value = entry.get(field)
        if _usable(value):
            values.append(value)
        if len(values) >= limit:
            break
    return values


def _looks_like_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        return False
    return ("/" in text or "\\" in text
            or bool(re.match(r"^[A-Za-z]:$", text)))


def _looks_like_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"(?:[A-Fa-f0-9]{32}|[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})",
                     value.strip())
    )


def _declared_field_roles(field_roles: Iterable[dict] | None
                          ) -> dict[str, set[str]]:
    """Normalize producer-declared field semantics, independent of key names."""
    out: dict[str, set[str]] = {}
    for item in field_roles or []:
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        roles = item.get("roles")
        if not isinstance(field, str) or not field or not isinstance(roles, list):
            continue
        normalized = {
            _normalize(role) for role in roles
            if isinstance(role, str) and _normalize(role) in _CONCEPT_ALIASES
        }
        if normalized:
            out.setdefault(field, set()).update(normalized)
    return out


def _field_concept_strength(entries: list[dict], field: str,
                            declared_roles: dict[str, set[str]]
                            ) -> dict[str, int]:
    strengths = {concept: 3 for concept in _concepts(field)}
    for concept in declared_roles.get(field, set()):
        strengths[concept] = 5
    samples = _sample_values(entries, field)
    if samples and sum(_looks_like_path(v) for v in samples) * 2 >= len(samples):
        strengths["path"] = max(strengths.get("path", 0), 1)
    if samples and sum(_looks_like_hash(v) for v in samples) * 2 >= len(samples):
        strengths["hash"] = max(strengths.get("hash", 0), 1)
    return strengths


def _fields(entries: list[dict]) -> list[str]:
    out: list[str] = []
    for entry in entries[:50]:
        for raw in entry.keys():
            field = str(raw)
            if not field.startswith("_") and field not in out:
                out.append(field)
    return out


def _choose(candidates: list[tuple[str, int]], ordinal: int | None,
            used: set[str]) -> str | None:
    if ordinal is not None:
        return candidates[ordinal - 1][0] if ordinal <= len(candidates) else None
    if len(candidates) == 1:
        return candidates[0][0]
    if not candidates:
        return None
    best_strength = max(strength for _, strength in candidates)
    strongest = [field for field, strength in candidates
                 if strength == best_strength]
    if len(strongest) == 1:
        return strongest[0]
    unused = [field for field in strongest if field not in used]
    return unused[0] if len(unused) == 1 else None


def _legacy_projection(entries: list[dict], columns: list[str],
                       fields: list[str], field_roles: Iterable[dict] | None
                       ) -> list[ColumnProjection]:
    normalized_fields = {_normalize(field): field for field in fields}
    populated = {field: bool(_sample_values(entries, field)) for field in fields}
    declared_roles = _declared_field_roles(field_roles)
    strengths = {field: _field_concept_strength(
        entries, field, declared_roles) for field in fields}
    projections: list[ColumnProjection] = []
    unresolved: list[str] = []
    used: set[str] = set()

    for header in columns:
        normalized = _normalize(header)
        exact = normalized_fields.get(normalized)
        # Planner-created placeholder aliases can coexist with the populated
        # canonical field.  An empty exact key must not mask real data under a
        # semantic sibling (for example ``domini=""`` and ``dominio="files"``).
        if exact is not None and populated.get(exact):
            projections.append(ColumnProjection(header, exact))
            used.add(exact)
            continue

        concepts = _concepts(header)
        wants_directory = "directory" in concepts
        if wants_directory:
            concepts = (concepts - {"directory"}) | {"path"}
        ordinal = _ordinal(header)
        chosen: str | None = None
        transform = "identity"

        # A requested filename can be materialized from the corresponding
        # path when the producer exposes only paths.  This is a derivation,
        # not a guessed source value, and works for POSIX and Windows paths.
        if "name" in concepts:
            name_candidates = [
                (field, evidence.get("name", 0))
                for field, evidence in strengths.items()
                if evidence.get("name") and populated.get(field)
            ]
            chosen = _choose(name_candidates, ordinal, used)
            if chosen is None and not name_candidates:
                path_candidates = [
                    (field, evidence.get("path", 0))
                    for field, evidence in strengths.items()
                    if evidence.get("path") and populated.get(field)
                ]
                chosen = _choose(path_candidates, ordinal, set())
                transform = "basename" if chosen is not None else "identity"

        if chosen is None:
            # Prefer the concept with the strongest unique evidence.  If a
            # label expresses two incompatible concepts, ambiguity is honest.
            selections: list[tuple[str, str, int]] = []
            for concept in sorted(concepts - {"name"}):
                candidates = [
                    (field, evidence.get(concept, 0))
                    for field, evidence in strengths.items()
                    if evidence.get(concept) and populated.get(field)
                ]
                candidate = _choose(candidates, ordinal, used)
                if candidate is not None:
                    selections.append(
                        (candidate, concept, strengths[candidate][concept]))
            unique_sources = {source for source, _, _ in selections}
            if len(unique_sources) == 1:
                chosen = selections[0][0]
            elif selections:
                best = max(score for _, _, score in selections)
                top = {source for source, _, score in selections if score == best}
                if len(top) == 1:
                    chosen = next(iter(top))

        if chosen is None:
            unresolved.append(header)
            continue
        if wants_directory:
            transform = "dirname"
        projections.append(ColumnProjection(header, chosen, transform))
        used.add(chosen)

    if unresolved:
        raise TabularProjectionError(unresolved, fields)
    return projections


def compile_projection(entries: list[dict], columns: Iterable[str] | None = None,
                       column_specs: Iterable[dict] | None = None,
                       field_roles: Iterable[dict] | None = None
                       ) -> list[ColumnProjection]:
    """Compile a safe column projection for ``entries``.

    Explicit specs take precedence.  If they are absent, legacy headers are
    resolved semantically.  The function never falls back to dict order.
    """
    fields = _fields(entries)
    specs = list(column_specs or [])
    if specs:
        projections: list[ColumnProjection] = []
        invalid: list[str] = []
        for index, spec in enumerate(specs, start=1):
            if not isinstance(spec, dict):
                invalid.append(f"#{index}")
                continue
            header = str(spec.get("header") or "").strip()
            source = str(spec.get("source") or "").strip()
            transform = str(spec.get("transform") or "identity").strip().lower()
            if (not header or source not in fields or transform not in _TRANSFORMS):
                invalid.append(header or f"#{index}")
                continue
            projections.append(ColumnProjection(header, source, transform))
        if invalid:
            raise TabularProjectionError(invalid, fields, "invalid_column_specs")
        return projections

    headers = [str(column).strip() for column in (columns or [])
               if isinstance(column, str) and column.strip()]
    if not headers:
        return [ColumnProjection(field, field) for field in fields]
    return _legacy_projection(entries, headers, fields, field_roles)


def _transform(value: Any, operation: str) -> Any:
    if operation == "identity":
        return value
    text = "" if value is None else str(value)
    path_module = ntpath if "\\" in text else posixpath
    return (path_module.basename(text) if operation == "basename"
            else path_module.dirname(text))


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    return value


def project_entries(entries: Iterable[dict], columns: Iterable[str] | None = None,
                    column_specs: Iterable[dict] | None = None,
                    field_roles: Iterable[dict] | None = None) -> list[list]:
    """Return ``[header, *data_rows]`` using a compiled safe projection."""
    records = [entry for entry in (entries or []) if isinstance(entry, dict)]
    projection = compile_projection(
        records, columns, column_specs, field_roles)
    if not projection:
        return []
    rows: list[list] = [[column.header for column in projection]]
    for entry in records:
        rows.append([
            _cell(_transform(entry.get(column.source), column.transform))
            for column in projection
        ])
    return rows


__all__ = [
    "ColumnProjection", "TabularProjectionError", "compile_projection",
    "project_entries",
]
