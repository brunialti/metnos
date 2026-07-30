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


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _authority_of(hit: "SourceHit") -> str:
    return hit.unit.authority if hit.unit is not None else "curated_guide"


def _document_group(hit: "SourceHit"):
    if hit.card:
        return f"card:{hit.source_id}"
    return (hit.unit.source_ref.split("#", 1)[0], hit.unit.title)


def _reserve_authorities(selected, visible, adjusted, threshold, top, band,
                         per_document, per_kind, limit_total,
                         pinned=()) -> None:
    """Una fonte per CLASSE DI AUTORITA' non resta a secco dentro la banda.

    La banda dichiara quali fonti sono equivalenti per pertinenza, ma il tetto
    di contesto tronca la banda: quando la banda contiene decine di sezioni di
    prosa, l'unita' di registro o l'inventario di capacita' che attestano il
    fatto restano fuori per differenze di centesimi, cioe' per rumore. Qui la
    banda ottiene rappresentanza: per ogni classe presente in banda e assente
    dalla selezione entra la sua migliore, sfrattando la piu' debole di una
    classe sovrarappresentata. Il primario non cambia mai e la classifica resta
    decrescente: e' una regola di copertura, non un riordino per autorita'.
    """

    in_band = [
        hit for hit in visible
        if adjusted(hit) >= threshold and adjusted(hit) >= top - band
    ]
    missing = [
        authority for authority in dict.fromkeys(
            _authority_of(hit) for hit in in_band)
        if authority not in {_authority_of(hit) for hit in selected}
    ]
    for authority in missing:
        counts: dict[str, int] = {}
        for hit in selected:
            key = _authority_of(hit)
            counts[key] = counts.get(key, 0) + 1
        candidate = next(
            (hit for hit in in_band
             if _authority_of(hit) == authority
             and hit not in selected
             and per_document.get(_document_group(hit), 0) < 1),
            None,
        )
        if candidate is None:
            continue
        if len(selected) >= limit_total:
            victim = next(
                (hit for hit in reversed(selected[1:])
                 if counts.get(_authority_of(hit), 0) > 1
                 and hit not in pinned),
                None,
            )
            if victim is None:
                continue
            selected.remove(victim)
            group = _document_group(victim)
            per_document[group] = max(0, per_document.get(group, 0) - 1)
            kind = victim.unit.source_kind if victim.unit else "curated_guide"
            per_kind[kind] = max(0, per_kind.get(kind, 0) - 1)
        position = next(
            (index for index, hit in enumerate(selected)
             if adjusted(hit) < adjusted(candidate)),
            len(selected),
        )
        selected.insert(position, candidate)
        group = _document_group(candidate)
        per_document[group] = per_document.get(group, 0) + 1
        kind = candidate.unit.source_kind if candidate.unit else "curated_guide"
        per_kind[kind] = per_kind.get(kind, 0) + 1


def _bounded_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, value))


def knowledge_band() -> float:
    """Return the single configured relevance band used by Tutor ranking."""

    return _bounded_float("METNOS_TUTOR_KNOWLEDGE_BAND", 0.06)


def association_adjusted_score(*, base: float, natural_top: float,
                               band: float, similarity: float,
                               strong: bool) -> float:
    """Apply the production association floor to one natural score.

    Keeping this primitive public to the Tutor package lets the permanent F4
    counterfactual execute the same arithmetic instead of asserting a desired
    rank as a constant.
    """

    # Learning may choose among sources already equivalent inside the natural
    # relevance band; it can never pull a distant source into that band. Only
    # repeated, strong confirmations may break a tie for primary.
    if float(base) < float(natural_top) - float(band):
        return float(base)
    floor = (
        natural_top + 0.0005 + similarity / 1_000_000.0
        if strong else natural_top - band / 2.0
    )
    return max(float(base), float(floor))


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


def _checkpoint(deadline_at: float) -> None:
    if deadline_at:
        from .deadline import remaining
        remaining(deadline_at)


def _query_vector(text: str, dimension: int, embedder=None,
                  *, deadline_at: float = 0.0) -> np.ndarray:
    _checkpoint(deadline_at)
    injected = embedder is not None
    if embedder is None:
        from virt import get_local_embedder
        embedder = get_local_embedder("text")
    # Lato QUERY dell'embedder: per i modelli simmetrici (BGE) coincide con
    # embed_texts, per quelli instruction-aware (Qwen) applica il prefisso
    # di istruzione. I documenti restano codificati nudi alla compilazione.
    bounded = getattr(embedder, "embed_query_bounded", None)
    if callable(bounded):
        from .deadline import remaining
        vector = bounded(text, timeout_s=remaining(deadline_at))
    elif injected:
        # Deterministic test/embedded providers are caller-owned and execute in
        # process; production providers must expose the bounded contract.
        vector = embedder.embed_query(text)
    else:
        raise RuntimeError("Tutor embedder lacks bounded query execution")
    query_vector = np.asarray([vector], dtype=np.float32)
    _checkpoint(deadline_at)
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
        companion_query: str = "",
        owner_user_id: str = "",
        required_source_ref: str = "",
        explain: dict | None = None,
        deadline_at: float = 0.0,
) -> SemanticContext | None:
    """Retrieve one bounded context across cards and the dynamic F2 corpus.

    Cards are high-authority sources in the same ranking, not a separate
    answer path.  Audience is checked before any source body is returned.  A
    restricted top result yields only a closed signal and never reaches the
    composer.

    ``companion_query`` e' una seconda formulazione della stessa domanda (la
    domanda precedente della conversazione, che risolve un riferimento
    ellittico). Ogni fonte prende il MASSIMO fra i punteggi delle due
    formulazioni: la domanda corrente resta sufficiente da sola quando lo e',
    e la fonte che risponde alla domanda risolta entra comunque. Non c'e'
    confronto fra i due punteggi di TESTA, che appartengono a testi di
    lunghezza diversa e quindi a scale diverse: quel confronto scartava in
    blocco la formulazione giusta (misurato: la superficie corretta passava
    dal rango 155 al 4 e veniva buttata via per 0,004 di margine).

    ``explain`` is a measurement hook: when a dict is passed, the full ranked
    candidate list with adjusted scores and the effective policy values are
    recorded there.  It never changes selection and exists so analysis
    harnesses observe the real ranking instead of re-implementing it.
    """

    text = str(query or "").strip()
    if not text:
        return None
    if not deadline_at:
        from .deadline import new_deadline
        deadline_at = new_deadline()
    _checkpoint(deadline_at)
    companion = str(companion_query or "").strip()
    probes = (text,) if not companion or companion == text else (text, companion)
    units = units if units is not None else load_knowledge_units()
    _checkpoint(deadline_at)
    source_ref = str(required_source_ref or "").split("#", 1)[0]
    source_bound_ids = {
        unit.unit_id for unit in units
        if source_ref and unit.source_ref.split("#", 1)[0] == source_ref
    }
    if source_ref and not source_bound_ids:
        return None
    knowledge_index = knowledge_index or load_knowledge_vector_index()
    _checkpoint(deadline_at)
    if cards and not source_ref:
        card_index = card_index or load_vector_index()
        if card_index.dimension != knowledge_index.dimension:
            raise ValueError("Tutor semantic indexes use different dimensions")
    vectors = tuple(
        _query_vector(
            probe, knowledge_index.dimension, embedder,
            deadline_at=deadline_at,
        )
        for probe in probes
    )
    _checkpoint(deadline_at)
    card_by_id = {card.card_id: card for card in cards}
    unit_by_id = {unit.unit_id: unit for unit in units}
    candidates: list[SourceHit] = []
    dense: dict[tuple[str, str], tuple[float, ...]] = {}

    for position, row in enumerate(_preferred_rows(
            card_index.refs
            if cards and card_index is not None and not source_ref else (),
            lambda item_id: item_id, lang)):
        if position % 128 == 0:
            _checkpoint(deadline_at)
        card_id, row_lang = card_index.refs[row]
        card = card_by_id.get(card_id)
        if card is None:
            raise ValueError("Tutor vector references an unknown card")
        scores = tuple(
            float(card_index.matrix[row] @ vector) for vector in vectors)
        dense[("card", card_id)] = scores
        candidates.append(SourceHit(
            source_type="card", source_id=card_id, lang=row_lang,
            score=max(scores), card=card,
        ))

    def knowledge_concept(unit_id: str) -> str:
        unit = unit_by_id.get(unit_id)
        if unit is None:
            raise ValueError("Tutor vector references an unknown knowledge unit")
        return unit.concept_id

    knowledge_rows = (
        tuple(
            row for row, (unit_id, _row_lang)
            in enumerate(knowledge_index.refs)
            if unit_id in source_bound_ids
        )
        if source_ref else
        _preferred_rows(knowledge_index.refs, knowledge_concept, lang)
    )
    for position, row in enumerate(knowledge_rows):
        if position % 128 == 0:
            _checkpoint(deadline_at)
        unit_id, row_lang = knowledge_index.refs[row]
        unit = unit_by_id.get(unit_id)
        if unit is None:
            raise ValueError("Tutor vector references an unknown knowledge unit")
        scores = tuple(
            float(knowledge_index.matrix[row] @ vector) for vector in vectors)
        dense[("knowledge", unit_id)] = scores
        candidates.append(SourceHit(
            source_type="knowledge", source_id=unit_id, lang=row_lang,
            score=max(scores), unit=unit,
        ))
    if not candidates:
        return None
    _checkpoint(deadline_at)

    candidate_tokens: dict[tuple[str, str], set[str]] = {}
    candidate_title_tokens: dict[tuple[str, str], set[str]] = {}
    for position, hit in enumerate(candidates):
        if position % 128 == 0:
            _checkpoint(deadline_at)
        content = (
            " ".join((hit.card.title.get(hit.lang, ""),
                      hit.card.semantic.get(hit.lang, "")))
            if hit.card else
            " ".join((hit.unit.title, hit.unit.semantic, hit.unit.text))
        )
        candidate_tokens[(hit.source_type, hit.source_id)] = (
            _lexical_tokens(content))
        title = (
            hit.card.title.get(hit.lang, "")
            if hit.card else hit.unit.title
        )
        candidate_title_tokens[(hit.source_type, hit.source_id)] = (
            _lexical_tokens(title))
    population = max(1, len(candidates))

    def _probe_bonus(probe: str):
        """Segnali lessicali di UNA formulazione: idf e affinita' di titolo.

        Frequenza documentale e peso della domanda dipendono dai termini di
        quella formulazione, quindi vanno ricalcolati per ognuna; il contenuto
        dei candidati e' invariante e si calcola una volta sola.
        """

        query_tokens = _lexical_tokens(probe)
        document_frequency: dict[str, int] = {}
        for key, tokens in candidate_tokens.items():
            for token in query_tokens & tokens:
                document_frequency[token] = document_frequency.get(token, 0) + 1

        def idf(token: str) -> float:
            return math.log(
                (population + 1) / (document_frequency.get(token, 0) + 1))

        query_weight = sum(idf(token) for token in query_tokens) or 1.0

        def bonus(key: tuple[str, str]) -> float:
            overlap = query_tokens & candidate_tokens[key]
            lexical = sum(idf(token) for token in overlap) / query_weight
            title_overlap = query_tokens & candidate_title_tokens[key]
            title_affinity = len(title_overlap) / max(1, len(query_tokens))
            # Authored titles are concise semantic evidence, especially for
            # short human questions whose dense embedding is otherwise
            # under-specified.  Both signals are derived from the admitted
            # source itself; no phrases, synonyms, executor names, or topics
            # are encoded here.
            return 0.05 * lexical + 0.08 * title_affinity

        return bonus

    bonuses = tuple(_probe_bonus(probe) for probe in probes)

    threshold = (
        # An exact identity match against the canonical publication registry
        # is stronger than semantic similarity.  Similarity ranks sections
        # *inside* that document but cannot reject or redirect the source.
        float("-inf")
        if source_ref else
        # Calibrated on the human certification corpus after the public-doc
        # expansion: short, unambiguous explain queries rank the correct
        # aggregate source at 0.70+, while mode classification still excludes
        # actions before retrieval.  This is one corpus-wide confidence floor,
        # not a phrase, topic, or source-specific exception.
        _bounded_float("METNOS_TUTOR_KNOWLEDGE_MIN", 0.70)
        if minimum_score is None else float(minimum_score)
    )
    band = knowledge_band()

    # Massimo per fonte fra le formulazioni: una fonte entra se e' pertinente
    # per ALMENO una di esse, senza che la media diluisca il segnale di quella
    # corta. Misurato contro la variante che ordina sulla sola domanda corrente
    # e ammette il compagno in coda (cert22/cert23, tre slot): il massimo per
    # fonte vince di uno e non introduce il regresso su
    # conversation-mailbox-credentials#2. Resta noto il prezzo: in tre
    # follow-up che cambiano argomento il compagno prende il primario
    # (RM-0003 §9-quater).
    def natural_adjusted(hit: SourceHit) -> float:
        priority = hit.card.priority if hit.card else hit.unit.priority
        key = (hit.source_type, hit.source_id)
        return max(
            score + bonus(key)
            for score, bonus in zip(dense[key], bonuses)
        ) + max(0, min(100, priority)) / 10000.0

    natural_scores = {
        (hit.source_type, hit.source_id): natural_adjusted(hit)
        for hit in candidates
    }
    _checkpoint(deadline_at)
    natural_top = max(natural_scores.values())
    association_matches: dict[str, tuple[float, bool, str]] = {}
    if owner_user_id:
        try:
            from .associations import match_with_evidence as match_associations
            association_matches = {
                unit_id: (similarity, strong, contributor_hash)
                for unit_id, similarity, strong, contributor_hash
                in match_associations(
                    vectors[0], knowledge_index.fingerprint,
                    {unit.unit_id: unit.content_hash for unit in units},
                    owner_user_id=owner_user_id,
                    audience=audience,
                )
            }
            _checkpoint(deadline_at)
        except Exception:
            # Learned hints are a removable retrieval layer; corruption or a
            # migration error cannot make grounded help unavailable.
            association_matches = {}

    def adjusted(hit: SourceHit) -> float:
        base = natural_scores[(hit.source_type, hit.source_id)]
        if hit.source_type != "knowledge":
            return base
        associated = association_matches.get(hit.source_id)
        if associated is None:
            return base
        similarity, strong, _contributor_hash = associated
        # A close learned neighbor enters the same relevance band; only a
        # strong (>= configured threshold) human-confirmed neighbor may become
        # primary.  This layer executes after EXPLAIN and is scoped per user.
        return association_adjusted_score(
            base=base,
            natural_top=natural_top,
            band=band,
            similarity=similarity,
            strong=strong,
        )

    ranked = sorted(candidates, key=adjusted, reverse=True)
    _checkpoint(deadline_at)
    if explain is not None:
        explain["threshold"] = threshold
        explain["band"] = band
        if source_ref:
            explain["identity_source_ref"] = source_ref
        explain["ranked"] = tuple((hit, adjusted(hit)) for hit in ranked)
        explain["query_vector"] = vectors[0].copy()
        explain["embedding_fingerprint"] = knowledge_index.fingerprint
        explain["association_matches"] = tuple(
            (unit_id, similarity, strong)
            for unit_id, (similarity, strong, _row_hash) in sorted(
                association_matches.items()))
    if adjusted(ranked[0]) < threshold:
        return None

    visible = [
        hit for hit in ranked
        if (hit.card.visible_to(audience) if hit.card
            else hit.unit.visible_to(audience))
    ]
    # Il rifiuto per autorizzazione e' onesto solo quando la risposta SAREBBE
    # STATA la fonte scartata, cioe' quando nessuna fonte visibile la eguaglia:
    # allora, e solo allora, tacere equivarrebbe a negare una pagina che esiste
    # (§2.8). Estendere il segnale a ogni scarto DENTRO LA BANDA lo fa scattare
    # quasi sempre, perche' le unita' del registro stanno di norma sopra la
    # soglia: misurato, 14 casi su 134 passavano da risposta fondata a rifiuto
    # (cert19 contro cert14) mentre erano fondati su una fonte visibile in
    # testa. Con una visibile prima, la risposta nasce da quella e non dichiara
    # alcuna assenza: il criterio resta il primato, valutato sullo STESSO
    # punteggio che ordina la classifica.
    if not visible or ranked[0] not in visible:
        return SemanticContext((), adjusted(ranked[0]), restricted=True)

    top = adjusted(visible[0])
    limit_total = max(1, min(16, int(top_k)))
    selected: list[SourceHit] = []
    per_document: dict[str, int] = {}
    per_kind: dict[str, int] = {}
    for hit in visible:
        if adjusted(hit) < threshold or adjusted(hit) < top - band:
            break
        # A published page is a sequence of independently authored, titled
        # sections; budgeting on the whole file would let two strong sections
        # evict a third, unrelated one.  The heading is part of the admitted
        # source structure, so the key stays structural — no topic or phrase
        # is encoded here.
        group = _document_group(hit)
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
        if len(selected) >= limit_total:
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
                if not _evict_weakest_outside(
                        selected, [primary, *admitted]):
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
    protected: list[SourceHit] = [primary]
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

    # Ultima, dopo le espansioni strutturali: la rappresentanza per autorita'
    # non deve spezzare un inventario ne' perdere una sezione adiacente, e a
    # sua volta nessuna espansione successiva puo' sfrattarla.
    if _flag("METNOS_TUTOR_AUTHORITY_QUOTA", True):
        _reserve_authorities(
            selected, visible, adjusted, threshold, top, band,
            per_document, per_kind, maximum, pinned=protected)
    if primary not in selected:
        raise RuntimeError("Tutor selection lost its semantic primary")
    selected = [primary, *sorted(
        (hit for hit in selected if hit is not primary),
        key=lambda hit: (-adjusted(hit), hit.source_id),
    )]
    if selected[0] is not primary:
        raise RuntimeError("Tutor semantic primary ordering invariant failed")
    if explain is not None:
        explain["final_selected"] = tuple(
            hit.source_id for hit in selected)
        explain["association_contributors"] = tuple(sorted({
            association_matches[hit.source_id][2]
            for hit in selected
            if hit.source_type == "knowledge"
            and hit.source_id in association_matches
            and adjusted(hit) > natural_scores[
                (hit.source_type, hit.source_id)] + 1e-12
        }))
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
