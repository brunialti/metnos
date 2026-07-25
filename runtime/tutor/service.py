"""Single fail-soft entry point for unified F2 retrieval and composition."""

from __future__ import annotations

import os
import re
import time
import tomllib

from logging_setup import get_logger
from messages import get as _msg

from .detect import classify
from .models import TutorAnswer, TutorRequest
from .render import render_card
from .semantic import SemanticContext, SourceHit, retrieve_sources

log = get_logger(__name__)

_PURPOSE_CUT = re.compile(
    r"\s+(?:PATTERN|NON|OUT|INPUT|OUTPUT):", re.IGNORECASE)
_PURPOSE_PREFIX = re.compile(r"^[^:]{1,20}:\s*")


def enabled() -> bool:
    return os.environ.get("METNOS_TUTOR", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _with_pending_note(answer: str, request: TutorRequest) -> str:
    if not request.has_pending:
        return answer
    return f"{answer.rstrip()}\n\n{_msg('MSG_TUTOR_PENDING_PRESERVED')}"


_PREVIOUS_QUESTION_MARKER = "PREVIOUS_USER_QUESTION:"
_PREVIOUS_ANSWER_MARKER = "PREVIOUS_TUTOR_ANSWER:"


def _previous_question(request: TutorRequest) -> str:
    """Estrae la sola domanda precedente dal contesto di conversazione.

    Il contesto passato al composer resta l'intero scambio; la SONDA di
    retrieval usa solo la domanda, per il motivo misurato in
    ``tutor.conversation.recent_question``. La struttura del contesto e'
    quella dichiarata da quel modulo: se cambia, qui non si indovina —
    si ricade sul comportamento senza contesto.
    """

    raw = request.conversation_context or ""
    if _PREVIOUS_QUESTION_MARKER not in raw:
        return ""
    question = raw.split(_PREVIOUS_QUESTION_MARKER, 1)[1]
    return question.split(_PREVIOUS_ANSWER_MARKER, 1)[0].strip()


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
        raise ValueError("Tutor live catalog below the card completeness floor")

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
            raise ValueError("Tutor overview has no admitted capability areas")
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
        raise ValueError("Tutor card could not describe the admitted inventory")
    heading = str(((selector.get("heading") or {}).get(lang)) or "").format(
        count=len(names))
    return "\n".join(([heading] if heading else []) + lines)


def _source_id(hit: SourceHit) -> str:
    if hit.card:
        return f"card:{hit.card.card_id}:{hit.lang}"
    return f"knowledge:{hit.unit.unit_id}"


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
    return (
        f"[SOURCE id={_source_id(hit)} authority={authority} "
        f"source_kind={source_kind}]\n"
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


def _ledger_scope(hits: tuple[SourceHit, ...],
                  primary: SourceHit) -> tuple[SourceHit, ...]:
    """Restringe la checklist alla pagina PRIMARIA quando la domanda è su una
    pagina.

    Il ledger nasce da tutte le fonti strutturate selezionate: giusto per una
    panoramica, sbagliato quando la primaria è una superficie e nel contesto
    ci sono anche pagine vicine. In quel caso il correttore chiede voci di
    un'ALTRA pagina e spende l'unica ricomposizione sul buco sbagliato
    (misurato: 15 voci richieste, tutte estranee alla domanda). Regola
    strutturale sull'identità della superficie, nessun tema o frase.
    """

    primary_key = _surface_key(primary)
    if primary_key is None:
        return hits
    return tuple(
        hit for hit in hits
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
                stop = bool(surface.stop_conditions(unit_lang))
                if stop:
                    entry += "; stop conditions attested by the source"
                if entry not in seen_surfaces:
                    seen_surfaces.add(entry)
                    surfaces.append({
                        "entry": entry,
                        "label": surface.label(unit_lang),
                        "route": surface.route,
                        "visible": visible,
                        "controls": controls,
                        "stop": stop,
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
_STOP_LEADS = {"it": "Fermati se", "en": "Stop if"}
_GAP_WORD = re.compile(r"[^\W\d_]+")


def _noise_words() -> set[str]:
    """Parole funzionali gia' lessicalizzate (articoli/preposizioni it+en)."""

    import detection_lexicon as dl
    return {
        form.casefold()
        for concept in ("sites.goal_noise",
                        "sites.goal_noise_articulated_preposition")
        for form in dl.forms(concept)
    }


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


def _content_words(label: str, noise: set[str]) -> list[str]:
    return [
        word for word in _GAP_WORD.findall(label)
        if len(word) >= 4 and word.casefold() not in noise
    ]


_SHORT_LABEL_WORDS = 3


def _label_covered(label: str, text: str, noise: set[str],
                   df: dict) -> bool:
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
    words = _content_words(label, noise)
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
    noise = _noise_words()
    labels: list[str] = [*coverage.get("areas", ()),
                         *coverage.get("tools", ())]
    for surface in coverage.get("surfaces", ()):
        labels.extend(surface["visible"])
        labels.extend(surface["controls"])
    df: Counter = Counter()
    for label in labels:
        df.update({_root(word) for word in _content_words(label, noise)})
    for marker in _INTERNAL_MARKERS:
        if marker.casefold() in low:
            gaps.append(f"remove the internal marker {marker!r} from the prose")
    for provider in coverage.get("providers", ()):
        if not dl.match_any([provider], text, mode="word"):
            gaps.append(f"mention the provider: {provider}")
    for area in coverage.get("areas", ()):
        if not _label_covered(area, text, noise, df):
            gaps.append(f"cover the capability area: {area}")
    for purpose in coverage.get("tools", ()):
        if not _label_covered(purpose, text, noise, df):
            gaps.append(f"state the tool purpose: {purpose}")
    for surface in coverage.get("surfaces", ()):
        if surface["route"].casefold() not in low:
            gaps.append(
                f"state the exact navigation path {surface['route']} "
                f"for {surface['label']}")
        for item in (*surface["visible"], *surface["controls"]):
            if not _label_covered(item, text, noise, df):
                gaps.append(f"cover the {surface['label']} item: {item}")
    if any(surface["stop"] for surface in coverage.get("surfaces", ())):
        if not any(lead.casefold() in low for lead in _STOP_LEADS.values()):
            lead = _STOP_LEADS.get(lang, _STOP_LEADS["en"])
            gaps.append(
                "report the stop conditions verbatim, introduced by "
                f"«{lead}»")
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


def answer_request(request: TutorRequest) -> TutorAnswer | None:
    """Return an answer only for a high-confidence help request.

    Any internal failure is handled by the channel adapter, which can emit the
    localized unavailable message without routing the help query to the
    planner.  Non-help returns ``None`` and leaves the existing path intact.
    """

    if not enabled():
        return None
    started = time.monotonic()
    try:
        detection = classify(request.query_redacted)
    except Exception:
        # Detection failure must not steal an operational request.
        log.warning("tutor detection unavailable", exc_info=True)
        return None
    if detection.reason in {"sensitive_shape", "control_command"}:
        return None
    import config
    lang = (request.lang or config.DEFAULT_LANG).lower().split("-", 1)[0]
    from .mode import classify_mode
    mode = classify_mode(
        request.query_redacted,
        lang,
        conversation_context=request.conversation_context,
    )
    if mode == "MIXED":
        return TutorAnswer(
            esito="clarification",
            answer_md=_with_pending_note(
                _msg("MSG_TUTOR_MIXED_CLARIFY"), request),
            score_band="high",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            detection="semantic_mixed",
        )
    if mode != "EXPLAIN":
        return None

    try:
        from .catalog import load_cards
        cards = load_cards()
        context = retrieve_sources(
            request.query_redacted,
            lang,
            request.principal.audience,
            cards=cards,
        )
        conversation_context_used = False
        previous_question = _previous_question(request)
        if previous_question:
            contextual = retrieve_sources(
                f"{request.query_redacted}\n\n{previous_question}",
                lang,
                request.principal.audience,
                cards=cards,
            )
            # Semantic evidence decides whether the previous exchange helps.
            # Independent questions retain their stronger current-turn match;
            # elliptical follow-ups inherit context only when it materially
            # improves retrieval.  No topic or phrase is encoded here.
            # The probe carries the previous QUESTION only: adding the previous
            # ANSWER made the vector a near-duplicate of that answer's own
            # sources, so the gain test was self-fulfilling for every
            # follow-up (see tutor.conversation.recent_question).
            if (contextual is not None and (
                    context is None
                    or contextual.top_score >= context.top_score + 0.02)):
                context = contextual
                conversation_context_used = True
    except Exception:
        log.warning("tutor catalog unavailable", exc_info=True)
        return TutorAnswer(
            esito="tutor_error",
            answer_md=_with_pending_note(_msg("MSG_TUTOR_UNAVAILABLE"), request),
            score_band="none",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            detection=detection.kind,
        )
    detection_label = (
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
        )
    if context is None:
        text = _with_pending_note(_msg("MSG_TUTOR_LACUNA"), request)
        return TutorAnswer(
            esito="lacuna",
            answer_md=text,
            score_band="low",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            detection=detection_label,
        )
    hits = context.hits
    primary = hits[0]
    repair_pass = 0
    repair_missing: tuple[str, ...] = ()
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
        elif primary.unit and primary.unit.source_kind == "ui_procedure":
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
            rendered_context = "\n\n".join(
                _render_context(
                    hit, cards=cards,
                    audience=request.principal.audience,
                )
                for hit in effective_hits
            )
            coverage = _coverage_items(_ledger_scope(effective_hits, primary))
            ledger = _render_ledger(coverage)
            if ledger:
                rendered_context = f"{rendered_context}\n\n{ledger}"
            if not rendered_context:
                raise RuntimeError("Tutor context unavailable")
            from .compose import compose_answer
            composition = compose_answer(
                query=request.query_redacted,
                context=rendered_context,
                lang=lang,
                source_ids=tuple(_source_id(hit) for hit in effective_hits),
                conversation_context=(request.conversation_context
                                      if conversation_context_used else ""),
                delivery_channel=request.principal.channel,
            )
            if composition.status == "insufficient":
                source_ids = tuple(_source_id(hit) for hit in effective_hits)
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
                )
            if composition.status != "answer" or not composition.text:
                raise RuntimeError("Tutor local composition unavailable")
            rendered = composition.text
            try:
                gaps = _find_gaps(coverage, rendered, lang)
                if gaps:
                    # Correttore di bozze: UNA sola ricomposizione con
                    # l'elenco esplicito dei buchi, poi si consegna comunque
                    # (cap onesto, niente loop). Costo: +1 chiamata wise solo
                    # su questi turni.
                    repair_pass = 1
                    repair_missing = tuple(gaps)
                    revision = compose_answer(
                        query=request.query_redacted,
                        context=(
                            f"{rendered_context}\n\n{_revision_block(gaps)}"),
                        lang=lang,
                        source_ids=tuple(
                            _source_id(hit) for hit in effective_hits),
                        conversation_context=(request.conversation_context
                                              if conversation_context_used
                                              else ""),
                        delivery_channel=request.principal.channel,
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
                        # `repair_missing` resta l'elenco CHIESTO alla bozza:
                        # e' il contratto di telemetria dichiarato (§5.7).
            except Exception:
                # La rilettura e' una cintura: un suo guasto non deve mai
                # degradare una composizione riuscita.
                log.warning("tutor repair pass failed", exc_info=True)
    except Exception:
        log.warning("tutor F2 render failed", exc_info=True)
        rendered = _msg("MSG_TUTOR_UNAVAILABLE")
        return TutorAnswer(
            esito="tutor_error",
            answer_md=_with_pending_note(rendered, request),
            score_band="none",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            detection=detection_label,
        )
    text = _with_pending_note(rendered, request)
    source_ids = tuple(_source_id(hit) for hit in effective_hits)
    card_ids = tuple(
        hit.card.card_id for hit in effective_hits if hit.card is not None)
    return TutorAnswer(
        esito="consolidata" if primary.card else "fondata",
        answer_md=text,
        source_ids=source_ids,
        card_ids=card_ids,
        score_band="high" if context.top_score >= 0.78 else "medium",
        elapsed_ms=int((time.monotonic() - started) * 1000),
        detection=detection_label,
        repair_pass=repair_pass,
        repair_missing=repair_missing,
    )
