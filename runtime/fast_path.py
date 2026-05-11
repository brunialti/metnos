"""fast_path.py — short-circuit deterministico per query triviali.

Pattern catch-all runtime-side prima del PLANNER LLM (parallelo a
ADR 0076 `synth_request` short-circuit). Quando una query utente matcha
ESATTAMENTE un pattern di altissima confidenza, il runtime invoca
direttamente l'executor giusto e formula la final_answer con un template
deterministico — ZERO chiamate LLM nella critical path.

Disciplina (CLAUDE.md §7.9 Determinismo > LLM, §2.4 robustezza al confine
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
_TRAILING_PUNCT = ".?!,;:"
# Whitespace multipli.
_WS_RE = re.compile(r"\s+")


def _normalize(query: str) -> str:
    """Normalizza la query per match esatto. Case-insensitive,
    apostrofi ASCII, niente punteggiatura finale, whitespace collassato.

    NON aggiungere normalizzazione semantica qui (es. stemming, sinonimi):
    il fast path e' deterministico per costruzione. Le varianti vivono
    nella tabella patterns sotto.
    """
    if not query:
        return ""
    q = query.strip().lower()
    for src, dst in _CURLY_APO.items():
        q = q.replace(src, dst)
    # Strip leading/trailing punctuation (anche lettere accentate vanno
    # gestite a tabella, NON qui — niente decompose unicode).
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
]


# Pre-build di un dict pattern→FastPattern per lookup O(1).
_PATTERN_INDEX: dict[str, FastPattern] = {}
for fp in _FAST_PATTERNS:
    for p in fp.patterns:
        _PATTERN_INDEX[p] = fp


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
# Razionale (ADR 0098 §c1 esteso): la regola PLANNER (Z) "URL esplicito →
# read_urls_html primo step" e' provata insufficiente live (turn federvolley
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
    fp = _PATTERN_INDEX.get(norm)
    if fp is None:
        return None

    args = dict(fp.args)
    # get_now accetta `timezone` con default UTC. Iniettiamo il default
    # progetto (Europe/Rome) cosi' la final_answer ha timezone locale.
    if fp.executor == "get_now" and "timezone" not in args:
        args["timezone"] = default_timezone

    tpl = fp.template_it if lang == "it" else fp.template_en

    def _render(observation: dict) -> str:
        if not observation.get("ok"):
            err = observation.get("error", "sconosciuto")
            return (f"Errore in {fp.executor}: {err}" if lang == "it"
                     else f"Error in {fp.executor}: {err}")
        return _render_template(tpl, observation, default_timezone)

    return {
        "executor": fp.executor,
        "args": args,
        "render": _render,
        "pattern": norm,
    }
