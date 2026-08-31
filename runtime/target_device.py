"""runtime.target_device — risoluzione del PC bersaglio dalla query (ADR 0034,
chat-driven placement).

Decide DOVE eseguire un turno: sul server `.33` (default) oppure su uno dei PC
appaiati dell'utente, in base a cosa dice la query. Deterministico (§7.9):
mai LLM, mai liste di sinonimi nel prompt. Due segnali, entrambi ANCORATI per
evitare falsi positivi su query normali:

  1. NOME device — abbinato ai nomi REALI dei device (dato curato), SOLO se
     preceduto da una preposizione locativa: «sul portatile-ufficio», «su MAC»,
     «on my-laptop». Il nome nudo in mezzo a una frase NON instrada (una foto
     «di casa» non deve finire sul device chiamato «casa»).
  2. Marcatore LOCALE — «su questo pc», «sul mio pc», «localmente», «on this pc»,
     «locally»: risolve al device dell'utente (uno → quello; più d'uno → ambiguo).
  + Marcatore SERVER — «sul server», «qui sul server»: riporta a `.33`.

Senza alcun segnale: l'ultima destinazione (appiccicosa, passata da chi chiama)
o, in mancanza, il server. Il controllo di connessione (§L1.d placement) è
applicato SEMPRE al target risolto: offline → status «unreachable» (mai fallback
silenzioso, §2.8).

I marcatori linguistici arrivano dal detection catalog RM-0005; i NOMI device
sono dato d'istanza, non lessico, quindi restano language-agnostic.
"""
from __future__ import annotations

import re
import socket
from dataclasses import dataclass, field

import detection_lexicon as _detlex
import detection_lexicon_seed_resolvers as _resolver_seed

SERVER = "server"

# Eleggibilità al device = PURO MANIFEST-DRIVEN (`[placement] device_ok=true`),
# valutata in `agent_runtime.invoke_executor`. La vecchia whitelist hardcoded
# DEVICE_ELIGIBLE è stata RIMOSSA (rilievo #4, 2026-07-04): un executor si
# dichiara device-able nel proprio manifest, niente set centrale da mantenere.
# Una destinazione device si applica SOLO agli executor con device_ok; gli altri
# girano sul server anche con destinazione appiccicosa a un PC — così «che ore
# sono» dopo un'operazione sul PC non fallisce (get_now non è impacchettabile).

_TARGET_LEXICON_KEYS = frozenset({
    "locative_anchor", "nominal_anchor", "local_marker",
    "server_adjunct", "server_nominal",
})


def _target_lexicon() -> dict | None:
    """Mapping completo e reviewed per la lingua attiva, o fail-closed."""
    _resolver_seed.ensure_registered()
    resource = _detlex.resource_for_language(
        "resolver.target_device", _detlex.current_lang(),
        fallback=False, ready_only=True,
    )
    payload = resource.get("payload") if resource else None
    if (not resource or resource.get("kind") != "mapping"
            or resource.get("review_policy") != "manual"
            or not isinstance(payload, dict)
            or set(payload) != _TARGET_LEXICON_KEYS
            or any(
                not isinstance(payload.get(key), list)
                or not payload.get(key)
                or not all(
                    isinstance(form, str) and form.strip()
                    for form in payload[key]
                )
                for key in _TARGET_LEXICON_KEYS
            )):
        return None
    merged = _detlex.mapping("resolver.target_device")
    return merged if set(merged) == _TARGET_LEXICON_KEYS else None


def _anchor_regex(forms) -> str:
    values = [str(form).strip() for form in forms or () if str(form).strip()]
    return "(?:" + "|".join(
        re.escape(form).replace(r"\ ", r"\s+")
        for form in sorted(values, key=len, reverse=True)
    ) + ")"


@dataclass
class TargetResolution:
    """Esito della risoluzione. `target` = SERVER oppure device_id."""
    status: str = "ok"                # "ok" | "ambiguous" | "unreachable"
    target: str = SERVER              # "server" | device_id
    device_name: str | None = None    # nome del device risolto (None = server)
    explicit: bool = False            # la query nominava esplicitamente un target?
    candidates: list = field(default_factory=list)   # per status="ambiguous": [(id,name)]
    unreachable_name: str | None = None              # per status="unreachable"
    unreachable_id: str | None = None                # id del device offline (A.1 defer)
    cleaned_query: str = ""           # query senza l'adjunct di destinazione


@dataclass(frozen=True)
class _TargetMention:
    """One ordered placement mention after centralized polarity analysis."""
    identity: str
    start: int
    stop: int
    state: str
    span: str
    strip: bool
    source: str
    strong: bool
    device: object | None = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _find_marker(qn: str, markers) -> str | None:
    for m in markers:
        if re.search(r"(?<![a-z0-9])" + re.escape(m) + r"(?![a-z0-9])", qn):
            return m
    return None


def _server_aliases(values=None) -> tuple[str, ...]:
    """Return validated instance aliases; identity data, never query rules."""

    if values is None:
        import config as _C
        values = [*_C.SERVER_ALIASES, socket.gethostname()]
    aliases = []
    for raw in values or ():
        value = _norm(str(raw or ""))
        if (1 <= len(value) <= 64 and "\x00" not in value
                and value not in aliases):
            aliases.append(value)
    return tuple(aliases)


def _has_machine_focus(query: str) -> bool:
    """Whether the request asks about machine state or hardware.

    The natural-language surface comes from the versioned detection catalog;
    aliases themselves are instance identity. This keeps the algorithm valid
    for any installed language and any server name.
    """

    try:
        import detection_lexicon as _detlex
        if _detlex.match("system.status_query", query):
            return True
        focus = _detlex.mapping("health.section_focus") or {}
        return any(
            _detlex.match_any(forms, query)
            for forms in focus.values()
            if isinstance(forms, list)
        )
    except Exception:
        return False


def _find_server_alias(query: str, aliases) -> str | None:
    for alias in _server_aliases(aliases):
        if re.search(
            r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])",
            query,
        ):
            return alias
    return None


def _match_polarity_state(qn: str, match) -> str:
    """Tri-state polarity shared by every explicit target form."""
    try:
        import detection_lexicon as _detlex
        return _detlex.polarity_state_at(
            qn, match.start(), target_scope=True,
        )
    except Exception:
        return "unavailable"


def _find_named_device(qn: str, devices):
    """Trova un device nominato ANCORATO da una preposizione locativa. Ritorna:
      - `(device, span, None)`          match UNICO (nome più lungo vince, per
                                        disambiguare nomi prefisso l'uno dell'altro);
      - `(None, span, [(id,name),…])`   nomi DUPLICATI (stesso nome più lungo su
                                        device DIVERSI) → ambiguo, non arbitrario
                                        (§5: unicità per owner o errore ambiguous);
      - `None`                          nessun match.
    """
    lexicon = _target_lexicon()
    if lexicon is None:
        return None
    locative_anchor = _anchor_regex(lexicon["locative_anchor"])
    nominal_anchor = _anchor_regex(lexicon["nominal_anchor"])
    matches = []  # (device, span, name, nominal)
    for d in devices:
        name = _norm(getattr(d, "name", "") or "")
        if len(name) < 3:
            continue  # nomi troppo corti = rischio falso positivo, salta
        pat = (r"(?<![a-z0-9])" + locative_anchor + r"\s+[\"']?"
               + re.escape(name) + r"(?![a-z0-9])")
        m = re.search(pat, qn)
        if m:
            matches.append((d, m.group(0), name, False))
            continue
        # Ancora NOMINALE («il pc-roberto», «di pc-roberto»): routing sì,
        # strip NO (il caller preserva la query). SOLO per nomi TECNICI
        # (composti: trattino/underscore/cifra) — un device chiamato con una
        # parola comune («casa») matcherebbe le locuzioni («le foto di casa»)
        # e roulerebbe per errore (test bare_name). Strutturale, no liste.
        if not re.search(r"[-_\d]", name):
            continue
        pat_n = (r"(?<![a-z0-9])" + nominal_anchor + r"\s+[\"']?"
                 + re.escape(name) + r"(?![a-z0-9])")
        m = re.search(pat_n, qn)
        if m:
            matches.append((d, m.group(0), name, True))
            continue
        # Nome tecnico nudo («temperatura pc-roberto»): i nomi con struttura
        # distintiva (trattino, underscore o cifra) sono sufficientemente
        # specifici da costituire da soli un riferimento esplicito.
        # Le locuzioni comuni restano escluse per evitare falsi positivi.
        if re.search(r"[-_\d]", name):
            pat_bare = (r"(?<![a-z0-9])" + re.escape(name)
                        + r"(?![a-z0-9])")
            m = re.search(pat_bare, qn)
            if m:
                matches.append((d, m.group(0), name, False))
    if not matches:
        return None
    maxlen = max(len(n) for _d, _s, n, _nom in matches)
    best = [t for t in matches if len(t[2]) == maxlen]
    # locativo (strip) preferito sul nominale a parità di device
    best.sort(key=lambda t: t[3])
    if len({d.id for d, _s, _n, _nom in best}) > 1:
        return (None, best[0][1],
                [(d.id, getattr(d, "name", "")) for d, _s, _n, _nom in best],
                best[0][3])
    d, s, _n, nom = best[0]
    return (d, s, None, nom)
def _surface_mentions(
        qn: str, specs, *, identity: str,
        device=None, source: str = "marker",
        strong: bool = True) -> list[_TargetMention]:
    """Non-overlapping literal marker mentions, longest surface first."""
    candidates = []
    for surface, strip in specs:
        pattern = re.compile(
            r"(?<![a-z0-9])" + re.escape(surface) + r"(?![a-z0-9])"
        )
        for match in pattern.finditer(qn):
            candidates.append((match, surface, bool(strip)))
    mentions: list[_TargetMention] = []
    occupied_until = -1
    for match, _surface, strip in sorted(
            candidates,
            key=lambda item: (
                item[0].start(), -(item[0].end() - item[0].start()),
            )):
        if match.start() < occupied_until:
            continue
        occupied_until = match.end()
        mentions.append(_TargetMention(
            identity=identity,
            start=match.start(),
            stop=match.end(),
            state=_match_polarity_state(qn, match),
            span=match.group(0),
            strip=strip,
            source=source,
            strong=strong,
            device=device,
        ))
    return mentions


def _named_device_mentions(qn: str, devices,
                           lexicon: dict | None = None) -> list[_TargetMention]:
    """All ordered named-device mentions, excluding nested bare duplicates."""
    lexicon = lexicon or _target_lexicon()
    if lexicon is None:
        return []
    locative_anchor = _anchor_regex(lexicon["locative_anchor"])
    nominal_anchor = _anchor_regex(lexicon["nominal_anchor"])
    out: list[_TargetMention] = []
    for device in devices:
        name = _norm(getattr(device, "name", "") or "")
        if len(name) < 3:
            continue
        anchored = []
        locative = re.compile(
            r"(?<![a-z0-9])" + locative_anchor + r"\s+[\"']?"
            + re.escape(name) + r"(?![a-z0-9])"
        )
        anchored.extend((match, True) for match in locative.finditer(qn))
        if re.search(r"[-_\d]", name):
            nominal = re.compile(
                r"(?<![a-z0-9])" + nominal_anchor + r"\s+[\"']?"
                + re.escape(name) + r"(?![a-z0-9])"
            )
            anchored.extend((match, False) for match in nominal.finditer(qn))
        ranges = [(match.start(), match.end()) for match, _strip in anchored]
        candidates = list(anchored)
        if re.search(r"[-_\d]", name):
            bare = re.compile(
                r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])"
            )
            for match in bare.finditer(qn):
                if not any(
                        begin <= match.start() and match.end() <= stop
                        for begin, stop in ranges):
                    candidates.append((match, True))
        for match, strip in candidates:
            out.append(_TargetMention(
                identity=str(getattr(device, "id", "")),
                start=match.start(),
                stop=match.end(),
                state=_match_polarity_state(qn, match),
                span=match.group(0),
                strip=strip,
                source="named",
                strong=True,
                device=device,
            ))
    return [
        mention for mention in out
        if not any(
            other.start <= mention.start
            and mention.stop < other.stop
            for other in out
        )
    ]


def _latest_mentions(mentions) -> dict[str, _TargetMention]:
    """Last mention wins independently for each stable target identity."""
    latest: dict[str, _TargetMention] = {}
    for mention in mentions:
        previous = latest.get(mention.identity)
        overlaps = (
            previous is not None
            and mention.start < previous.stop
            and previous.start < mention.stop
        )
        prefer_overlap = (
            overlaps
            and (
                mention.strong,
                mention.stop - mention.start,
            ) > (
                previous.strong,
                previous.stop - previous.start,
            )
        )
        prefer_later = (
            previous is not None and not overlaps
            and (mention.start, mention.stop) >= (
                previous.start, previous.stop
            )
        )
        if previous is None or prefer_overlap or prefer_later:
            latest[mention.identity] = mention
    return latest


_POSIX_SERVER_PATH_RE = re.compile(
    r"(?:^|[\s\"'`(])/(?:opt|home|etc|var|usr|srv|mnt|tmp|root)(?:/|\b)")
_WIN_FORM_PATH_RE = re.compile(r"(?:^|[\s\"'`(])(?:[A-Za-z]:[\\/]|\\\\)")


def _path_platform_hints(query: str) -> set[str]:
    """Forme di path presenti nella query: {'posix','windows'} (può essere
    vuoto o doppio). Deterministico §7.9 — serve all'hint forma-path→host."""
    hints: set[str] = set()
    if _POSIX_SERVER_PATH_RE.search(query or ""):
        hints.add("posix")
    if _WIN_FORM_PATH_RE.search(query or ""):
        hints.add("windows")
    return hints


def resolve_target(query: str,
                   devices: list,
                   *,
                   last_target: str | None = None,
                   server_aliases=None,
                   is_available=None,
                   now=None) -> TargetResolution:
    """Risolvi il PC bersaglio.

    - query: testo utente del turno.
    - devices: lista dei device dell'utente (già filtrata per proprietario).
    - last_target: destinazione appiccicosa (device_id o SERVER) del turno
      precedente, o None.
    - is_available: callable(device, now)->bool (default: placement.is_available).
    """
    if is_available is None:
        from placement import is_available as _ia
        is_available = _ia

    qn = _norm(query)
    res = TargetResolution(cleaned_query=query or "")
    target_lexicon = _target_lexicon()
    if target_lexicon is None:
        res.status = "ambiguous"
        return res
    machine_focus = _has_machine_focus(qn)

    # Build one ordered event stream. The last mention wins independently for
    # each stable target identity, including across equivalent forms (generic
    # local marker versus a named device). This prevents an earlier assertion
    # from bypassing a later revocation and honors later target corrections.
    server_specs = [
        *((marker, True) for marker in target_lexicon["server_adjunct"]),
        *((marker, False) for marker in target_lexicon["server_nominal"]),
    ]
    server_mentions = _surface_mentions(
        qn, server_specs, identity=SERVER, source="server", strong=True,
    )
    if machine_focus:
        server_mentions.extend(_surface_mentions(
            qn,
            ((alias, False) for alias in _server_aliases(server_aliases)),
            identity=SERVER,
            source="alias",
            strong=False,
        ))
    local_mentions = _surface_mentions(
        qn, ((marker, True) for marker in target_lexicon["local_marker"]),
        identity="__local__", source="local", strong=True,
    )
    mentions = list(server_mentions)
    mentions.extend(_named_device_mentions(qn, devices, target_lexicon))
    if devices:
        for local in local_mentions:
            for device in devices:
                mentions.append(_TargetMention(
                    identity=str(device.id),
                    start=local.start,
                    stop=local.stop,
                    state=local.state,
                    span=local.span,
                    strip=local.strip,
                    source=local.source,
                    strong=local.strong,
                    device=device,
                ))
    else:
        mentions.extend(local_mentions)

    latest = _latest_mentions(mentions)
    if any(mention.state == "unavailable" for mention in latest.values()):
        res.status = "ambiguous"
        return res

    asserted = [
        mention for mention in latest.values()
        if mention.state == "asserted"
    ]
    if asserted:
        if any(mention.strong for mention in asserted):
            asserted = [mention for mention in asserted if mention.strong]
        last_start = max(mention.start for mention in asserted)
        winners = [
            mention for mention in asserted if mention.start == last_start
        ]
        if len(winners) > 1 and all(
                mention.source == "local" for mention in winners):
            available = [
                mention for mention in winners
                if mention.device is not None
                and is_available(mention.device, now)
            ]
            if len(available) == 1:
                winners = available
            elif not available:
                res.status = "unreachable"
                first = winners[0]
                res.unreachable_name = getattr(first.device, "name", None)
                res.unreachable_id = getattr(first.device, "id", None)
                return res
            else:
                winners = available
        if len(winners) != 1:
            res.status = "ambiguous"
            res.candidates = [
                (
                    mention.identity,
                    "server" if mention.identity == SERVER else str(
                        getattr(mention.device, "name", "") or ""
                    ),
                )
                for mention in winners
            ]
            return res
        winner = winners[0]
        res.explicit = True
        if winner.strip:
            res.cleaned_query = _strip_span(query, winner.span)
        if winner.identity == SERVER:
            res.target = SERVER
            return res
        if winner.identity == "__local__" or winner.device is None:
            res.status = "unreachable"
            return res
        if not is_available(winner.device, now):
            res.status = "unreachable"
            res.unreachable_name = getattr(winner.device, "name", None)
            res.unreachable_id = getattr(winner.device, "id", None)
            return res
        res.target = winner.identity
        res.device_name = getattr(winner.device, "name", None)
        return res

    server_negated = (
        latest.get(SERVER) is not None
        and latest[SERVER].state == "negated"
    )
    forbidden_device_ids = {
        identity for identity, mention in latest.items()
        if identity not in {SERVER, "__local__"}
        and mention.state == "negated"
    }

    # --- Nessun segnale: destinazione appiccicosa, poi server ---
    if last_target and last_target != SERVER:
        dev = next((d for d in devices if d.id == last_target), None)
        sticky_forbidden = str(last_target) in forbidden_device_ids
        if dev is not None and not sticky_forbidden:
            if is_available(dev, now):
                # Hint forma-path→host (5/7, visto live): lo STICKY non deve
                # dirottare al device una query con un path in forma POSIX
                # assoluta (= filesystem del server) se il device è Windows —
                # «/opt/metnos/...» diventava «C:\opt\...» not-found sul PC.
                # RESTRIZIONE-only (principio ADR 0179): il nome ESPLICITO nel
                # turno vince sempre (ramo sopra); un path Windows-form
                # conferma il device; forma doppia/assente = sticky normale.
                _hints = _path_platform_hints(query or "")
                _dev_os = (getattr(dev, "os_family", "") or "").lower()
                if ("posix" in _hints and "windows" not in _hints
                        and _dev_os.startswith("win")):
                    if server_negated:
                        res.status = "ambiguous"
                        return res
                    res.target = SERVER
                    return res
                res.target = dev.id
                res.device_name = getattr(dev, "name", None)
                res.explicit = False
                return res
            # appiccicosa ma OFFLINE: l'utente NON ha nominato il device questo
            # turno → decadi al SERVER, NON errore (§1: «che ore sono» non deve
            # fallire solo perché l'ultimo PC usato è spento). Un riferimento
            # ESPLICITO a un PC offline dà invece «non connesso» (sopra).
            if server_negated:
                res.status = "ambiguous"
                return res
            res.target = SERVER
            return res
        # il device appiccicoso non esiste più → decadi al server
    if server_negated:
        res.status = "ambiguous"
        return res
    res.target = SERVER
    return res


def references_device(query: str, devices: list, *, server_aliases=None) -> bool:
    """True se la query cita ESPLICITAMENTE una destinazione (nome device
    ancorato, marcatore locale, marcatore server). Usato PRIMA del fast_path
    lessicale (target-blind) per saltarlo: una query che nomina un PC deve
    passare dall'engine, che ri-risolve il placement e ri-controlla la
    connessione ad OGNI turno (mai una risposta cachata stantia / sul server
    sbagliato). Economico: solo regex, nessun I/O."""
    qn = _norm(query)
    target_lexicon = _target_lexicon()
    if target_lexicon is None:
        return True
    server_markers = [
        *target_lexicon["server_adjunct"],
        *target_lexicon["server_nominal"],
    ]
    if _find_marker(qn, server_markers):
        return True
    if _find_marker(qn, target_lexicon["local_marker"]):
        return True
    if devices and _find_named_device(qn, devices):
        return True
    if _has_machine_focus(qn) and _find_server_alias(qn, server_aliases):
        return True
    return False


def _strip_span(query: str, span: str) -> str:
    """Rimuove l'adjunct di destinazione dalla query (best-effort), così l'engine
    pianifica sull'operazione pura. Case-insensitive, collassa gli spazi."""
    if not query or not span:
        return query or ""
    out = re.sub(re.escape(span), " ", query, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip(" ,.;:")
