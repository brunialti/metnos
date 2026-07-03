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

# Executor che POSSONO girare su un device remoto oggi (chiusura shim C7:
# stdlib + executor_helpers/messages/path_alias). Una destinazione device si
# applica SOLO a questi; gli altri girano sul server ANCHE con destinazione
# appiccicosa a un PC — così «che ore sono» dopo un'operazione sul PC non
# fallisce (get_now non è impacchettabile). TODO: rendere manifest-driven
# ([placement] device_ok) quando C7 R2/R3 amplia la copertura.
DEVICE_ELIGIBLE = frozenset({"get_files", "compute_files_loc", "list_dirs"})

# Preposizioni locative che ANCORANO un nome-device (IT + EN). L'ancora è ciò che
# distingue «sul portatile» (instrada) da «il portatile» (no).
_PREP = r"(?:su|sul|sullo|sulla|sui|sugli|sulle|nel|su\s+questo|on|onto)"

# Marcatori «questo pc / locale» → device dell'utente (ancorati per frase).
_LOCAL_MARKERS = (
    "su questo pc", "su questo computer", "su questa macchina",
    "sul mio pc", "sul mio computer", "sul mio portatile", "sul mio fisso",
    "localmente", "in locale", "qui sul pc", "sul pc locale",
    "on this pc", "on this computer", "on this machine",
    "on my pc", "on my computer", "on my laptop", "on my machine", "locally",
)
# Marcatori «server / .33» → riporta al server.
_SERVER_MARKERS = (
    "sul server", "qui sul server", "sul .33", "sul metnos", "lato server",
    "on the server", "server side",
)


@dataclass
class TargetResolution:
    """Esito della risoluzione. `target` = SERVER oppure device_id."""
    status: str = "ok"                # "ok" | "ambiguous" | "unreachable"
    target: str = SERVER              # "server" | device_id
    device_name: str | None = None    # nome del device risolto (None = server)
    explicit: bool = False            # la query nominava esplicitamente un target?
    candidates: list = field(default_factory=list)   # per status="ambiguous": [(id,name)]
    unreachable_name: str | None = None              # per status="unreachable"
    cleaned_query: str = ""           # query senza l'adjunct di destinazione


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _find_marker(qn: str, markers) -> str | None:
    for m in markers:
        if re.search(r"(?<![a-z0-9])" + re.escape(m) + r"(?![a-z0-9])", qn):
            return m
    return None


def _find_named_device(qn: str, devices):
    """Ritorna (device, matched_span_text) se un nome-device compare ANCORATO
    da una preposizione locativa. Preferisce il match più lungo (nome più
    specifico) per disambiguare nomi che sono prefisso l'uno dell'altro."""
    best = None
    best_len = 0
    for d in devices:
        name = _norm(getattr(d, "name", "") or "")
        if len(name) < 3:
            continue  # nomi troppo corti = rischio falso positivo, salta
        pat = r"(?<![a-z0-9])" + _PREP + r"\s+[\"']?" + re.escape(name) + r"(?![a-z0-9])"
        m = re.search(pat, qn)
        if m and len(name) > best_len:
            best, best_len = (d, m.group(0)), len(name)
    return best  # (device, span) | None


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
    sm = _find_marker(qn, _SERVER_MARKERS)
    if sm:
        res.target = SERVER
        res.device_name = None
        res.explicit = True
        res.cleaned_query = _strip_span(query, sm)
        return res

    # --- NOME device esplicito (ancorato) ---
    named = _find_named_device(qn, devices)
    if named:
        dev, span = named
        res.explicit = True
        res.cleaned_query = _strip_span(query, span)
        if not is_available(dev, now):
            res.status = "unreachable"
            res.unreachable_name = getattr(dev, "name", None)
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
                res.target = dev.id
                res.device_name = getattr(dev, "name", None)
                res.explicit = False
                return res
            # appiccicosa ma offline: onesto, mai fallback silenzioso (§2.8)
            res.status = "unreachable"
            res.unreachable_name = getattr(dev, "name", None)
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
