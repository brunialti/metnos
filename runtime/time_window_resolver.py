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
`today | yesterday | last-Nh | last-Nd | last-Nw | last-Nm | last-Ny`.

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
# Settimane/mesi/anni: forma esplicita N+unita' (segnale forte, rolling). Il
# bare singolare («ultimo mese/anno», «settimana scorsa») resta NOOP perche'
# ambiguo (calendario vs rolling) → decide il planner. Vocabolario canonico
# single-char condiviso da TUTTI i consumer (last-Nw/last-Nm/last-Ny;
# m=mesi~30g, y=anni~365g — vedi time_window_parser/email_metnos/find_images).
_WEEKS = r"(?:settiman[ae]\b|sett\b|weeks?\b|w\b)"
_MONTHS = r"(?:mes[ei]\b|months?\b|m\b)"
_YEARS = r"(?:ann[oi]\b|years?\b|y\b)"

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
# N + settimane/mesi/anni: «ultimi 12 mesi», «last 2 weeks», «ultimi 3 anni».
# Anche postfix IT: «12 mesi fa/scorsi». Forma esplicita → priority 0.
_add(rf"\b{_DET_IT}\s+(\d{{1,4}})\s*{_WEEKS}",
     lambda m: f"last-{_n(m)}w" if _n(m) else None)
_add(rf"\b{_DET_EN}\s+(\d{{1,4}})\s*{_WEEKS}",
     lambda m: f"last-{_n(m)}w" if _n(m) else None)
_add(rf"\b{_DET_IT}\s+(\d{{1,4}})\s*{_MONTHS}",
     lambda m: f"last-{_n(m)}m" if _n(m) else None)
_add(rf"\b{_DET_EN}\s+(\d{{1,4}})\s*{_MONTHS}",
     lambda m: f"last-{_n(m)}m" if _n(m) else None)
_add(rf"\b{_DET_IT}\s+(\d{{1,4}})\s*{_YEARS}",
     lambda m: f"last-{_n(m)}y" if _n(m) else None)
_add(rf"\b{_DET_EN}\s+(\d{{1,4}})\s*{_YEARS}",
     lambda m: f"last-{_n(m)}y" if _n(m) else None)
# Postfix «N <unita'> fa/scorsi/passati»: «12 mesi fa», «2 anni scorsi»
_add(rf"\b(\d{{1,4}})\s*{_WEEKS}\s+(?:fa\b|scors[ae]|passat[ae])",
     lambda m: f"last-{_n(m)}w" if _n(m) else None)
_add(rf"\b(\d{{1,4}})\s*{_MONTHS}\s+(?:fa\b|scors[ai]|passat[ai])",
     lambda m: f"last-{_n(m)}m" if _n(m) else None)
_add(rf"\b(\d{{1,4}})\s*{_YEARS}\s+(?:fa\b|scors[ai]|passat[ai])",
     lambda m: f"last-{_n(m)}y" if _n(m) else None)
# Singolare nudo (N=1): «ultima ora», «ultimo giorno», «last hour», «past day».
# Anche settimana/mese/anno: «(dell')ultimo anno», «last month» → rolling 1y/1m/1w
# (per i produttori read/find la lettura rolling e' l'interpretazione naturale;
# la finestra di CALENDARIO la chiede esplicitamente l'utente con date assolute).
_add(rf"\b{_DET_IT}\s+ora\b", lambda m: "last-1h")
_add(rf"\b{_DET_EN}\s+hour\b", lambda m: "last-1h")
_add(rf"\b{_DET_IT}\s+giorno\b", lambda m: "last-1d")
_add(rf"\b{_DET_EN}\s+day\b", lambda m: "last-1d")
_add(rf"\b{_DET_IT}\s+mese\b", lambda m: "last-1m")
_add(rf"\b{_DET_EN}\s+month\b", lambda m: "last-1m")
_add(rf"\b{_DET_IT}\s+anno\b", lambda m: "last-1y")
_add(rf"\b{_DET_EN}\s+year\b", lambda m: "last-1y")
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

# Anno di CALENDARIO assoluto («del 2026», «dell'anno 2026», «in 2026», «of
# 2026») — fix bug live 3/7: la query nomina un anno assoluto (un BOUND, non
# un offset rolling da "ora"), l'LLM tenta di esprimerlo come stringa
# "2026-01-01/2026-12-31" che NESSUN consumer riconosce (email_metnos._resolve_
# window: unknown_preset). Il manifest read_messages dichiara GIA' `since`/
# `before` come arg top-level stringa IMAP proprio per le finestre custom
# ("vince su time_window se entrambi presenti") — qui si valorizzano quelli,
# MAI un dict dentro time_window (che lo schema dichiara type=string, un
# dict lo violerebbe). Range 2000-2099: riduce falsi positivi su 4 cifre
# non-anno; parola-segnale IT/EN richiesta come per il resto del file.
_YEAR_RE = re.compile(
    r"\b(?:dell['a]?\s*anno|nell['a]?\s*anno|anno|del|dal|nel|of|in|year)\s+"
    r"(20\d{2})\b|\b(20\d{2})\s+year\b",
    re.IGNORECASE,
)
_MONTHS_IMAP = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _parse_absolute_year(query: str) -> int | None:
    """Anno di calendario assoluto esplicito nella query, o None. Vince solo
    se non c'e' GIA' un match rolling (vedi `resolve_time_window`): «ultimi
    2 anni» resta rolling, «del 2026» e' un anno di calendario."""
    if not query:
        return None
    m = _YEAR_RE.search(query)
    if not m:
        return None
    y = int(m.group(1) or m.group(2))
    return y if 2000 <= y <= 2099 else None


def _year_bounds_imap(year: int) -> tuple[str, str]:
    """(since, before) IMAP (`DD-Mon-YYYY`) per l'intero anno di calendario.
    BEFORE e' esclusivo per contratto IMAP (RFC 3501): il bound superiore e'
    il 1° gennaio dell'anno SUCCESSIVO, non il 31 dicembre (altrimenti i
    messaggi del 31/12 verrebbero esclusi)."""
    return (f"01-{_MONTHS_IMAP[0]}-{year}", f"01-{_MONTHS_IMAP[0]}-{year + 1}")


def parse_query_time_window(query: str) -> str | None:
    """Estrae la finestra temporale RELATIVA espressa nella query NL.

    Ritorna la spec canonica (`today|yesterday|last-Nh|last-Nd|last-Nw|last-Nm|last-Ny`) o None se
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
    props = (args_schema or {}).get("properties") \
        if isinstance(args_schema, dict) else None
    if not isinstance(props, dict):
        return args
    spec = parse_query_time_window(query)

    # `filter_entries` non dichiara `time_window`: il suo contratto esprime
    # la stessa semantica tramite bound ISO `mtime_after`/`mtime_before`.
    # Colmare qui il bridge evita che un piano L0/L1 stantio perda «ultimi
    # N giorni» (turn live 1e998f93). Il gate sui DATI materializzati è
    # deliberato: `filter_entries` è cross-domain; iniettiamo mtime solo se
    # sta davvero consumando record filesystem con `mtime|mtime_epoch`, mai
    # su mail/eventi/task. Al record del piano gli entries possono non essere
    # ancora materializzati: l'Executor riapplica comunque questo resolver
    # dopo l'auto-wire, immediatamente prima dell'invoke.
    if (tool == "filter_entries"
            and "mtime_after" in props and "mtime_before" in props
            and spec):
        entries = args.get("entries")
        file_entries = (isinstance(entries, list) and bool(entries)
                        and all(isinstance(item, dict)
                                and (item.get("mtime") is not None
                                     or item.get("mtime_epoch") is not None)
                                for item in entries))
        if file_entries:
            try:
                from time_window_parser import parse_time_window
                start_iso, end_iso = parse_time_window(spec)
            except (ImportError, TypeError, ValueError):
                return args
            out = dict(args)
            out["mtime_after"] = start_iso
            out["mtime_before"] = end_iso
            # Un piano LLM/cachato puo' aver espresso la stessa finestra con
            # il filtro generico, usando un alias che i record filesystem non
            # espongono (es. modified_time=now_minus_60d). Tenere ENTRAMBE le
            # forme applica un AND e azzera correttamente i bound appena
            # risolti. Quando il campo generico e' un alias del tempo di
            # modifica, la finestra canonica mtime_* lo sussume: elimina solo
            # quel predicato e conserva qualunque filtro generico non
            # temporale (status, owner, ...).
            temporal_aliases = {
                "mtime", "modified_time", "modification_time",
                "modified_at", "last_modified", "last_modified_time",
            }
            where_field = str(out.get("where_field") or "").strip().casefold()
            if where_field in temporal_aliases:
                for key in (
                    "where_field", "where_value", "where_in", "where_not_in",
                    "where_starts_with", "where_contains", "where_glob",
                    "where_regex",
                ):
                    out.pop(key, None)
            return out if out != args else args
        return args

    if tool.split("_", 1)[0] not in _SAFE_VERB_HEADS:
        return args
    if "time_window" not in props:
        return args
    if args.get("since") or args.get("before"):
        return args  # bound assoluti espliciti: vincono per contratto
    if not spec:
        # Nessuna finestra ROLLING: prova l'anno di calendario assoluto (fix
        # 3/7). Valorizza since/before TOP-LEVEL (arg dedicati del manifest,
        # "vincono su time_window se presenti") — MAI un dict dentro
        # time_window, che lo schema dichiara type=string. Solo se il tool
        # dichiara ENTRAMBI since e before (schema-gated come il resto del
        # file): altri consumer di time_window (find_images_indices, ...)
        # potrebbero non averli, e restano noop di proposito.
        if "since" in props and "before" in props:
            year = _parse_absolute_year(query)
            if year is not None:
                since_v, before_v = _year_bounds_imap(year)
                if args.get("since") != since_v or args.get("before") != before_v:
                    out = dict(args)
                    out["since"] = since_v
                    out["before"] = before_v
                    out.pop("time_window", None)  # bound espliciti sostituiscono lo spec rotto
                    return out
        return args  # la query non esprime una finestra: mai spurio
    out = dict(args)
    cur = args.get("time_window")
    if not (isinstance(cur, str) and cur.strip().lower() == spec):
        out["time_window"] = spec
    # §2.4/§7.9 — DE-CONFLAZIONE: il numero della finestra («12» in «12 mesi») non
    # deve finire anche in un arg di CONTEGGIO. Bug live 21/6: «ultimi 12 mesi» →
    # l'LLM lega 12 a max_results=12 (legge solo 12 mail). Se la spec porta una N
    # e un arg-conteggio del manifest vale ESATTAMENTE quella N, era la finestra
    # mal-legata → rimuovilo (torna al default). General, deterministico, no
    # hardcoding: vale per ogni tool/arg-conteggio dichiarato nello schema.
    _mn = re.match(r"^last-(\d+)[hdwmy]$", spec)
    if _mn:
        n = int(_mn.group(1))
        for _ca in ("max_results", "max_total", "top_k", "top", "limit",
                    "max_results_total", "count"):
            if _ca in props and out.get(_ca) == n:
                out.pop(_ca, None)
    return out if out != args else args
