"""Semantic retrieval for Tutor cards using the admitted local vector index."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import re

import numpy as np

from .cards import Card
from .catalog import (
    VectorIndex,
    load_knowledge_units,
    load_knowledge_vector_index,
    load_vector_index,
)
from .sources import KnowledgeUnit


def _bounded_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, value))


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    card: Card
    score: float
    margin: float


@dataclass(frozen=True, slots=True)
class SourceHit:
    source_type: str
    source_id: str
    lang: str
    score: float
    card: Card | None = None
    unit: KnowledgeUnit | None = None


@dataclass(frozen=True, slots=True)
class SemanticContext:
    hits: tuple[SourceHit, ...]
    top_score: float
    restricted: bool = False


def _query_vector(text: str, dimension: int, embedder=None) -> np.ndarray:
    if embedder is None:
        from virt import get_local_embedder
        embedder = get_local_embedder("text")
    # Lato QUERY dell'embedder: per i modelli simmetrici (BGE) coincide con
    # embed_texts, per quelli instruction-aware (Qwen) applica il prefisso
    # di istruzione. I documenti restano codificati nudi alla compilazione.
    query_vector = np.asarray([embedder.embed_query(text)], dtype=np.float32)
    if query_vector.shape != (1, dimension):
        raise ValueError("invalid Tutor query embedding shape")
    if not np.isfinite(query_vector).all():
        raise ValueError("non-finite Tutor query embedding")
    norm = float(np.linalg.norm(query_vector[0]))
    if norm <= 1e-8:
        raise ValueError("zero Tutor query embedding")
    return query_vector[0] / norm


def _language_order(lang: str) -> tuple[str, ...]:
    requested = str(lang or "en").strip().lower().replace("_", "-")
    base = requested.split("-", 1)[0]
    return tuple(dict.fromkeys((requested, base, "en")))


def _preferred_rows(
        refs: tuple[tuple[str, str], ...],
        concept_for_id,
        lang: str,
) -> tuple[int, ...]:
    """Choose a translation independently for every semantic concept."""

    grouped: dict[str, list[tuple[int, str]]] = {}
    for row, (item_id, row_lang) in enumerate(refs):
        grouped.setdefault(concept_for_id(item_id), []).append(
            (row, str(row_lang).lower()))
    preferred = _language_order(lang)
    selected: list[int] = []
    for concept in sorted(grouped):
        choices = grouped[concept]
        by_lang = {row_lang: row for row, row_lang in choices}
        row = next((by_lang[candidate] for candidate in preferred
                    if candidate in by_lang), None)
        if row is None:
            row = min(choices, key=lambda item: (item[1], item[0]))[0]
        selected.append(row)
    return tuple(selected)


_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _lexical_tokens(text: str) -> set[str]:
    """Language-neutral lexical signal derived from content, not a phrase list."""

    tokens = set()
    for token in _WORD.findall(str(text or "").casefold()):
        if len(token) < 3:
            continue
        tokens.add(token)
        if len(token) >= 6:
            tokens.add(token[:5])
    return tokens


def retrieve_sources(
        query: str,
        lang: str,
        audience: str,
        *,
        cards: tuple[Card, ...],
        card_index: VectorIndex | None = None,
        units: tuple[KnowledgeUnit, ...] | None = None,
        knowledge_index: VectorIndex | None = None,
        embedder=None,
        minimum_score: float | None = None,
        top_k: int = 16,
        explain: dict | None = None,
) -> SemanticContext | None:
    """Retrieve one bounded context across cards and the dynamic F2 corpus.

    Cards are high-authority sources in the same ranking, not a separate
    answer path.  Audience is checked before any source body is returned.  A
    restricted top result yields only a closed signal and never reaches the
    composer.

    ``explain`` is a measurement hook: when a dict is passed, the full ranked
    candidate list with adjusted scores and the effective policy values are
    recorded there.  It never changes selection and exists so analysis
    harnesses observe the real ranking instead of re-implementing it.
    """

    text = str(query or "").strip()
    if not text:
        return None
    units = units if units is not None else load_knowledge_units()
    knowledge_index = knowledge_index or load_knowledge_vector_index()
    if cards:
        card_index = card_index or load_vector_index()
        if card_index.dimension != knowledge_index.dimension:
            raise ValueError("Tutor semantic indexes use different dimensions")
    normalized = _query_vector(text, knowledge_index.dimension, embedder)
    card_by_id = {card.card_id: card for card in cards}
    unit_by_id = {unit.unit_id: unit for unit in units}
    candidates: list[SourceHit] = []

    for row in _preferred_rows(
            card_index.refs if cards and card_index is not None else (),
            lambda item_id: item_id, lang):
        card_id, row_lang = card_index.refs[row]
        card = card_by_id.get(card_id)
        if card is None:
            raise ValueError("Tutor vector references an unknown card")
        candidates.append(SourceHit(
            source_type="card", source_id=card_id, lang=row_lang,
            score=float(card_index.matrix[row] @ normalized), card=card,
        ))

    def knowledge_concept(unit_id: str) -> str:
        unit = unit_by_id.get(unit_id)
        if unit is None:
            raise ValueError("Tutor vector references an unknown knowledge unit")
        return unit.concept_id

    for row in _preferred_rows(knowledge_index.refs, knowledge_concept, lang):
        unit_id, row_lang = knowledge_index.refs[row]
        unit = unit_by_id.get(unit_id)
        if unit is None:
            raise ValueError("Tutor vector references an unknown knowledge unit")
        candidates.append(SourceHit(
            source_type="knowledge", source_id=unit_id, lang=row_lang,
            score=float(knowledge_index.matrix[row] @ normalized), unit=unit,
        ))
    if not candidates:
        return None

    query_tokens = _lexical_tokens(text)
    candidate_tokens: dict[tuple[str, str], set[str]] = {}
    candidate_title_tokens: dict[tuple[str, str], set[str]] = {}
    document_frequency: dict[str, int] = {}
    for hit in candidates:
        content = (
            " ".join((hit.card.title.get(hit.lang, ""),
                      hit.card.semantic.get(hit.lang, "")))
            if hit.card else
            " ".join((hit.unit.title, hit.unit.semantic, hit.unit.text))
        )
        tokens = _lexical_tokens(content)
        candidate_tokens[(hit.source_type, hit.source_id)] = tokens
        title = (
            hit.card.title.get(hit.lang, "")
            if hit.card else hit.unit.title
        )
        candidate_title_tokens[(hit.source_type, hit.source_id)] = (
            _lexical_tokens(title))
        for token in query_tokens & tokens:
            document_frequency[token] = document_frequency.get(token, 0) + 1
    population = max(1, len(candidates))
    query_weight = sum(
        math.log((population + 1) / (document_frequency.get(token, 0) + 1))
        for token in query_tokens
    ) or 1.0

    def lexical_score(hit: SourceHit) -> float:
        overlap = query_tokens & candidate_tokens[(hit.source_type, hit.source_id)]
        return sum(
            math.log((population + 1) / (document_frequency.get(token, 0) + 1))
            for token in overlap
        ) / query_weight

    def adjusted(hit: SourceHit) -> float:
        priority = hit.card.priority if hit.card else hit.unit.priority
        title_overlap = query_tokens & candidate_title_tokens[
            (hit.source_type, hit.source_id)]
        title_affinity = len(title_overlap) / max(1, len(query_tokens))
        # Authored titles are concise semantic evidence, especially for short
        # human questions whose dense embedding is otherwise under-specified.
        # Both signals are derived from the admitted source itself; no phrases,
        # synonyms, executor names, or topics are encoded here.
        return (
            hit.score
            + 0.05 * lexical_score(hit)
            + 0.08 * title_affinity
            + max(0, min(100, priority)) / 10000.0
        )

    ranked = sorted(candidates, key=adjusted, reverse=True)
    threshold = (
        # Calibrated on the human certification corpus after the public-doc
        # expansion: short, unambiguous explain queries rank the correct
        # aggregate source at 0.70+, while mode classification still excludes
        # actions before retrieval.  This is one corpus-wide confidence floor,
        # not a phrase, topic, or source-specific exception.
        _bounded_float("METNOS_TUTOR_KNOWLEDGE_MIN", 0.70)
        if minimum_score is None else float(minimum_score)
    )
    band = _bounded_float("METNOS_TUTOR_KNOWLEDGE_BAND", 0.06)
    if explain is not None:
        explain["threshold"] = threshold
        explain["band"] = band
        explain["ranked"] = tuple((hit, adjusted(hit)) for hit in ranked)
    if adjusted(ranked[0]) < threshold:
        return None

    visible = [
        hit for hit in ranked
        if (hit.card.visible_to(audience) if hit.card
            else hit.unit.visible_to(audience))
    ]
    # Un scarto per audience deve restare VISIBILE come tale. Legare il
    # segnale al solo ranked[0] lo rendeva silenzioso ogni volta che in testa
    # c'era una fonte pubblica: la risposta dichiarava allora l'ASSENZA di una
    # pagina che invece esiste e non e' autorizzata (§2.8, esito non
    # corrispondente alla realta'). Il segnale scatta se una fonte scartata
    # sarebbe entrata nella selezione, cioe' e' sopra la soglia assoluta e
    # dentro la banda della migliore visibile.
    dropped_in_band = [
        hit for hit in ranked
        if hit not in visible
        and adjusted(hit) >= threshold
        and (not visible or adjusted(hit) >= adjusted(visible[0]) - band)
    ]
    if not visible or dropped_in_band:
        top_dropped = (adjusted(dropped_in_band[0]) if dropped_in_band
                       else ranked[0].score)
        return SemanticContext((), top_dropped, restricted=True)

    top = adjusted(visible[0])
    selected: list[SourceHit] = []
    per_document: dict[str, int] = {}
    per_kind: dict[str, int] = {}
    for hit in visible:
        if adjusted(hit) < threshold or adjusted(hit) < top - band:
            break
        if hit.card:
            group = f"card:{hit.source_id}"
        else:
            # A published page is a sequence of independently authored,
            # titled sections; budgeting on the whole file would let two
            # strong sections evict a third, unrelated one.  The heading is
            # part of the admitted source structure, so the key stays
            # structural — no topic or phrase is encoded here.
            group = (hit.unit.source_ref.split("#", 1)[0], hit.unit.title)
        if hit.unit and hit.unit.source_kind == "capability_catalog":
            limit = 8
        else:
            limit = (
                2 if hit.unit and hit.unit.source_kind != "executor_manifest"
                else 1
            )
        if per_document.get(group, 0) >= limit:
            continue
        kind = hit.unit.source_kind if hit.unit else "curated_guide"
        # Argument-level manifest fragments are valuable evidence but must not
        # fill the whole context and crowd out an equally relevant workflow,
        # UI contract, or catalog inventory.  This is a source-shape budget,
        # independent of domain names and query wording.
        if kind == "executor_manifest_argument" and per_kind.get(kind, 0) >= 3:
            continue
        selected.append(hit)
        per_document[group] = per_document.get(group, 0) + 1
        per_kind[kind] = per_kind.get(kind, 0) + 1
        if len(selected) >= max(1, min(16, int(top_k))):
            break
    if not selected:
        return None
    if explain is not None:
        explain["walk_selected"] = tuple(
            hit.source_id for hit in selected)

    def _evict_weakest_outside(chosen: list[SourceHit],
                               keep: list[SourceHit]) -> bool:
        """Drop the weakest hit not in ``keep``; the walk appended in
        descending adjusted order, so the last outsider is the weakest."""
        for index in range(len(chosen) - 1, -1, -1):
            if chosen[index] not in keep:
                chosen.pop(index)
                return True
        return False

    # A document is authored as a sequence of sections, while vectors rank
    # each section independently.  Preserve a small amount of source coherence
    # around a semantically selected primary document so an introductory hit
    # does not crowd out its immediately adjacent procedure or constraint.
    # This expansion is structural (source identity + section ordinal), not a
    # topic or phrase rule, and remains inside the same audience-filtered set.
    primary = selected[0]
    if (primary.unit is not None
            and primary.unit.authority == "published_documentation"):
        primary_group = primary.unit.source_ref.split("#", 1)[0]

        def section_ordinal(hit: SourceHit) -> int:
            if hit.unit is None:
                return 0
            _separator, _hash, raw = hit.unit.source_ref.rpartition("#")
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0

        same_document = [
            hit for hit in visible
            if hit.unit is not None
            and hit.unit.authority == "published_documentation"
            and hit.unit.source_ref.split("#", 1)[0] == primary_group
        ]
        admitted = [
            hit for hit in selected
            if hit.unit is not None
            and hit.unit.source_ref.split("#", 1)[0] == primary_group
        ]
        anchors = tuple(section_ordinal(hit) for hit in admitted)
        neighbors = sorted(
            (hit for hit in same_document if hit not in selected),
            key=lambda hit: (
                min((abs(section_ordinal(hit) - anchor)
                     for anchor in anchors), default=10**9),
                -adjusted(hit),
                section_ordinal(hit),
            ),
        )
        maximum = max(1, min(16, int(top_k)))
        while len(admitted) < 3 and neighbors:
            neighbor = neighbors.pop(0)
            if len(selected) >= maximum:
                if not _evict_weakest_outside(selected, admitted):
                    break
            selected.append(neighbor)
            admitted.append(neighbor)

    # A capability inventory is one logical source split only for size: a
    # partially selected inventory would present itself as the whole.  When
    # any part of an inventory is selected, its remaining sibling parts join
    # so coverage-led composition sees the complete inventory.  Structural
    # rule (source identity), bounded by the same per-group budget as the
    # selection walk; no topic or phrase involved.
    maximum = max(1, min(16, int(top_k)))
    expanded_groups: set[str] = set()
    # Members of already-rejoined inventories are protected from the eviction
    # of later, weaker groups; and an anchor evicted by a previous expansion
    # no longer proves relevance, so it must not re-expand its own group.
    protected: list[SourceHit] = []
    for anchor in list(selected):
        if (anchor.unit is None
                or anchor.unit.source_kind != "capability_catalog"
                or anchor not in selected):
            continue
        group_ref = anchor.unit.source_ref.split("#", 1)[0]
        if group_ref in expanded_groups:
            continue
        expanded_groups.add(group_ref)
        in_group = [
            hit for hit in selected
            if hit.unit is not None
            and hit.unit.source_ref.split("#", 1)[0] == group_ref
        ]
        protected.extend(in_group)
        siblings = sorted(
            (hit for hit in visible
             if hit.unit is not None
             and hit.unit.source_ref.split("#", 1)[0] == group_ref
             and hit not in selected),
            key=adjusted, reverse=True,
        )
        if explain is not None:
            explain.setdefault("expansion_events", []).append(
                ("group", group_ref,
                 tuple(hit.source_id for hit in in_group),
                 tuple(hit.source_id for hit in siblings)))
        while len(in_group) < 8 and siblings:
            sibling = siblings.pop(0)
            if len(selected) >= maximum:
                if not _evict_weakest_outside(selected, protected):
                    if explain is not None:
                        explain["expansion_events"].append(
                            ("evict_failed", sibling.source_id))
                    break
            selected.append(sibling)
            in_group.append(sibling)
            protected.append(sibling)
    if explain is not None:
        explain["final_selected"] = tuple(
            hit.source_id for hit in selected)
    return SemanticContext(tuple(selected), top, restricted=False)


def retrieve(
        query: str,
        lang: str,
        *,
        cards: tuple[Card, ...],
        index: VectorIndex | None = None,
        embedder=None,
        minimum_score: float | None = None,
        minimum_margin: float | None = None,
) -> SemanticMatch | None:
    """Return one unambiguous card or no match.

    Thresholds are language-neutral confidence policy, not phrase routing.
    The query is embedded once; selection only compares dense vectors stored in
    the signed catalog.  Audience filtering deliberately happens afterwards so
    a restricted card cannot be replaced by a less relevant public card.
    """

    text = str(query or "").strip()
    if not text or not cards:
        return None
    vector_index = index or load_vector_index()
    if embedder is None:
        from virt import get_local_embedder
        embedder = get_local_embedder("text")
    query_vector = np.asarray([embedder.embed_query(text)], dtype=np.float32)
    if query_vector.shape != (1, vector_index.dimension):
        raise ValueError("invalid Tutor query embedding shape")
    if not np.isfinite(query_vector).all():
        raise ValueError("non-finite Tutor query embedding")
    norm = float(np.linalg.norm(query_vector[0]))
    if norm <= 1e-8:
        raise ValueError("zero Tutor query embedding")
    normalized = query_vector[0] / norm

    card_by_id = {card.card_id: card for card in cards}
    scores: dict[str, float] = {}
    for row in _preferred_rows(vector_index.refs, lambda item_id: item_id, lang):
        card_id, _row_lang = vector_index.refs[row]
        if card_id not in card_by_id:
            raise ValueError("Tutor vector references an unknown card")
        score = float(vector_index.matrix[row] @ normalized)
        scores[card_id] = max(scores.get(card_id, -1.0), score)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return None
    threshold = (
        _bounded_float("METNOS_TUTOR_SEMANTIC_MIN", 0.75)
        if minimum_score is None else float(minimum_score)
    )
    margin_threshold = (
        _bounded_float("METNOS_TUTOR_SEMANTIC_MARGIN", 0.025)
        if minimum_margin is None else float(minimum_margin)
    )
    top_id, top_score = ranked[0]
    margin = top_score - ranked[1][1] if len(ranked) > 1 else 1.0
    if top_score < threshold or margin < margin_threshold:
        return None
    return SemanticMatch(
        card=card_by_id[top_id], score=top_score, margin=margin)
