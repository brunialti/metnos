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

import detection_lexicon as _detlex
import detection_lexicon_seed_resolvers as _resolver_seed
import i18n as _i18n


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
    template_it: str = ""        # compat API; i nuovi output vivono in i18n
    template_en: str = ""        # compat API; i nuovi output vivono in i18n
    requires_capability: bool = False  # se True passa per vaglio (default no)
    message_key: str = ""        # output nel catalogo i18n


# ── Tabella patterns ───────────────────────────────────────────────────
# IT + EN, esatto. Per estendere: aggiungi tuple a `patterns`. Per nuovo
# executor: append a `_FAST_PATTERNS`. Niente regex, niente fuzzy.
#
# get_now ritorna {ok, content (iso str), metadata: {timezone, iso8601, epoch}}.
# Template renderizza via `_render_get_now_message()` sotto.

def _is_ready_language(concept: str, lang: str | None) -> bool:
    requested = _i18n.normalize_language(lang or _detlex.current_lang())
    active = _i18n.normalize_language(_detlex.current_lang())
    if not requested or not active:
        return False
    # IT/EN sono baseline editoriali native e restano selezionabili per la
    # compat API ``lang=``. Una lingua terza e' valida solo quando coincide
    # con quella attiva, che le API native-ready sottopongono al gate.
    return requested == active or requested in set(
        _detlex.baseline_languages(concept)
    )


def _intent_mapping(lang: str | None = None) -> dict[str, list[str]]:
    _resolver_seed.ensure_registered()
    if not _is_ready_language("fast_path.intent_exact", lang):
        return {}
    return _detlex.native_ready_mapping(
        "fast_path.intent_exact",
        require_manual=True,
        include_reviewed_baselines=True,
    )


def _native_forms(concept: str, lang: str | None = None) -> tuple[str, ...]:
    _resolver_seed.ensure_registered()
    if not _is_ready_language(concept, lang):
        return ()
    return tuple(_detlex.native_ready_forms(
        concept,
        require_manual=True,
        include_reviewed_baselines=True,
    ))


_BOOTSTRAP_INTENTS = _intent_mapping()
_TIME_PATTERNS = tuple(_BOOTSTRAP_INTENTS.get("time", ()))
_CONFIGURED_TIMEZONE_PATTERNS = tuple(
    _BOOTSTRAP_INTENTS.get("configured_timezone", ()))
_DATE_PATTERNS = tuple(_BOOTSTRAP_INTENTS.get("date", ()))
_UNDO_PATTERNS = tuple(_BOOTSTRAP_INTENTS.get("undo", ()))
_LOCATION_PATTERNS = tuple(_BOOTSTRAP_INTENTS.get("location", ()))


_FAST_PATTERNS: list[FastPattern] = [
    FastPattern(
        patterns=_CONFIGURED_TIMEZONE_PATTERNS,
        executor="get_now",
        args={},
        message_key="MSG_FAST_TIME_TZ",
    ),
    FastPattern(
        patterns=_TIME_PATTERNS,
        executor="get_now",
        args={},  # timezone arriva da config.DEFAULT_TIMEZONE in try_fast_path
        message_key="MSG_FAST_TIME",
    ),
    FastPattern(
        patterns=_DATE_PATTERNS,
        executor="get_now",
        args={},
        message_key="MSG_FAST_DATE",
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
        message_key="MSG_FAST_UNDO",
    ),
    # get_location: query trivialemente single-step (#H0 19/5/2026 sera).
    # L'executor restituisce {lat, lon, ts, accuracy, channel}. Rendering
    # template renderizza coordinate. NO geocoding inverso qui — il PLANNER
    # rimane libero di chiamare find_places se l'utente lo richiede.
    FastPattern(
        patterns=_LOCATION_PATTERNS,
        executor="get_location",
        args={},
        message_key="MSG_FAST_LOCATION",
    ),
]

_FAST_PATTERN_BY_INTENT = dict(zip(
    ("configured_timezone", "time", "date", "undo", "location"),
    _FAST_PATTERNS,
))


# Pre-build di un dict pattern→FastPattern per lookup O(1).
_PATTERN_INDEX: dict[str, FastPattern] = {}
for fp in _FAST_PATTERNS:
    for p in fp.patterns:
        _PATTERN_INDEX[p] = fp


def _pattern_index(lang: str) -> dict[str, FastPattern]:
    mapping = _intent_mapping(lang)
    return {
        _normalize(form): fp
        for intent, fp in _FAST_PATTERN_BY_INTENT.items()
        for form in mapping.get(intent, ())
        if isinstance(form, str) and _normalize(form)
    }


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
_UNDO_PREFIX_TOKENS = _native_forms("fast_path.undo_prefix")


def _undo_prefix_match(norm: str, lang: str | None = None) -> bool:
    """Ritorna True se `norm` inizia con uno dei prefissi UNDO seguito da
    fine stringa o spazio. NON matcha sottostringhe casuali (es. "annulla"
    dentro a "annullamento" o "undoubted"). Match esatto su token-boundary.
    """
    if not norm:
        return False
    for tok in _native_forms("fast_path.undo_prefix", lang):
        tok = _normalize(tok)
        if norm == tok or norm.startswith(tok + " "):
            return True
    return False


# Riusa la FastPattern UNDO gia' definita in `_FAST_PATTERNS` per il render.
_UNDO_FALLBACK_FP: Optional[FastPattern] = None
for _fp in _FAST_PATTERNS:
    if _fp.executor == "undo_last_turn":
        _UNDO_FALLBACK_FP = _fp
        break


def _render_template(message_key: str, observation: dict, default_tz: str,
                     lang: str) -> str:
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
        return _i18n.get_for_language(
            message_key, lang,
            hhmm=iso[:5] if iso else "?",
            tz=tz, weekday="?", day="?", month="?", year="?",
        )
    wd = dt.weekday()
    return _i18n.get_for_language(
        message_key, lang,
        hhmm=dt.strftime("%H:%M"),
        tz=tz,
        weekday=_i18n.get_for_language(f"MSG_FAST_WEEKDAY_{wd}", lang),
        day=dt.day,
        month=_i18n.get_for_language(f"MSG_FAST_MONTH_{dt.month}", lang),
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
_IDENTITY_EXACT = frozenset(_BOOTSTRAP_INTENTS.get("identity", ()))


def _identity_match(norm: str, lang: str | None = None) -> bool:
    identities = {
        _normalize(form) for form in _intent_mapping(lang).get("identity", ())
    }
    if norm in identities:
        return True
    # Suffisso: «…tu chi sei», «no roberto sono io tu chi sei» → identità.
    return any(
        norm.endswith(" " + _normalize(suffix))
        for suffix in _native_forms("fast_path.identity_suffix", lang)
        if _normalize(suffix)
    )


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
    if _identity_match(norm, lang):
        _resolver_seed.ensure_output_registered()
        return {
            "direct_answer": _i18n.get_for_language("MSG_FAST_IDENTITY", lang),
            "pattern": "identity:" + norm,
            "executor": None,
            "args": {},
        }
    fp = _pattern_index(lang).get(norm)
    if fp is None:
        # Fallback safety-critical: prefisso UNDO (annulla/undo/...) cattura
        # tutte le varianti non elencate in _UNDO_PATTERNS senza esplodere
        # la tabella. Solo per il caso UNDO (semantica chiusa, non distruttiva).
        if _UNDO_FALLBACK_FP is not None and _undo_prefix_match(norm, lang):
            fp = _UNDO_FALLBACK_FP
        else:
            return None

    # Il provisioning degli output e' deliberatamente successivo al match:
    # un catalogo output indisponibile non autorizza mai un route fast-path.
    _resolver_seed.ensure_output_registered()

    args = dict(fp.args)
    # get_now accetta `timezone` con default UTC. Iniettiamo il default
    # progetto (Europe/Rome) cosi' la final_answer ha timezone locale.
    if fp.executor == "get_now" and "timezone" not in args:
        args["timezone"] = default_timezone

    def _render(observation: dict) -> str:
        if not observation.get("ok"):
            # Caso speciale undo_last_turn ok=False (nothing-to-undo): NON
            # un errore, e' uno stato legittimo. Messaggio dedicato.
            if fp.executor == "undo_last_turn":
                undone = observation.get("undone_count") or 0
                if undone == 0:
                    return _i18n.get_for_language(
                        "MSG_FAST_NOTHING_UNDO", lang,
                    )
            # get_location ok=False: posizione non condivisa / non disponibile.
            if fp.executor == "get_location":
                return _i18n.get_for_language(
                    "MSG_FAST_LOCATION_RECENT_MISSING", lang,
                )
            return _i18n.get_for_language(
                "ERR_FAST_EXECUTOR", lang, executor=fp.executor,
                error=observation.get("error", "unknown"),
            )
        # undo_last_turn ok=True: render dai details + undone_count.
        if fp.executor == "undo_last_turn":
            undone = observation.get("undone_count") or 0
            details = observation.get("details") or []
            d0 = details[0] if details else {}
            target_executor = d0.get("executor", "action")
            target_count = d0.get("ok_count", undone)
            return _i18n.get_for_language(
                "MSG_FAST_UNDO", lang, executor=target_executor,
                count=target_count,
            )
        if fp.executor == "get_location":
            loc = observation.get("location") or {}
            lat = loc.get("lat")
            lon = loc.get("lon")
            age = observation.get("age_seconds")
            if lat is None or lon is None:
                return _i18n.get_for_language(
                    "MSG_FAST_LOCATION_MISSING", lang,
                )
            age_str = ""
            if isinstance(age, (int, float)):
                if age < 60:
                    age_key, age_value = "MSG_FAST_AGE_SECONDS", int(age)
                elif age < 3600:
                    age_key, age_value = "MSG_FAST_AGE_MINUTES", int(age / 60)
                else:
                    age_key, age_value = "MSG_FAST_AGE_HOURS", int(age / 3600)
                age_str = _i18n.get_for_language(
                    age_key, lang, value=age_value,
                )
            return _i18n.get_for_language(
                "MSG_FAST_LOCATION", lang, lat=lat, lon=lon, age=age_str,
            )
        return _render_template(
            fp.message_key, observation, default_timezone, lang,
        )

    return {
        "executor": fp.executor,
        "args": args,
        "render": _render,
        "pattern": norm,
    }
