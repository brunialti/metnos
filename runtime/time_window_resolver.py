# SPDX-License-Identifier: AGPL-3.0-only
"""time_window_resolver.py — estrazione DETERMINISTICA della finestra NL.

Problema generale (bug live 11/6/2026, faglia 2 del routing mail):
«controlla tutte le mie mailbox ultime 24 ore» eseguiva read_messages SENZA
`time_window` — le «24 ore» non arrivavano mai all'arg. Sia il proposer LLM
(che copia la FORMA dal PATTERN del manifest, §2.5) sia i piani SERVITI da
un layer di cache (L1 champion / L0) perdono la finestra espressa dalla
query ATTUALE. Model-independent e layer-independent.

Soluzione (gemello di `mail_account_resolver`, stessa filosofia §7.9): il
segnale robusto e' la QUERY, non gli arg del piano. Quando la query esprime
una finestra temporale RELATIVA, il runtime canonicalizza l'arg
`time_window` (§2.1: «ultime N ore»→`last-Nh`, «ultimi N giorni»→`last-Nd`,
«oggi»→`today`, «ieri»→`yesterday`). Deterministico (regex, zero LLM),
single-point (engine/executor.py, catena resolver).

Distinto da `time_window_parser.py`: quello valida i VALORI canonici
(spec→bounds, lato executor); questo ESTRAE la spec dalla query NL
(lato runtime). Il vocabolario emesso e' il core supportato da TUTTI i
consumer di `time_window` (email_metnos._resolve_window,
time_window_parser, find_images_indices._parse_time_window):
`today | yesterday | last-Nh | last-Nd`.

Confini (§2.8, mai inventare una finestra):
- solo se l'utente ESPRIME la finestra: query senza tempo → nessun
  `time_window` spurio; espressioni non riconosciute (numeri in lettere,
  «settimana scorsa» = settimana di CALENDARIO ≠ rolling) → noop, decide
  il planner;
- solo tool il cui args_schema DICHIARA `time_window` (schema-gated §7.3);
- solo verbi produttori non-mutating (read/find/get/list — mai iniettare
  una finestra in un delete/move: cambierebbe il perimetro dell'azione);
- `since`/`before` espliciti nel piano (bound assoluti) → noop, vincono
  per contratto manifest;
- piu' espressioni in conflitto → vince la FORMA ESPLICITA N+unita'
  («oggi voglio le mail delle ultime 48 ore» → last-48h: «oggi» li' e'
  discorsivo); a parita' di forma vince la prima in ordine di lettura.
  Deterministico.
"""
from __future__ import annotations

import re

# Verbi-testa per cui l'iniezione della finestra e' sicura: produttori
# read-only. MAI mutating (delete/move/write/...): la finestra cambierebbe
# il perimetro di un'azione irreversibile.
_SAFE_VERB_HEADS = frozenset({"read", "find", "get", "list"})

# Unita' → suffisso canonico. Ore: «ore/ora/h/hours/hrs». Giorni:
# «giorni/giorno/gg/days/d».
_HOURS = r"(?:or[ae]\b|h\b|hours?\b|hrs?\b)"
_DAYS = r"(?:giorn[oi]\b|gg\b|days?\b|d\b)"

# Determinante IT (ultime/scorse/passate, ogni genere/numero) e EN
# (last/past). Word-bounded, case-insensitive a livello di scan.
_DET_IT = r"(?:ultim[aeio]|scors[aeio]|passat[aeio])"
_DET_EN = r"(?:last|past)"

# Pattern (regex, builder, priority): builder riceve il Match e ritorna la
# spec canonica o None (N invalido). N a cifre (1-4 digit); numeri in
# lettere fuori confine (noop). priority 0 = forma esplicita N+unita' /
# singolare nudo (segnale forte); 1 = oggi/ieri (spesso discorsivi:
# perdono il conflitto contro una finestra esplicita).
_PATTERNS: list[tuple[re.Pattern, object, int]] = []


def _n(m: re.Match) -> int | None:
    try:
        n = int(m.group(1))
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 9999 else None


def _add(rx: str, build, priority: int = 0) -> None:
    _PATTERNS.append((re.compile(rx, re.IGNORECASE), build, priority))


# N + ore: «ultime 24 ore», «nelle scorse 12h», «last 24 hours», «past 6 hrs»
_add(rf"\b{_DET_IT}\s+(\d{{1,4}})\s*{_HOURS}",
     lambda m: f"last-{_n(m)}h" if _n(m) else None)
_add(rf"\b{_DET_EN}\s+(\d{{1,4}})\s*{_HOURS}",
     lambda m: f"last-{_n(m)}h" if _n(m) else None)
# N + giorni: «ultimi 3 giorni», «scorsi 2 gg», «last 7 days»
_add(rf"\b{_DET_IT}\s+(\d{{1,4}})\s*{_DAYS}",
     lambda m: f"last-{_n(m)}d" if _n(m) else None)
_add(rf"\b{_DET_EN}\s+(\d{{1,4}})\s*{_DAYS}",
     lambda m: f"last-{_n(m)}d" if _n(m) else None)
# Singolare nudo (N=1): «ultima ora», «ultimo giorno», «last hour», «past day»
_add(rf"\b{_DET_IT}\s+ora\b", lambda m: "last-1h")
_add(rf"\b{_DET_EN}\s+hour\b", lambda m: "last-1h")
_add(rf"\b{_DET_IT}\s+giorno\b", lambda m: "last-1d")
_add(rf"\b{_DET_EN}\s+day\b", lambda m: "last-1d")
# Postfix IT: «l'ora scorsa», «il giorno passato», «le 24 ore passate»
_add(rf"\b(\d{{1,4}})\s*{_HOURS}\s+(?:scors[ae]|passat[ae])\b",
     lambda m: f"last-{_n(m)}h" if _n(m) else None)
_add(rf"\b(\d{{1,4}})\s*{_DAYS}\s+(?:scors[oi]|passat[oi])\b",
     lambda m: f"last-{_n(m)}d" if _n(m) else None)
_add(r"\bora\s+(?:scorsa|passata)\b", lambda m: "last-1h")
_add(r"\bgiorno\s+(?:scorso|passato)\b", lambda m: "last-1d")
# Giorno di calendario: «oggi», «today», «ieri», «yesterday»
_add(r"\boggi\b|\btoday\b", lambda m: "today", priority=1)
_add(r"\bieri\b|\byesterday\b", lambda m: "yesterday", priority=1)


def parse_query_time_window(query: str) -> str | None:
    """Estrae la finestra temporale RELATIVA espressa nella query NL.

    Ritorna la spec canonica (`today|yesterday|last-Nh|last-Nd`) o None se
    la query non esprime una finestra riconoscibile. Piu' match → vince la
    forma esplicita N+unita' (priority 0), poi il piu' a sinistra.
    Deterministico, mai eccezioni."""
    if not query or not isinstance(query, str):
        return None
    candidates: list[tuple[int, int, str]] = []
    for rx, build, priority in _PATTERNS:
        m = rx.search(query)
        if not m:
            continue
        spec = build(m)
        if spec:
            candidates.append((priority, m.start(), spec))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def resolve_time_window(tool: str, args: dict, query: str,
                        args_schema: dict | None = None) -> dict:
    """Canonicalizza `args['time_window']` dalla finestra espressa nella
    query. Ritorna args (copia se modificati). Mai eccezioni: su dubbio,
    noop. Schema-gated: agisce solo se `args_schema.properties` dichiara
    `time_window` (senza schema → noop conservativo)."""
    if not isinstance(args, dict) or not query or not tool:
        return args
    if tool.split("_", 1)[0] not in _SAFE_VERB_HEADS:
        return args
    props = (args_schema or {}).get("properties") \
        if isinstance(args_schema, dict) else None
    if not isinstance(props, dict) or "time_window" not in props:
        return args
    if args.get("since") or args.get("before"):
        return args  # bound assoluti espliciti: vincono per contratto
    spec = parse_query_time_window(query)
    if not spec:
        return args  # la query non esprime una finestra: mai spurio
    cur = args.get("time_window")
    if isinstance(cur, str) and cur.strip().lower() == spec:
        return args  # gia' canonico
    out = dict(args)
    out["time_window"] = spec
    return out
