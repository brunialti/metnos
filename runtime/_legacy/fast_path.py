"""fast_path.py — short-circuit deterministico per query triviali.

Pattern catch-all runtime-side prima del PLANNER LLM (parallelo a
ADR 0076 `synth_request` short-circuit). Quando una query utente matcha
ESATTAMENTE un pattern di altissima confidenza, il runtime invoca
direttamente l'executor giusto e formula la final_answer con un template
deterministico — ZERO chiamate LLM nella critical path.

Disciplina (the design guide §7.9 Determinismo > LLM, §2.4 robustezza al confine
NL→determinismo, §7.2 semplicita'):
- Tabella modulo-level chiusa, ampliabile in append-only.
- Match per `exact` su query normalizzata (lowercase, apostrofi
  tipografici, punteggiatura finale, whitespace collapse).
- Sull'incertezza → ritorna None, caller fa fallback al normale flusso.
- Niente regex complesse, niente engine pluggable.

Iniziale: solo `get_now` (mapping 1:1, nessun argomento NL). Pattern
piu' complessi (find_*, read_*) NON entrano qui: hanno argomenti, hanno
varianti semantiche, vanno al PLANNER.

ADR 0094.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# Sostituzioni curly apostrophes → ASCII apostrophe (resilienza UI mobile).
_CURLY_APO = {"’": "'", "‘": "'", "ʼ": "'"}
# Punteggiatura finale da scartare.
# Trailing punctuation da scartare. Include apostrofo cosi' "e'"
# (forma italiana di "è" senza accento) diventa "e" dopo strip.
# Strip e' leading+trailing only: apostrofi in mezzo (l'ora) NON
# vengono toccati.
_TRAILING_PUNCT = ".?!,;:'"
# Whitespace multipli.
_WS_RE = re.compile(r"\s+")


def _normalize(query: str) -> str:
    """Normalizza la query per match esatto. Case-insensitive, accent-fold
    via Unicode NFKD, apostrofi ASCII, niente punteggiatura finale,
    whitespace collassato.

    Accent-fold (20/5 v6): "e'", "e", "è" (e + combining grave)
    e "è" tutti convergono a "e". Lang-independent: lo stesso meccanismo
    vale per `é`, `ñ`, `ü`, `ç`, etc. senza tabelle per-lingua. Razionale:
    una tabella patterns esaustiva con tutte le varianti accentate esplode
    O(N varianti per ogni lemma). NFKD + drop combining marks risolve in
    una riga.

    NON aggiungere normalizzazione semantica qui (es. stemming, sinonimi):
    il fast path e' deterministico per costruzione. Le varianti SEMANTICHE
    (sinonimi/parafrasi) vivono nella tabella patterns; le varianti
    LESSICALI (accenti, punteggiatura) sono normalizzate qui.
    """
    if not query:
        return ""
    q = query.strip().lower()
    for src, dst in _CURLY_APO.items():
        q = q.replace(src, dst)
    # Unicode NFKD + drop combining marks (Mn category):
    # "è" (U+00E8) → "e" + U+0300 → "e" (Mn dropped). Funziona per
    # qualunque scrittura latina, greca, cirillica, etc.
    import unicodedata as _ud
    q = "".join(
        ch for ch in _ud.normalize("NFKD", q)
        if _ud.category(ch) != "Mn"
    )
    # Strip leading/trailing punctuation.
    q = q.strip(_TRAILING_PUNCT + " \t\n")
    q = _WS_RE.sub(" ", q)
    return q


@dataclass(frozen=True)
class FastPattern:
    patterns: tuple              # set chiuso di stringhe normalizzate
    executor: str                # nome canonico in catalog
    args: dict                   # args literal (NIENTE placeholder NL)
    template_it: str             # final_message IT con placeholder {iso}/{tz}/...
    template_en: str             # final_message EN
    requires_capability: bool = False  # se True passa per vaglio (default no)


# ── Tabella patterns ───────────────────────────────────────────────────
# IT + EN, esatto. Per estendere: aggiungi tuple a `patterns`. Per nuovo
# executor: append a `_FAST_PATTERNS`. Niente regex, niente fuzzy.
#
# get_now ritorna {ok, content (iso str), metadata: {timezone, iso8601, epoch}}.
# Template renderizza via `_render_get_now_message()` sotto.

_TIME_PATTERNS = (
    # IT — ora
    "che ora e",
    "che ore sono",
    "che ora",
    "che ore",
    "dimmi l'ora",
    "dimmi che ore sono",
    "ora attuale",
    # EN — time
    "what time is it",
    "what's the time",
    "whats the time",
    "what time",
    "current time",
    "tell me the time",
)

_DATE_PATTERNS = (
    # IT — data/giorno
    "che giorno e oggi",
    "che data e oggi",
    "che data e",
    "che data",
    "che giorno",
    "data odierna",
    "oggi che giorno e",
    # EN — date/day
    "what date is it",
    "what's the date",
    "what date",
    "today's date",
    "current date",
    "what day is it",
)

_UNDO_PATTERNS = (
    "annulla", "annulla ultima azione", "annulla l'ultima azione",
    "annullare", "annullo", "annulla turn", "annulla l'ultimo turno",
    "annulla ultimo evento", "annulla ultimo messaggio",
    "undo", "undo last", "undo last action", "undo last turn",
    "revert", "revert last", "rollback", "rollback last",
    "ripristina", "ripristina turno precedente",
)

# get_location: query ovvie sulla propria posizione. Pattern stretti
# (esatti, no fuzzy) per evitare false positive su query con verbi
# d'azione tipo "sposta i file dove sono ora" (NB: queste hanno verbo
# `sposta` PRIMA di "dove sono", quindi NON matchano per _normalize +
# lookup esatto).
_LOCATION_PATTERNS = (
    # IT
    "dove sono",
    "dove mi trovo",
    "posizione attuale",
    "mia posizione",
    "qual'e' la mia posizione",
    "qual e la mia posizione",
    # EN
    "where am i",
    "current location",
    "my location",
    "my current location",
    "what is my location",
)


_FAST_PATTERNS: list[FastPattern] = [
    FastPattern(
        patterns=_TIME_PATTERNS,
        executor="get_now",
        args={},  # timezone arriva da config.DEFAULT_TIMEZONE in try_fast_path
        template_it="Sono le {hhmm} ({tz}).",
        template_en="It's {hhmm} ({tz}).",
    ),
    FastPattern(
        patterns=_DATE_PATTERNS,
        executor="get_now",
        args={},
        template_it="Oggi e' {weekday_it} {day} {month_it} {year}.",
        template_en="Today is {weekday_en}, {month_en} {day}, {year}.",
    ),
    # Safety-critical: query «annulla ...» bypassa il PLANNER LLM e va dritta
    # a undo_last_turn (Metnos-action perspective). Bug live turn 6c6a0076
    # (11/5/2026 sera): «annulla ultimo evento» -> planner pesco delete_events
    # destructive sul calendario dell'utente, cancellando un evento legittimo
    # (COMMERCIALISTA) invece di rovesciare la set_events della sessione.
    # _UNDO_PATTERNS riusa la stessa lista di intent_extractor.py per
    # consistenza semantica IT+EN.
    FastPattern(
        patterns=_UNDO_PATTERNS,
        executor="undo_last_turn",
        args={},
        template_it="",  # output formattato dall'executor stesso
        template_en="",
    ),
    # get_location: query trivialemente single-step (#H0 19/5/2026 sera).
    # L'executor restituisce {lat, lon, ts, accuracy, channel}. Rendering
    # template renderizza coordinate. NO geocoding inverso qui — il PLANNER
    # rimane libero di chiamare find_places se l'utente lo richiede.
    FastPattern(
        patterns=_LOCATION_PATTERNS,
        executor="get_location",
        args={},
        template_it="",  # render speciale in _render via observation
        template_en="",
    ),
]


# Pre-build di un dict pattern→FastPattern per lookup O(1).
_PATTERN_INDEX: dict[str, FastPattern] = {}
for fp in _FAST_PATTERNS:
    for p in fp.patterns:
        _PATTERN_INDEX[p] = fp


# Prefissi UNDO safety-critical: query che INIZIANO con uno di questi
# token (case-insensitive, dopo `_normalize`) routano deterministicamente
# a `undo_last_turn` indipendentemente dal resto. Razionale: nessuna
# semantica utente in cui "annulla X" / "undo X" non sia annullamento; al
# contempo non possiamo elencare tutte le varianti possibili di X
# (es. "annulla l'ultima azione", "annulla l'evento appena creato", ...).
# Per i due verbi `annulla`/`undo` la sicurezza viene PRIMA della precisione:
# meglio un fast-path occasionalmente over-confidente che lasciare il
# PLANNER LLM scegliere `delete_events` distruttivo. Bug live turn 742b746d
# (11/5/2026 sera): «annulla ultimo evento» con candidates
# [delete_events, read_events, set_events, undo_last_turn, admin] -> per
# fortuna PLANNER scelse undo_last_turn, ma in turn precedenti aveva
# scelto delete_events su evento legittimo dell'utente. §7.9 deterministico.
_UNDO_PREFIX_TOKENS = (
    "annulla",  # IT: copre "annulla", "annulla X", "annulla l'ultimo X"
    "annullare",
    "annullo",
    "undo",     # EN: copre "undo", "undo X", "undo last action"
    "rollback",
    "ripristina",
    "revert",
)


def _undo_prefix_match(norm: str) -> bool:
    """Ritorna True se `norm` inizia con uno dei prefissi UNDO seguito da
    fine stringa o spazio. NON matcha sottostringhe casuali (es. "annulla"
    dentro a "annullamento" o "undoubted"). Match esatto su token-boundary.
    """
    if not norm:
        return False
    for tok in _UNDO_PREFIX_TOKENS:
        if norm == tok or norm.startswith(tok + " "):
            return True
    return False


# Riusa la FastPattern UNDO gia' definita in `_FAST_PATTERNS` per il render.
_UNDO_FALLBACK_FP: Optional[FastPattern] = None
for _fp in _FAST_PATTERNS:
    if _fp.executor == "undo_last_turn":
        _UNDO_FALLBACK_FP = _fp
        break


_WEEKDAY_IT = ["lunedi'", "martedi'", "mercoledi'", "giovedi'",
                "venerdi'", "sabato", "domenica"]
_WEEKDAY_EN = ["Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday"]
_MONTH_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
              "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
_MONTH_EN = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]


def _render_template(tpl: str, observation: dict, default_tz: str) -> str:
    """Rendering deterministico delle variabili dal result di get_now.

    Estrae iso8601 dalla `metadata` e parsa per ottenere componenti
    locali (hh:mm, weekday, day, month, year). Niente LLM.
    """
    meta = (observation.get("metadata") or {})
    iso = meta.get("iso8601") or observation.get("content") or ""
    tz = meta.get("timezone", default_tz)
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        # Fallback degenere: ritorna il template con iso letterale.
        return tpl.format(
            hhmm=iso[:5] if iso else "?",
            tz=tz, weekday_it="?", weekday_en="?",
            day="?", month_it="?", month_en="?", year="?",
        )
    wd = dt.weekday()
    return tpl.format(
        hhmm=dt.strftime("%H:%M"),
        tz=tz,
        weekday_it=_WEEKDAY_IT[wd],
        weekday_en=_WEEKDAY_EN[wd],
        day=dt.day,
        month_it=_MONTH_IT[dt.month - 1],
        month_en=_MONTH_EN[dt.month - 1],
        year=dt.year,
    )


# ─── Seed-step injection (ADR 0099) ──────────────────────────────────────
#
# Quando la query utente contiene un URL completo (con path), il primo step
# del runtime e' DETERMINISTICAMENTE `read_urls_html(urls=[<URL>])`. Il
# PLANNER LLM riceve il risultato in history e prende il controllo dallo
# step 2 in poi.
#
# Razionale (ADR 0098 §c1 esteso): la regola PLANNER (url_explicit_seed)
# "URL esplicito → read_urls_html primo step" e' provata insufficiente live (turn federvolley
# 7/5/2026 15:29: PLANNER ha comunque scelto find_urls). Il segnale «URL
# specifico» e' un fatto strutturale, non interpretabile: il runtime puo'
# garantirlo deterministicamente. PLANNER resta libero per gli step 2+.
#
# Disciplina §7.9: niente LLM nel routing; pattern catch-all.

_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
# Caratteri di chiusura comuni che NON appartengono all'URL.
_URL_TRAILING_STRIP = ".,;:)]}\"'"


def try_seed_step(query: str) -> Optional[dict]:
    """Inietta il primo step deterministico quando la query contiene un URL.

    Returns:
        None se nessun URL nella query.
        dict con shape:
          {
            "executor": "read_urls_html",
            "args": {"urls": [URL]},
            "url": URL,           # primo URL trovato (debug/audit)
          }
        se match.

    Niente effetto sul flusso PLANNER post-step1: e' solo un'iniezione
    dello step 1. Caller e' responsabile di append-and-continue.
    """
    if not query:
        return None
    m = _URL_RE.search(query)
    if not m:
        return None
    url = m.group(0).rstrip(_URL_TRAILING_STRIP)
    # Validazione minima: deve avere un netloc reale.
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        if not p.netloc or "." not in p.netloc:
            return None
    except Exception:
        return None
    return {
        "executor": "read_urls_html",
        "args": {"urls": [url]},
        "url": url,
    }


# ── Identità dell'ASSISTENTE (deterministico, prima di Praxis) ──────────
# "chi sei" ≠ "chi sono io": la prima è l'identità dell'assistente, la seconda
# il profilo dell'utente. Senza questo, il cache Praxis instrada entrambe a
# read_persons(actor) → dump del profilo+email dell'utente (bug live 5/6/2026).
_IDENTITY_EXACT = frozenset({
    "chi sei", "chi sei tu", "tu chi sei", "ma chi sei", "e tu chi sei",
    "chi sei esattamente", "cosa sei", "che cosa sei", "sei un assistente",
    "sei un ai", "sei un'ai", "sei metnos", "presentati", "chi e metnos",
    "who are you", "who are you?", "what are you", "what are you?",
    "are you an assistant", "are you an ai", "are you metnos",
    "introduce yourself", "tell me who you are",
})
_IDENTITY_ANSWER_IT = (
    "Sono Metnos, un assistente personale self-hosted che gira sulla tua "
    "macchina (via Telegram e interfaccia web). Ti aiuto con file, posta, "
    "foto, calendario, web e altro — solo le funzioni che attivi tu. Come "
    "posso aiutarti?"
)
_IDENTITY_ANSWER_EN = (
    "I am Metnos, a self-hosted personal assistant running on your own machine "
    "(via Telegram and a web UI). I help with files, mail, photos, calendar, "
    "the web and more — only the capabilities you switch on. How can I help?"
)


def _identity_match(norm: str) -> bool:
    if norm in _IDENTITY_EXACT:
        return True
    # Suffisso: «…tu chi sei», «no roberto sono io tu chi sei» → identità.
    return norm.endswith(" chi sei") or norm.endswith(" tu chi sei") \
        or norm.endswith(" who are you")


def try_fast_path(query: str, lang: str = "it",
                   default_timezone: str = "Europe/Rome") -> Optional[dict]:
    """Tenta match deterministico di una query contro i pattern fast.

    Returns:
        None se nessun match (caller fa fallback PLANNER).
        dict con shape:
          {
            "executor": str,         # nome in catalog
            "args": dict,            # args con timezone iniettato
            "render": callable,      # (observation) -> final_message str
            "pattern": str,          # pattern matched (debug/audit)
          }
        se match.

    NON invoca l'executor (caller responsabilita'): cosi' il modulo resta
    puro (testabile senza filesystem/sandbox).
    """
    norm = _normalize(query)
    if not norm:
        return None
    # Identità assistente: risposta diretta, nessun executor (no read_persons).
    if _identity_match(norm):
        return {
            "direct_answer": _IDENTITY_ANSWER_IT if lang == "it" else _IDENTITY_ANSWER_EN,
            "pattern": "identity:" + norm,
            "executor": None,
            "args": {},
        }
    fp = _PATTERN_INDEX.get(norm)
    if fp is None:
        # Fallback safety-critical: prefisso UNDO (annulla/undo/...) cattura
        # tutte le varianti non elencate in _UNDO_PATTERNS senza esplodere
        # la tabella. Solo per il caso UNDO (semantica chiusa, non distruttiva).
        if _UNDO_FALLBACK_FP is not None and _undo_prefix_match(norm):
            fp = _UNDO_FALLBACK_FP
        else:
            return None

    args = dict(fp.args)
    # get_now accetta `timezone` con default UTC. Iniettiamo il default
    # progetto (Europe/Rome) cosi' la final_answer ha timezone locale.
    if fp.executor == "get_now" and "timezone" not in args:
        args["timezone"] = default_timezone

    tpl = fp.template_it if lang == "it" else fp.template_en

    def _render(observation: dict) -> str:
        if not observation.get("ok"):
            # Caso speciale undo_last_turn ok=False (nothing-to-undo): NON
            # un errore, e' uno stato legittimo. Messaggio dedicato.
            if fp.executor == "undo_last_turn":
                undone = observation.get("undone_count") or 0
                if undone == 0:
                    return ("Niente da annullare: nessuna azione reversibile nel turno precedente."
                            if lang == "it"
                            else "Nothing to undo: no reversible action in the previous turn.")
            # get_location ok=False: posizione non condivisa / non disponibile.
            if fp.executor == "get_location":
                return ("Posizione non disponibile (nessuna condivisione recente)."
                        if lang == "it"
                        else "Location not available (no recent share).")
            err = observation.get("error", "sconosciuto")
            return (f"Errore in {fp.executor}: {err}" if lang == "it"
                     else f"Error in {fp.executor}: {err}")
        # undo_last_turn ok=True: render dai details + undone_count.
        if fp.executor == "undo_last_turn":
            undone = observation.get("undone_count") or 0
            details = observation.get("details") or []
            d0 = details[0] if details else {}
            target_executor = d0.get("executor", "azione")
            target_count = d0.get("ok_count", undone)
            if lang == "it":
                return f"Annullato: {target_executor} ({target_count} elementi)."
            return f"Undone: {target_executor} ({target_count} items)."
        if fp.executor == "get_location":
            loc = observation.get("location") or {}
            lat = loc.get("lat")
            lon = loc.get("lon")
            age = observation.get("age_seconds")
            if lat is None or lon is None:
                return ("Posizione non disponibile."
                        if lang == "it" else "Location not available.")
            age_str = ""
            if isinstance(age, (int, float)):
                if age < 60:
                    age_str = (f" (aggiornata {int(age)}s fa)" if lang == "it"
                                else f" (updated {int(age)}s ago)")
                elif age < 3600:
                    age_str = (f" (aggiornata {int(age/60)}min fa)" if lang == "it"
                                else f" (updated {int(age/60)}min ago)")
                else:
                    age_str = (f" (aggiornata {int(age/3600)}h fa)" if lang == "it"
                                else f" (updated {int(age/3600)}h ago)")
            if lang == "it":
                return f"Posizione: {lat:.4f}, {lon:.4f}{age_str}."
            return f"Location: {lat:.4f}, {lon:.4f}{age_str}."
        return _render_template(tpl, observation, default_timezone)

    return {
        "executor": fp.executor,
        "args": args,
        "render": _render,
        "pattern": norm,
    }
