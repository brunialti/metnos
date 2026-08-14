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

Debito i18n: i marcatori sono inline {it,en} (vedi [[project-i18n-lexicon-debt]]);
i NOMI device sono dato, non lessico, quindi language-agnostic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SERVER = "server"

# Eleggibilità al device = PURO MANIFEST-DRIVEN (`[placement] device_ok=true`),
# valutata in `agent_runtime.invoke_executor`. La vecchia whitelist hardcoded
# DEVICE_ELIGIBLE è stata RIMOSSA (rilievo #4, 2026-07-04): un executor si
# dichiara device-able nel proprio manifest, niente set centrale da mantenere.
# Una destinazione device si applica SOLO agli executor con device_ok; gli altri
# girano sul server anche con destinazione appiccicosa a un PC — così «che ore
# sono» dopo un'operazione sul PC non fallisce (get_now non è impacchettabile).

# Preposizioni locative che ANCORANO un nome-device (IT + EN). L'ancora è ciò che
# distingue «sul portatile» (instrada) da «il portatile» (no).
_PREP = r"(?:su|sul|sullo|sulla|sui|sugli|sulle|nel|su\s+questo|on|onto)"
# Ancora NOMINALE (10/7, turn 143f7cff «che processore ha il pc-roberto»):
# il device è l'OGGETTO della frase, non un complemento di luogo. Articoli/
# preposizioni nominali; il match nominale NON strippa la query (come i
# marcatori server nominali — strippare demolirebbe la semantica).
_PREP_NOMINAL = (r"(?:il|lo|la|l'|del|dello|della|dell'|dei|degli|delle|di|"
                 r"the|of|from)")

# Marcatori «questo pc / locale» → device dell'utente (ancorati per frase).
_LOCAL_MARKERS = (
    "su questo pc", "su questo computer", "su questa macchina",
    "sul mio pc", "sul mio computer", "sul mio portatile", "sul mio fisso",
    "localmente", "in locale", "qui sul pc", "sul pc locale",
    "on this pc", "on this computer", "on this machine",
    "on my pc", "on my computer", "on my laptop", "on my machine", "locally",
)
# Marcatori «server / .33» → riporta al server.
# AVVERBIALI (complemento di luogo, «dove eseguire»): il marcatore è un adjunct
# rimovibile — la query resta sensata senza («elenca i file sul server» →
# «elenca i file»). Routing + STRIP.
_SERVER_MARKERS_ADJUNCT = (
    "sul server", "qui sul server", "sul .33", "sul metnos", "lato server",
    "on the server", "server side",
)
# NOMINALI: «server» è l'OGGETTO della domanda («stato del server», «come sta
# il server», «descrivi metnos server»). Routing sì, STRIP **NO** — strippare
# demoliva la semantica (bug 9/7: «stato del server»→«stato»→intent object
# instabile approval/numbers → misroute get_approval/wttr.in). «server» in
# Metnos = .33; il PC è «pc/computer/laptop».
_SERVER_MARKERS_NOMINAL = (
    "del server", "dello .33", "questo server", "il server", "metnos server",
    "server metnos", "questo metnos", "of the server", "this server",
    "the server",
)
# Unione (compat per i call-site che testano solo la presenza).
_SERVER_MARKERS = _SERVER_MARKERS_ADJUNCT + _SERVER_MARKERS_NOMINAL


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


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _find_marker(qn: str, markers) -> str | None:
    for m in markers:
        if re.search(r"(?<![a-z0-9])" + re.escape(m) + r"(?![a-z0-9])", qn):
            return m
    return None


def _find_named_device(qn: str, devices):
    """Trova un device nominato ANCORATO da una preposizione locativa. Ritorna:
      - `(device, span, None)`          match UNICO (nome più lungo vince, per
                                        disambiguare nomi prefisso l'uno dell'altro);
      - `(None, span, [(id,name),…])`   nomi DUPLICATI (stesso nome più lungo su
                                        device DIVERSI) → ambiguo, non arbitrario
                                        (§5: unicità per owner o errore ambiguous);
      - `None`                          nessun match.
    """
    matches = []  # (device, span, name, nominal)
    for d in devices:
        name = _norm(getattr(d, "name", "") or "")
        if len(name) < 3:
            continue  # nomi troppo corti = rischio falso positivo, salta
        pat = r"(?<![a-z0-9])" + _PREP + r"\s+[\"']?" + re.escape(name) + r"(?![a-z0-9])"
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
        pat_n = (r"(?<![a-z0-9])" + _PREP_NOMINAL + r"\s+[\"']?"
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

    # --- SERVER esplicito (vince, riporta al .33) ---
    # Adjunct («sul server») → strip; nominale («del server») → query INTATTA
    # (il «server» è l'oggetto della domanda, non un complemento di luogo).
    sm = _find_marker(qn, _SERVER_MARKERS_ADJUNCT)
    if sm:
        res.target = SERVER
        res.device_name = None
        res.explicit = True
        res.cleaned_query = _strip_span(query, sm)
        return res
    sm = _find_marker(qn, _SERVER_MARKERS_NOMINAL)
    if sm:
        res.target = SERVER
        res.device_name = None
        res.explicit = True
        # niente strip: la query resta intera per intent/routing
        return res

    # --- NOME device esplicito (ancorato: locativo → strip; nominale → no) ---
    named = _find_named_device(qn, devices)
    if named:
        dev, span, dup, nominal = named
        res.explicit = True
        if not nominal:
            res.cleaned_query = _strip_span(query, span)
        if dup is not None:               # nomi duplicati → ambiguo (§5)
            res.status = "ambiguous"
            res.candidates = dup
            return res
        if not is_available(dev, now):
            res.status = "unreachable"
            res.unreachable_name = getattr(dev, "name", None)
            res.unreachable_id = getattr(dev, "id", None)
            return res
        res.target = dev.id
        res.device_name = getattr(dev, "name", None)
        return res

    # --- Marcatore LOCALE → device dell'utente ---
    lm = _find_marker(qn, _LOCAL_MARKERS)
    if lm:
        res.explicit = True
        res.cleaned_query = _strip_span(query, lm)
        avail = [d for d in devices if is_available(d, now)]
        if len(avail) == 1:
            res.target = avail[0].id
            res.device_name = getattr(avail[0], "name", None)
            return res
        if len(avail) == 0:
            res.status = "unreachable"
            # se ha device ma nessuno raggiungibile, nomina il primo per il messaggio
            res.unreachable_name = (getattr(devices[0], "name", None)
                                    if devices else None)
            return res
        # più device raggiungibili e nessun nome → ambiguo
        res.status = "ambiguous"
        res.candidates = [(d.id, getattr(d, "name", "")) for d in avail]
        return res

    # --- Nessun segnale: destinazione appiccicosa, poi server ---
    if last_target and last_target != SERVER:
        dev = next((d for d in devices if d.id == last_target), None)
        if dev is not None:
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
            res.target = SERVER
            return res
        # il device appiccicoso non esiste più → decadi al server
    res.target = SERVER
    return res


def references_device(query: str, devices: list) -> bool:
    """True se la query cita ESPLICITAMENTE una destinazione (nome device
    ancorato, marcatore locale, marcatore server). Usato PRIMA del fast_path
    lessicale (target-blind) per saltarlo: una query che nomina un PC deve
    passare dall'engine, che ri-risolve il placement e ri-controlla la
    connessione ad OGNI turno (mai una risposta cachata stantia / sul server
    sbagliato). Economico: solo regex, nessun I/O."""
    qn = _norm(query)
    if _find_marker(qn, _SERVER_MARKERS):
        return True
    if _find_marker(qn, _LOCAL_MARKERS):
        return True
    if devices and _find_named_device(qn, devices):
        return True
    return False


def _strip_span(query: str, span: str) -> str:
    """Rimuove l'adjunct di destinazione dalla query (best-effort), così l'engine
    pianifica sull'operazione pura. Case-insensitive, collassa gli spazi."""
    if not query or not span:
        return query or ""
    out = re.sub(re.escape(span), " ", query, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip(" ,.;:")
