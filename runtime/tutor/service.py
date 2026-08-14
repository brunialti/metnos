"""Single fail-soft entry point for unified F2 retrieval and composition."""

from __future__ import annotations

import os
import re
import time
import tomllib

from logging_setup import get_logger
from messages import get as _msg

from .detect import classify
from .models import TutorAnswer, TutorEvidence, TutorRequest
from .render import render_card
from .semantic import SemanticContext, SourceHit, retrieve_sources

log = get_logger(__name__)

_PURPOSE_CUT = re.compile(
    r"\s+(?:PATTERN|NON|OUT|INPUT|OUTPUT):", re.IGNORECASE)
_PURPOSE_PREFIX = re.compile(r"^[^:]{1,20}:\s*")


class TutorContentError(ValueError):
    """An admitted source is structurally incomplete for its contract."""


def enabled() -> bool:
    return os.environ.get("METNOS_TUTOR", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _with_pending_note(answer: str, request: TutorRequest) -> str:
    if not request.has_pending:
        return answer
    return f"{answer.rstrip()}\n\n{_msg('MSG_TUTOR_PENDING_PRESERVED')}"


def _executor_purpose(executor, lang: str) -> str:
    """Read one localized, bounded purpose from the admitted manifest."""

    text = ""
    try:
        with executor.manifest_path.open("rb") as handle:
            description = tomllib.load(handle).get("description") or {}
        if isinstance(description, dict):
            text = str(
                description.get(lang) or description.get("en")
                or description.get("it") or next(iter(description.values()), "")
            )
        else:
            text = str(description)
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        text = str(getattr(executor, "description", "") or "")
    purpose = _PURPOSE_CUT.split(" ".join(text.split()), maxsplit=1)[0]
    purpose = _PURPOSE_PREFIX.sub("", purpose, count=1).strip(" .")
    return purpose[:180]


def _catalog_summary(card, lang: str, cards, audience: str) -> str:
    """Render a closed, localized capability inventory from the live loader."""

    selector = card.catalog
    if not selector:
        return ""
    from loader import load_catalog

    suffix = str(selector.get("suffix") or "")
    object_suffix = str(selector.get("object_suffix") or "")
    names = []
    overview_rows: list[tuple[str, object]] = []
    for executor in load_catalog():
        name = str(getattr(executor, "name", "") or "")
        membership = str(getattr(executor, "membership", "") or "")
        if not name:
            continue
        if selector.get("overview"):
            if selector.get("membership") and membership != selector["membership"]:
                continue
            names.append(name)
            overview_rows.append((name, executor))
            continue
        if suffix and not name.endswith(suffix):
            continue
        if object_suffix and not name.endswith(f"_{object_suffix}"):
            continue
        if selector.get("membership") and membership != selector["membership"]:
            continue
        names.append(name)
    names.sort()
    minimum = int(selector.get("expected_min") or 1)
    if len(names) < minimum:
        raise TutorContentError(
            "Tutor live catalog below the card completeness floor")

    if selector.get("overview"):
        heading = str(
            ((selector.get("heading") or {}).get(lang)) or ""
        ).format(count=len(names))
        areas: dict[str, dict] = {}
        providers: set[str] = set()
        for name, executor in overview_rows:
            parts = name.split("_", 2)
            if len(parts) >= 2:
                verb, obj = parts[0], parts[1]
                area = areas.setdefault(
                    obj, {"actions": set(), "names": set(), "purposes": {}})
                area["actions"].add(verb)
                area["names"].add(name)
                purpose = _executor_purpose(executor, lang)
                if purpose:
                    area["purposes"].setdefault(verb, []).append((
                        0 if name == f"{verb}_{obj}" else 1,
                        name,
                        purpose,
                    ))
            for capability in (getattr(executor, "capabilities", ()) or ()):
                if not isinstance(capability, dict):
                    continue
                if str(capability.get("name") or "") != "provider:access":
                    continue
                for hint in capability.get("hint") or ():
                    if str(hint).strip():
                        providers.add(str(hint).strip())
            from vocab import PROVIDER_SUFFIXES
            for marker in PROVIDER_SUFFIXES:
                if name.endswith(f"_{marker}"):
                    providers.add(marker.replace("_", "-"))
        priority_order = (
            "messages", "files", "dirs", "urls", "places", "location",
            "images", "events", "calendars", "contacts", "tasks", "sites",
            "processes", "credentials",
        )
        ordered_objects = sorted(
            areas,
            key=lambda obj: (
                priority_order.index(obj) if obj in priority_order
                else len(priority_order),
                obj,
            ),
        )
        lines = []
        if providers:
            provider_order = ("google-workspace", "google-photos", "github")
            ordered_providers = sorted(
                providers,
                key=lambda value: (
                    provider_order.index(value)
                    if value in provider_order else len(provider_order),
                    value,
                ),
            )
            lines.append("CATALOG_PROVIDERS: " + ", ".join(ordered_providers))
        lines.append("CATALOG_AREAS:")
        for obj in ordered_objects:
            actions = ",".join(sorted(areas[obj]["actions"]))
            purposes = []
            for action in sorted(areas[obj]["purposes"]):
                _rank, _name, purpose = sorted(
                    areas[obj]["purposes"][action])[0]
                purposes.append(f"{action}={purpose}")
            detail = " | ".join(purposes)
            lines.append(
                f"- object={obj}; actions={actions}; purposes={detail}")
        if not areas:
            raise TutorContentError(
                "Tutor overview has no admitted capability areas")
        return "\n".join(([heading] if heading else []) + lines)

    labels = ((selector.get("labels") or {}).get(lang) or {})
    verbs = ((selector.get("verbs") or {}).get(lang) or {})
    grouped: dict[str, list[str]] = {}
    for name in names:
        stem = name[:-len(suffix)] if suffix else name
        stem = stem.rstrip("_")
        verb, _, obj = stem.partition("_")
        if not verb or not obj or obj not in labels or verb not in verbs:
            continue
        grouped.setdefault(obj, []).append(str(verbs[verb]))
    lines = []
    for obj in sorted(grouped, key=lambda value: str(labels[value]).casefold()):
        actions = list(dict.fromkeys(grouped[obj]))
        lines.append(f"- **{labels[obj]}:** {', '.join(actions)}")
    if not lines:
        raise TutorContentError(
            "Tutor card could not describe the admitted inventory")
    heading = str(((selector.get("heading") or {}).get(lang)) or "").format(
        count=len(names))
    return "\n".join(([heading] if heading else []) + lines)


def _source_id(hit: SourceHit) -> str:
    if hit.card:
        return f"card:{hit.card.card_id}:{hit.lang}"
    return f"knowledge:{hit.unit.unit_id}"


def _document_outline(units, source_ref: str) -> str:
    """Render a bounded outline derived only from admitted catalog units."""

    base = str(source_ref or "").split("#", 1)[0]
    if not base:
        return ""

    def _ordinal(unit) -> int:
        raw = str(unit.source_ref or "").rpartition("#")[2]
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 10**9

    document_units = sorted(
        (unit for unit in units
         if unit.source_ref.split("#", 1)[0] == base),
        key=_ordinal,
    )
    if not document_units:
        return ""
    headings = []
    for unit in document_units:
        title = " ".join(str(unit.title or "").split())[:240]
        if title and title not in headings:
            headings.append(title)
        if len(headings) >= 80:
            break
    public_url = next(
        (unit.public_url for unit in document_units if unit.public_url), "")
    lines = [
        f"[DOCUMENT_IDENTITY source_ref={base}]",
        f"FILE_NAME: {base.rsplit('/', 1)[-1]}",
    ]
    if public_url:
        lines.append(f"PUBLIC_URL: {public_url}")
    lines.append("SECTION_OUTLINE:")
    lines.extend(f"- {heading}" for heading in headings)
    lines.append("[/DOCUMENT_IDENTITY]")
    return "\n".join(lines)[:8_000]


def _render_context(hit: SourceHit, *, cards, audience: str) -> str:
    """Render one admitted source with explicit trust metadata for the LLM."""

    if hit.card:
        rendered = render_card(
            hit.card,
            hit.lang,
            catalog_summary=_catalog_summary(
                hit.card, hit.lang, cards, audience),
        )
        authority = "curated_guide"
        title = hit.card.title[hit.lang]
    else:
        rendered = hit.unit.text
        authority = hit.unit.authority
        title = hit.unit.title
    source_kind = "curated_guide" if hit.card else hit.unit.source_kind
    public_url = ""
    if hit.unit is not None and hit.unit.public_url:
        public_url = f" public_url={hit.unit.public_url}"
    return (
        f"[SOURCE id={_source_id(hit)} authority={authority} "
        f"source_kind={source_kind}{public_url}]\n"
        f"TITLE: {title}\n{rendered}\n[/SOURCE]"
    )


_LEDGER_ROW = re.compile(
    r"^\s*-\s+(?P<name>[^:\[\n]+?)"
    r"(?:\s*\[(?P<actions>[^\]\n]+)\])?"
    r"(?:\s*:\s*(?P<detail>[^\n]+))?\s*$",
    re.MULTILINE,
)


def _surface_key(hit: SourceHit) -> str | None:
    """Surface key of a UI unit; ``None`` for every other kind of evidence."""

    unit = hit.unit
    if unit is None or unit.source_kind not in ("ui_surface", "ui_procedure"):
        return None
    reference = unit.source_ref.split(":")
    return reference[2] if len(reference) >= 3 else None


def _manifest_family(hit: SourceHit) -> str:
    unit = hit.unit
    if unit is None or unit.authority != "admitted_manifest":
        return ""
    parts = str(unit.source_ref or "").split(":")
    return parts[1] if len(parts) >= 2 and parts[0] == "manifest" else ""


def _coherent_manifest_scope(
        hits: tuple[SourceHit, ...], primary: SourceHit,
) -> tuple[SourceHit, ...]:
    """Bound operation help to one explicit manifest neighbourhood.

    Executor arguments are leaf evidence, while retrieved manuals may discuss
    a nearby but different pipeline.  When the primary is a complete admitted
    manifest, retain its leaves, complete manifests that explicitly cross-link
    it in their signed descriptions, the best published explanation, and one
    additional publication that names the primary executor.  This derives the
    neighbourhood entirely from source structure and authored cross-references;
    it contains no query words, domains, or executor-specific table.
    """

    unit = primary.unit
    if unit is None or unit.source_kind != "executor_manifest":
        return hits
    primary_family = _manifest_family(primary)
    if not primary_family:
        return hits

    roots = [primary]
    primary_text = str(unit.text or "").casefold()
    for hit in hits:
        if hit is primary or hit.unit is None:
            continue
        if hit.unit.source_kind != "executor_manifest":
            continue
        family = _manifest_family(hit)
        if not family:
            continue
        linked = (
            family.casefold() in primary_text
            or primary_family.casefold() in str(hit.unit.text or "").casefold()
        )
        if linked:
            roots.append(hit)
        if len(roots) >= 3:
            break
    admitted_families = {_manifest_family(hit) for hit in roots}

    first_document = next((
        hit for hit in hits
        if hit.unit is not None
        and hit.unit.authority == "published_documentation"
    ), None)
    selected: list[SourceHit] = []
    argument_counts: dict[str, int] = {}
    document_count = 0
    for hit in hits:
        candidate = hit.unit
        if hit in roots:
            selected.append(hit)
        elif candidate is None:
            continue
        elif candidate.source_kind == "executor_manifest_argument":
            family = _manifest_family(hit)
            if (family in admitted_families
                    and argument_counts.get(family, 0) < 2):
                selected.append(hit)
                argument_counts[family] = argument_counts.get(family, 0) + 1
        elif candidate.source_kind == "executor_manifest":
            continue
        elif candidate.authority == "published_documentation":
            explicitly_linked = primary_family.casefold() in (
                f"{candidate.title} {candidate.text}".casefold())
            if (document_count < 2
                    and (hit is first_document or explicitly_linked)):
                selected.append(hit)
                document_count += 1
        # Other source shapes are separate authorities.  Once a complete
        # manifest is primary they cannot expand its operational contract.
        if len(selected) >= 8:
            break
    return tuple(selected or (primary,))


def _ledger_scope(hits: tuple[SourceHit, ...],
                  primary: SourceHit) -> tuple[SourceHit, ...]:
    """Restringe le voci UI della checklist alla pagina PRIMARIA.

    Il ledger nasce da tutte le fonti strutturate selezionate: giusto per una
    panoramica, sbagliato quando una superficie entra solo come fonte
    secondaria per vicinanza semantica. Il correttore la trasformerebbe in una
    sezione obbligatoria anche se la domanda riguarda il documento primario
    (turno 1ab456aa: configurazione dell'embedder completata con la console
    delle proposte). Il corpo resta nel contesto e il compositore può usarlo
    quando è davvero pertinente; soltanto la checklist meccanica lo ignora.
    Regola strutturale sul primato e sull'identità della superficie, senza
    nomi di pagina, argomenti o frasi della domanda.
    """

    primary_key = _surface_key(primary)
    primary_manifest = (
        primary.unit is not None
        and primary.unit.source_kind == "executor_manifest"
    )
    # A secondary manifest is candidate evidence, not a mandatory topic.  The
    # completeness ledger may force only the manifest that won semantic
    # primacy; otherwise a focused question expands into every nearby tool.
    without_secondary_manifests = tuple(
        hit for hit in hits
        if not (hit.unit is not None
                and hit.unit.source_kind == "executor_manifest"
                and (not primary_manifest or hit is not primary))
    )
    if primary_key is None:
        return tuple(
            hit for hit in without_secondary_manifests
            if _surface_key(hit) is None
        )
    return tuple(
        hit for hit in without_secondary_manifests
        if _surface_key(hit) in (None, primary_key)
    )


def _coverage_items(hits: tuple[SourceHit, ...]) -> dict:
    """Build the structured completeness checklist from the selected sources.

    Every structured source admitted by retrieval contributes one checklist
    entry: capability rows, manifest purposes, and UI-surface contracts.  The
    checklist is derived from registries and authored structure, never from
    query wording, so the same rule serves overviews, domain questions, and
    focused operations alike.  The same items feed the rendered ledger given
    to the composer and the mechanical re-read that follows composition.
    """

    from vocab import PROVIDER_DISPLAY_NAMES

    areas: set[str] = set()
    providers: set[str] = set()
    operations: list[str] = []
    tools: list[str] = []
    surfaces: list[dict] = []
    seen_surfaces: set[str] = set()
    for hit in hits:
        if hit.unit is None:
            continue
        if hit.unit.source_kind == "executor_manifest":
            from .sources import _catalog_purpose
            # Manifest units prepend a neutral metadata sentence; the
            # substantive localized description follows the first period.
            description = hit.unit.text.split(". ", 1)[-1]
            purpose = _catalog_purpose(description) or hit.unit.title
            if purpose and purpose not in tools:
                tools.append(purpose)
            continue
        if hit.unit.source_kind in ("ui_surface", "ui_procedure"):
            reference = hit.unit.source_ref.split(":")
            if len(reference) >= 3:
                try:
                    from ui_surfaces import by_key
                    surface = by_key(reference[2])
                except StopIteration:
                    continue
                unit_lang = hit.unit.lang
                visible = tuple(surface.visible(unit_lang))
                controls = tuple(surface.controls(unit_lang))
                entry = (
                    f"{surface.label(unit_lang)} [{surface.route}]: "
                    + ", ".join(visible))
                if controls:
                    entry += "; controls: " + ", ".join(controls)
                stop_conditions = tuple(surface.stop_conditions(unit_lang))
                if stop_conditions:
                    entry += "; stop conditions attested by the source"
                if entry not in seen_surfaces:
                    seen_surfaces.add(entry)
                    surfaces.append({
                        "entry": entry,
                        "label": surface.label(unit_lang),
                        "route": surface.route,
                        "visible": visible,
                        "controls": controls,
                        "stop_conditions": stop_conditions,
                    })
            continue
        if hit.unit.source_kind != "capability_catalog":
            continue
        text = str(hit.unit.text or "")
        # Inventory rows are authored one per line by the capability
        # projection; the ledger reads that structure instead of re-parsing
        # localized prose.  Provider rows carry the action list after the
        # colon, area rows inside brackets.
        for match in _LEDGER_ROW.finditer(text):
            name = match.group("name").strip()
            if not name:
                continue
            areas.add(name)
            actions = ",".join(
                part.strip()
                for part in (match.group("actions")
                             or match.group("detail") or "").split(",")
                if part.strip())
            if match.group("actions") or match.group("detail"):
                operations.append(f"{name}: {actions}")
        # Provider identity comes from the canonical display registry, not
        # from parsing a localized sentence.
        providers.update(
            display for display in PROVIDER_DISPLAY_NAMES.values()
            if display in text
        )
    return {
        "providers": sorted(providers),
        "areas": sorted(areas),
        "operations": sorted(set(operations)),
        "tools": tools,
        "surfaces": surfaces,
    }


def _render_ledger(coverage: dict) -> str:
    """Render the checklist block appended to the composer context."""

    if not any(coverage.values()):
        return ""
    lines = [
        "[COVERAGE_LEDGER] Derived checklist: represent every listed area, "
        "provider, tool purpose, and surface at least once, translating "
        "labels naturally and hiding technical object syntax.",
    ]
    if coverage["providers"]:
        lines.append("providers=" + ", ".join(coverage["providers"]))
    if coverage["areas"]:
        lines.append("areas=" + ", ".join(coverage["areas"]))
    if coverage["operations"]:
        lines.append("operations=" + "; ".join(coverage["operations"]))
    if coverage["tools"]:
        lines.append("tools=" + "; ".join(coverage["tools"]))
    if coverage["surfaces"]:
        lines.append("surfaces=" + " | ".join(
            surface["entry"] for surface in coverage["surfaces"]))
    lines.append("[/COVERAGE_LEDGER]")
    return "\n".join(lines)


# Correttore di bozze deterministico (§7.9, zero LLM nella rilettura): la
# risposta composta viene riletta contro le stesse voci strutturate del
# ledger. Marker interni che non devono MAI raggiungere l'utente:
_INTERNAL_MARKERS = (
    "object=", "actions=", "executor=", "source_kind=", "from_step",
    "CATALOG_AREAS", "CATALOG_PROVIDERS",
)
_GAP_WORD = re.compile(r"[^\W\d_]+")


def _root(word: str) -> str:
    """Radice flessiva: parola senza la vocale finale (stessa forma dei
    pattern `tutor_gate.*`: prefisso + ``\\w*``)."""

    root = word.casefold()
    if len(root) >= 5 and root[-1] in "aeiouy":
        root = root[:-1]
    return root


def _root_hit(word: str, text: str) -> bool:
    # L'underscore e' un carattere di parola per le regex, quindi dentro un
    # identificatore composto (`events_empty`) non esiste confine prima della
    # seconda parola: la rilettura dichiarava mancante una voce che la
    # risposta conteneva alla lettera. Le etichette sono gia' spezzate sugli
    # underscore da `_GAP_WORD`; qui si allinea il testo, cosi' i due lati
    # vedono le stesse parole. Regola generale sugli identificatori.
    return re.search(
        r"\b" + re.escape(_root(word)) + r"\w*", text.replace("_", " "),
        re.IGNORECASE) is not None


def _content_words(label: str) -> list[str]:
    return [
        word for word in _GAP_WORD.findall(label)
        if len(word) >= 4
    ]


_SHORT_LABEL_WORDS = 3


def _label_covered(label: str, text: str, df: dict) -> bool:
    """Una voce di checklist e' rappresentata nella risposta?

    Etichetta CORTA (fino a tre parole): e' un nome esatto di campo o di
    controllo — «esegui ora», «nome visualizzato» — e vale solo per intero.
    Spezzarla in parole la dichiarerebbe coperta da un «eseguire» qualsiasi,
    e il buco resterebbe invisibile alla rilettura (misurato: zero dei gate
    falliti risultava fra i punti richiesti).

    Etichetta LUNGA (una frase di contenuti visibili): basta una sua parola
    DISTINTIVA, cioe' con radice presente in UNA sola voce della checklist
    (frequenza documentale interna): «impronta» identifica la finalita' hash,
    «file» e' condiviso da mezza checklist e non prova nulla.
    """

    import detection_lexicon as dl
    tokens = _GAP_WORD.findall(label)
    if tokens and len(tokens) <= _SHORT_LABEL_WORDS:
        # Ogni parola a livello di radice, tutte insieme: «riprova» resta
        # coperto da «riprovare», mentre «esegui ora» non lo e' da un
        # «eseguire» isolato.
        return all(_root_hit(token, text) for token in tokens)
    words = _content_words(label)
    if not words:
        return dl.match_any([label], text, mode="word")
    distinctive = [word for word in words if df.get(_root(word), 0) <= 1]
    return any(_root_hit(word, text) for word in (distinctive or words))


def _find_gaps(coverage: dict, text: str, lang: str) -> list[str]:
    """Rilettura meccanica della risposta contro il ledger (§7.9).

    Ogni voce strutturata deve essere rappresentata: percorso esatto delle
    superfici con i loro campi e controlli, aree e provider dell'inventario,
    finalita' dei tool, condizioni di arresto attestate; nessun marker
    interno. Ritorna l'elenco esplicito dei buchi per la ricomposizione.
    """

    from collections import Counter

    import detection_lexicon as dl
    gaps: list[str] = []
    low = text.casefold()
    labels: list[str] = [*coverage.get("areas", ()),
                         *coverage.get("tools", ())]
    for surface in coverage.get("surfaces", ()):
        labels.extend(surface["visible"])
        labels.extend(surface["controls"])
        labels.extend(surface["stop_conditions"])
    df: Counter = Counter()
    for label in labels:
        df.update({_root(word) for word in _content_words(label)})
    for marker in _INTERNAL_MARKERS:
        if marker.casefold() in low:
            gaps.append(f"remove the internal marker {marker!r} from the prose")
    for provider in coverage.get("providers", ()):
        if not dl.match_any([provider], text, mode="word"):
            gaps.append(f"mention the provider: {provider}")
    for area in coverage.get("areas", ()):
        if not _label_covered(area, text, df):
            gaps.append(f"cover the capability area: {area}")
    for purpose in coverage.get("tools", ()):
        if not _label_covered(purpose, text, df):
            gaps.append(f"state the tool purpose: {purpose}")
    for surface in coverage.get("surfaces", ()):
        if surface["route"].casefold() not in low:
            gaps.append(
                f"state the exact navigation path {surface['route']} "
                f"for {surface['label']}")
        for item in (*surface["visible"], *surface["controls"]):
            if not _label_covered(item, text, df):
                gaps.append(f"cover the {surface['label']} item: {item}")
        for condition in surface["stop_conditions"]:
            if not _label_covered(condition, text, df):
                # The condition itself is localized source evidence. No fixed
                # Italian/English lead or fallback is imposed on a new locale.
                gaps.append(
                    f"report the stop condition from {surface['label']}: "
                    f"{condition}")
    return gaps


def _revision_block(gaps: list[str]) -> str:
    """Elenco esplicito dei buchi appeso al contesto per l'UNICA
    ricomposizione (cap onesto: dopo, si consegna comunque)."""

    bullets = "\n".join(f"- {gap}" for gap in gaps)
    return (
        "[REVISION] A mechanical re-read found that the previous draft "
        "missed mandatory checklist items. Compose the complete answer "
        "again and integrate every item below naturally, without "
        "mentioning this note:\n" + bullets)


def _evidence(trace: dict, catalog_version: str,
              primary: SourceHit | None = None, *, eligible: bool = False
              ) -> TutorEvidence | None:
    """Build internal F4 evidence without retaining the clear-text query."""

    vector = trace.get("query_vector")
    fingerprint = str(trace.get("embedding_fingerprint") or "")
    if vector is None or not fingerprint or not catalog_version:
        return None
    try:
        values = tuple(float(value) for value in vector)
    except (TypeError, ValueError):
        return None
    if not values:
        return None
    source_id = _source_id(primary) if primary is not None else ""
    content_hash = (
        primary.unit.content_hash
        if primary is not None and primary.unit is not None else ""
    )
    return TutorEvidence(
        query_vector=values,
        embedding_fingerprint=fingerprint,
        catalog_version=catalog_version,
        primary_source_id=source_id,
        primary_content_hash=content_hash,
        eligible_for_association=bool(
            eligible and source_id.startswith("knowledge:") and content_hash),
        association_contributor_hashes=tuple(sorted({
            str(value) for value in trace.get(
                "association_contributors", ())
            if str(value)
        })),
    )


def _answer_live_observation(
        request: TutorRequest, *, query: str, lang: str, started: float,
        authority_deadline_at: float,
        request_deadline_at: float) -> TutorAnswer | None:
    """Answer through one signed view, or leave the ordinary runtime intact.

    ``OBSERVE`` is descriptive, not authority.  A request reaches a probe only
    after a context-free closed selector chooses one complete visible view and
    that view is found again in the verified catalog.  Every mismatch, missing
    facet, restriction, stale observation, or selector failure falls through
    to the planner; Tutor must never replace a richer operational executor with
    nearby documentation.
    """

    from .deadline import TutorDeadlineExceeded, remaining

    try:
        from .catalog import load_request_snapshot
        from .observation_views import (
            project_capsule,
            select_view,
            verify_semantic_coverage,
        )
        snapshot = load_request_snapshot()
        remaining(authority_deadline_at)
        selection = select_view(
            query=query, lang=lang, principal=request.principal,
            deadline_at=authority_deadline_at)
        if not selection.available or selection.view is None:
            return None
        view = selection.view
        coverage_ok, coverage_reason = verify_semantic_coverage(
            query=query,
            lang=lang,
            principal=request.principal,
            selected=view,
            snapshot=snapshot,
            deadline_at=authority_deadline_at,
        )
        if not coverage_ok:
            log.info("Tutor live observation declined reason=%s",
                     coverage_reason)
            return None

        from .semantic import _language_order
        requested = str(lang or "en").lower().split("-", 1)[0]
        language_order = _language_order(lang)
        candidates = tuple(
            unit for unit in snapshot.units
            if unit.observation_ref == view.view_id
            and unit.source_kind == "live_observation"
            and unit.visible_to(request.principal.audience)
        )
        unit = next(
            (item for candidate_lang in language_order for item in candidates
             if item.lang == candidate_lang),
            None,
        )
        if unit is None:
            return None
        # Authority is now complete.  Probing and composing are read-only
        # post-authority work and use the full request budget; keeping them in
        # the short admission window would turn harmless LLM contention into a
        # terminal Tutor response and suppress the ordinary runtime.
        remaining(authority_deadline_at)

        from .probes import (
            capsules_are_fresh,
            compact_for_composition,
            execute_probe_refs,
            render_capsules,
        )
        raw = execute_probe_refs(
            (view.probe_id,),
            principal=request.principal,
            lang=lang,
            injected=request.probes,
            deadline_at=request_deadline_at,
        )
        if len(raw) != 1:
            return None
        capsule = project_capsule(view, raw[0])
        if capsule.status not in {"ok", "partial"}:
            return None
        if not capsules_are_fresh((capsule,)):
            return None
        capsule = compact_for_composition((capsule,))[0]
        remaining(request_deadline_at)

        hit = SourceHit(
            source_type="knowledge", source_id=unit.unit_id,
            lang=unit.lang, score=1.0, unit=unit)
        source_ids = (
            _source_id(hit), f"view:{view.view_id}",
            f"probe:{view.probe_id}",
        )
        rendered_context = "\n\n".join((
            _render_context(
                hit, cards=snapshot.cards,
                audience=request.principal.audience),
            render_capsules((capsule,)),
        ))
        from .compose import compose_answer
        composition = compose_answer(
            query=query,
            context=rendered_context,
            lang=lang,
            source_ids=source_ids,
            # Context may resolve language, but can never broaden a live view.
            # The selector and composer therefore see only the current request.
            conversation_context="",
            delivery_channel=request.principal.channel,
            deadline_at=request_deadline_at,
        )
        if composition.status != "answer" or not composition.text:
            # A live observation is an optional, read-only acceleration.  It
            # has performed no effect that would make ordinary runtime fallback
            # unsafe, so composition failure must not steal the user's turn.
            log.info("Tutor live observation declined reason=composer_%s",
                     composition.status)
            return None
        gap_reason = (
            "live_observation_incomplete"
            if capsule.status == "partial" else
            "weak_language"
            if unit.lang.lower().split("-", 1)[0] != requested else ""
        )
        return TutorAnswer(
            esito="fondata",
            answer_md=_with_pending_note(composition.text, request),
            source_ids=source_ids,
            score_band="high",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            detection="semantic_live_observation",
            probe_statuses=((view.probe_id, capsule.status),),
            gap_reason=gap_reason,
            # Live observations are intentionally never F4 association
            # evidence: a future static query cannot inherit data authority.
            evidence=None,
        )
    except TutorDeadlineExceeded:
        # No side effect was performed.  Exhausting either the admission phase
        # or the read-only delivery phase leaves the request to the ordinary
        # runtime instead of producing a terminal Tutor error.
        log.info("Tutor live observation declined reason=deadline")
        return None
    except Exception:
        log.warning("Tutor live observation declined", exc_info=True)
        return None


def answer_request(request: TutorRequest) -> TutorAnswer | None:
    """Return an answer only for a high-confidence help request.

    Technical failures before a semantic mode or a complete live view has
    acquired Tutor authority return ``None`` and leave the existing runtime
    intact. Failures after a request is positively established as help are
    terminal and localized by the Tutor/channel boundary.
    """

    if not enabled():
        return None
    started = time.monotonic()
    from .deadline import (
        TutorDeadlineExceeded,
        mode_budget_s,
        new_deadline,
        phase_deadline,
        remaining,
    )
    deadline_at = request.deadline_at or new_deadline()
    remaining(deadline_at)
    try:
        detection = classify(request.query_redacted)
    except Exception:
        log.warning("tutor detection unavailable", exc_info=True)
        return None
    remaining(deadline_at)
    if detection.reason in {"sensitive_shape", "control_command"}:
        return None
    import config
    lang = (request.lang or config.DEFAULT_LANG).lower().split("-", 1)[0]
    from published_docs import resolve_reference
    document_reference = resolve_reference(
        request.query_redacted, lang=lang)
    remaining(deadline_at)
    from .mode import classify_mode_decision
    # Mode, mixed segmentation, and live-view admission form one
    # pre-authority transaction. Sharing a single short deadline prevents a
    # sequence of individually bounded classifiers from consuming the whole
    # HTTP budget before the ordinary runtime gets a chance to act.
    authority_deadline_at = phase_deadline(deadline_at, mode_budget_s())
    try:
        mode_decision = classify_mode_decision(
            request.query_redacted,
            lang,
            conversation_context=request.conversation_context,
            deadline_at=authority_deadline_at,
        )
    except TutorDeadlineExceeded:
        log.info("Tutor mode declined reason=authority_deadline")
        return None
    if not mode_decision.available:
        log.warning(
            "Tutor mode unavailable reason=%s", mode_decision.reason)
        return None
    # Reading an exact document admitted by the publication registry is
    # static help even though the current-query classifier correctly treats
    # arbitrary file contents as an observation.  Source identity is applied
    # only here, after mode classification; it never enters the classifier.
    if document_reference is not None and mode_decision.mode == "OBSERVE":
        from dataclasses import replace
        mode_decision = replace(mode_decision, mode="EXPLAIN")
    mode = mode_decision.mode
    working_query = request.query_redacted
    handoff_query = ""
    if mode == "MIXED":
        if request.has_pending:
            return TutorAnswer(
                esito="clarification",
                answer_md=_with_pending_note(
                    _msg("MSG_TUTOR_MIXED_CLARIFY"), request),
                score_band="high",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                detection="semantic_mixed",
                gap_reason="mode_ambiguity",
            )
        try:
            from .handoff import MixedSplitUnavailable, split_mixed_query
            split = split_mixed_query(
                request.query_redacted,
                lang,
                conversation_context=request.conversation_context,
                deadline_at=authority_deadline_at,
            )
        except TutorDeadlineExceeded:
            log.info("Tutor mixed segmentation declined reason=authority_deadline")
            return None
        except MixedSplitUnavailable:
            log.warning("Tutor mixed segmentation unavailable", exc_info=True)
            return None
        except Exception:
            log.warning("Tutor mixed segmentation failed", exc_info=True)
            return None
        if split is None:
            # A MIXED request not proven to be exactly one explanation plus
            # one operational clause belongs to the ordinary compound
            # planner. Tutor must not steal an OBSERVE+ACT request or force a
            # clarification it cannot resolve.
            return None
        working_query = split.explanation
        handoff_query = split.action
        # CURRENT/FOLLOWUP belong to the exact clause being answered.  The
        # whole-query MIXED decision may describe only the action clause and
        # must never authorize a live probe for a static explanation.
        mode_decision = split.explanation_decision
        document_reference = resolve_reference(working_query, lang=lang)
    if mode_decision.mode == "OBSERVE":
        return _answer_live_observation(
            request,
            query=working_query,
            lang=lang,
            started=started,
            authority_deadline_at=authority_deadline_at,
            request_deadline_at=deadline_at,
        )
    if mode != "EXPLAIN":
        if mode != "MIXED":
            return None

    try:
        from .catalog import (
            load_request_snapshot,
        )
        remaining(deadline_at)
        snapshot = load_request_snapshot()
        cards = snapshot.cards
        bound_units = (
            snapshot.units if document_reference is not None else None)
        remaining(deadline_at)
        required_source_ref = (
            f"docs/{document_reference.relative_path}"
            if document_reference is not None else ""
        )
        # Le due formulazioni della domanda — quella corrente e quella LETTA
        # NEL SUO CONTESTO — entrano nella STESSA classifica, dove ogni fonte
        # prende il massimo fra i due punteggi. Una domanda indipendente
        # conserva cosi' la propria fonte migliore, e un follow-up ellittico
        # ottiene anche la fonte che risponde alla domanda risolta, senza
        # scegliere in blocco fra due classifiche costruite su testi di
        # lunghezza diversa (vedi tutor.semantic.retrieve_sources).
        #
        # La seconda formulazione e' la CONGIUNZIONE della domanda precedente
        # con quella corrente, non la domanda precedente da sola: quest'ultima
        # non e' una domanda che l'utente ha fatto, e da sola riporta la
        # classifica sull'argomento del turno prima. Misurato sui dodici
        # scambi del corpus: la congiunzione migliora il rango della superficie
        # attesa in undici casi su dodici e ne porta in selezione due che con
        # la precedente nuda restavano fuori (RM-0003 §9-quinquies). La
        # risposta precedente resta fuori dalla sonda — renderebbe il vettore
        # quasi-duplicato delle proprie fonti (vedi
        # tutor.conversation.recent_question) — e vive nel contesto del
        # composer, dove serve a risolvere il riferimento.
        previous_question = (
            request.previous_question.strip()
            if mode_decision.is_followup else "")
        conversation_context_used = bool(previous_question)
        retrieval_trace: dict = {}
        context = retrieve_sources(
            working_query,
            lang,
            request.principal.audience,
            cards=cards,
            card_index=snapshot.card_index,
            units=bound_units,
            knowledge_index=snapshot.knowledge_index,
            companion_query=(
                f"{previous_question} {working_query}".strip()
                if previous_question else ""
            ),
            owner_user_id=request.principal.user_id,
            required_source_ref=required_source_ref,
            explain=retrieval_trace,
            deadline_at=deadline_at,
        )
        remaining(deadline_at)
        # Both real loaders above admit/verify the signed catalog.  Read its
        # identity only afterwards and without starting an extra compilation;
        # sealed unit fixtures may intentionally have no on-disk generation.
        catalog_version = snapshot.version
        remaining(deadline_at)
    except TutorDeadlineExceeded:
        raise
    except Exception:
        log.warning("tutor catalog unavailable", exc_info=True)
        return TutorAnswer(
            esito="tutor_error",
            answer_md=_with_pending_note(_msg("MSG_TUTOR_UNAVAILABLE"), request),
            score_band="none",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            detection=detection.kind,
            gap_reason="source_unavailable",
        )
    detection_label = (
        "semantic_mixed_handoff"
        if handoff_query else
        "published_document_reference"
        if document_reference is not None else
        "semantic_contextual_help"
        if conversation_context_used else "semantic_help"
    )
    if context is not None and context.restricted:
        text = _with_pending_note(_msg("MSG_TUTOR_ADMIN_REQUIRED"), request)
        return TutorAnswer(
            esito="restricted",
            answer_md=text,
            score_band="high",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            detection=detection_label,
            gap_reason="restricted_source",
            evidence=_evidence(retrieval_trace, catalog_version),
        )
    if context is None:
        text = _with_pending_note(_msg("MSG_TUTOR_LACUNA"), request)
        return TutorAnswer(
            esito="lacuna",
            answer_md=text,
            score_band="low",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            detection=detection_label,
            gap_reason="no_source",
            evidence=_evidence(retrieval_trace, catalog_version),
        )
    hits = context.hits
    primary = hits[0]
    repair_pass = 0
    repair_missing: tuple[str, ...] = ()
    repair_remaining: tuple[str, ...] = ()
    try:
        if primary.card and primary.lang in primary.card.procedure:
            # High-criticality procedures remain literal even though their
            # source was selected by the unified F2 ranker.
            effective_hits = (primary,)
            rendered = _render_context(
                primary, cards=cards,
                audience=request.principal.audience,
            )
            rendered = rendered.split("\n", 2)[2].rsplit("\n[/SOURCE]", 1)[0]
        elif (primary.unit
              and primary.unit.source_kind == "ui_procedure"):
            # Typed critical procedures are already localized, reviewed and
            # complete.  Returning them literally preserves stop conditions
            # without depending on a legacy F1 card or LLM paraphrase.
            effective_hits = (primary,)
            rendered = primary.unit.text
        else:
            # Procedures not selected as primary never become generative
            # context.  A procedure carries numbered steps and stop
            # conditions that the composer must report in full, so beside a
            # question about a DIFFERENT page it captures the answer (real
            # turn: the user-detail question answered with the proposals
            # procedure).  The rule is therefore about topic, not kind: a
            # procedure is dropped only when the primary source is another
            # page.  When the primary is the same page — or is not a page at
            # all — the procedure is often the only source attesting route
            # and fields, and removing it opens a hole (measured on the
            # proposals-console cases).  Curated card procedures stay out
            # regardless: they are whole answers, not evidence.
            primary_surface = _surface_key(primary)
            effective_hits = tuple(
                hit for hit in hits
                if not (hit.card and hit.lang in hit.card.procedure)
                and not (primary_surface is not None
                         and hit.unit is not None
                         and hit.unit.source_kind == "ui_procedure"
                         and _surface_key(hit) != primary_surface)
            )
            if not effective_hits:
                effective_hits = (primary,)
            effective_hits = _coherent_manifest_scope(
                effective_hits, primary)
            rendered_blocks = tuple((
                hit,
                _render_context(
                    hit, cards=cards,
                    audience=request.principal.audience,
                ),
            ) for hit in effective_hits)
            rendered_context = "\n\n".join(
                block for _hit, block in rendered_blocks)
            outline = ""
            if document_reference is not None and bound_units is not None:
                outline = _document_outline(
                    bound_units, required_source_ref)
                if outline:
                    rendered_context = f"{rendered_context}\n\n{outline}"
            coverage = _coverage_items(_ledger_scope(effective_hits, primary))
            ledger = _render_ledger(coverage)
            if ledger:
                rendered_context = f"{rendered_context}\n\n{ledger}"
            if not rendered_context:
                raise RuntimeError("Tutor context unavailable")
            from .compose import compose_answer
            composition_source_ids = tuple(
                _source_id(hit) for hit in effective_hits)
            composition = compose_answer(
                query=working_query,
                context=rendered_context,
                lang=lang,
                source_ids=composition_source_ids,
                conversation_context=(request.conversation_context
                                      if conversation_context_used else ""),
                delivery_channel=request.principal.channel,
                deadline_at=deadline_at,
            )
            if composition.status == "insufficient":
                source_ids = composition_source_ids
                card_ids = tuple(
                    hit.card.card_id for hit in effective_hits
                    if hit.card is not None)
                return TutorAnswer(
                    esito="lacuna",
                    answer_md=_with_pending_note(
                        _msg("MSG_TUTOR_LACUNA"), request),
                    source_ids=source_ids,
                    card_ids=card_ids,
                    score_band="low",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    detection=detection_label,
                    gap_reason="composer_insufficient",
                    evidence=_evidence(
                        retrieval_trace, catalog_version, primary),
                )
            if composition.status != "answer" or not composition.text:
                raise RuntimeError("Tutor local composition unavailable")
            rendered = composition.text
            try:
                gaps = _find_gaps(coverage, rendered, lang)
                repair_remaining = tuple(gaps)
                if gaps:
                    # Correttore di bozze: UNA sola ricomposizione con
                    # l'elenco esplicito dei buchi, poi si consegna comunque
                    # (cap onesto, niente loop). Costo: +1 chiamata wise solo
                    # su questi turni.
                    repair_pass = 1
                    repair_missing = tuple(gaps)
                    revision = compose_answer(
                        query=working_query,
                        context=(
                            f"{rendered_context}\n\n{_revision_block(gaps)}"),
                        lang=lang,
                        source_ids=composition_source_ids,
                        conversation_context=(request.conversation_context
                                              if conversation_context_used
                                              else ""),
                        delivery_channel=request.principal.channel,
                        deadline_at=deadline_at,
                    )
                    if revision.status == "answer" and revision.text:
                        # La revisione va RILETTA come la bozza: integrando i
                        # buchi elencati puo' perderne un altro, e sostituirla
                        # alla cieca peggiora la risposta consegnata (misurato
                        # su due casi: il buco finale non era fra quelli
                        # richiesti). Si consegna la versione con MENO buchi;
                        # nessuna chiamata in piu', il confronto e' meccanico.
                        revised_gaps = _find_gaps(coverage, revision.text, lang)
                        if len(revised_gaps) <= len(gaps):
                            rendered = revision.text
                            repair_remaining = tuple(revised_gaps)
                        # `repair_missing` resta l'elenco CHIESTO alla bozza:
                        # e' il contratto di telemetria dichiarato (§5.7).
            except TutorDeadlineExceeded:
                raise
            except Exception:
                # La rilettura e' una cintura: un suo guasto non deve mai
                # degradare una composizione riuscita.
                log.warning("tutor repair pass failed", exc_info=True)
    except TutorDeadlineExceeded:
        raise
    except TutorContentError:
        log.warning("Tutor source content incomplete", exc_info=True)
        rendered = _msg("MSG_TUTOR_UNAVAILABLE")
        return TutorAnswer(
            esito="tutor_error",
            answer_md=_with_pending_note(rendered, request),
            score_band="none",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            detection=detection_label,
            gap_reason="source_incomplete",
            evidence=_evidence(
                retrieval_trace, catalog_version, primary),
        )
    except Exception:
        log.warning("tutor F2 render failed", exc_info=True)
        rendered = _msg("MSG_TUTOR_UNAVAILABLE")
        return TutorAnswer(
            esito="tutor_error",
            answer_md=_with_pending_note(rendered, request),
            score_band="none",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            detection=detection_label,
            gap_reason="composer_unavailable",
            evidence=_evidence(retrieval_trace, catalog_version, primary),
        )
    text = _with_pending_note(rendered, request)
    remaining(deadline_at)
    source_ids = tuple(_source_id(hit) for hit in effective_hits)
    card_ids = tuple(
        hit.card.card_id for hit in effective_hits if hit.card is not None)
    gap_reason = (
        "composer_incomplete" if repair_remaining else ""
    )
    requested_language = lang.split("-", 1)[0]
    served_language = str(primary.lang or "").lower().split("-", 1)[0]
    if not gap_reason and served_language != requested_language:
        gap_reason = "weak_language"
    return TutorAnswer(
        esito=("handoff" if handoff_query else
               "consolidata" if primary.card else "fondata"),
        answer_md=text,
        source_ids=source_ids,
        card_ids=card_ids,
        score_band=("high" if document_reference is not None
                    or context.top_score >= 0.78 else "medium"),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        detection=detection_label,
        repair_pass=repair_pass,
        repair_missing=repair_missing,
        repair_remaining=repair_remaining,
        handoff_query=handoff_query,
        gap_reason=gap_reason,
        evidence=_evidence(
            retrieval_trace, catalog_version, primary,
            eligible=not handoff_query and not gap_reason),
    )
