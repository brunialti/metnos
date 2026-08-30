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

import detection_lexicon_seed_parsers as _parser_lex

# Verbi-testa per cui l'iniezione della finestra e' sicura: produttori
# read-only. MAI mutating (delete/move/write/...): la finestra cambierebbe
# il perimetro di un'azione irreversibile.
_SAFE_VERB_HEADS = frozenset({"read", "find", "get", "list"})

def _phrase_alt(forms) -> str:
    return "|".join(
        re.escape(str(form)).replace(r"\ ", r"\s+")
        for form in sorted(set(forms or ()), key=lambda item: (-len(item), item))
        if str(form).strip()
    )


def _surface_to_canonical(mapping: dict) -> dict[str, str]:
    return {
        str(surface).casefold(): str(canonical)
        for canonical, forms in mapping.items()
        for surface in forms
    }

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
_MONTHS_IMAP = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _parse_absolute_year(query: str) -> int | None:
    """Anno di calendario assoluto esplicito nella query, o None. Vince solo
    se non c'e' GIA' un match rolling (vedi `resolve_time_window`): «ultimi
    2 anni» resta rolling, «del 2026» e' un anno di calendario."""
    if not query:
        return None
    lexicon = _parser_lex.load_family("time_resolver")
    if lexicon is None:
        return None
    candidates: list[tuple[int, int]] = []
    prefix = _phrase_alt(lexicon["parser.time.absolute_year_prefix"])
    suffix = _phrase_alt(lexicon["parser.time.absolute_year_suffix"])
    if prefix:
        rx = re.compile(
            rf"(?<!\w)(?:{prefix})(?!\w)\s+(20\d{{2}})(?!\d)",
            re.IGNORECASE | re.UNICODE,
        )
        candidates.extend((m.start(), int(m.group(1)))
                          for m in rx.finditer(query))
    if suffix:
        rx = re.compile(
            rf"(?<!\d)(20\d{{2}})\s+(?:{suffix})(?!\w)",
            re.IGNORECASE | re.UNICODE,
        )
        candidates.extend((m.start(), int(m.group(1)))
                          for m in rx.finditer(query))
    candidates = [(pos, year) for pos, year in candidates
                  if 2000 <= year <= 2099]
    return min(candidates)[1] if candidates else None


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
    lexicon = _parser_lex.load_family("time_resolver")
    if lexicon is None:
        return None
    candidates: list[tuple[int, int, str]] = []
    determiners = _phrase_alt(lexicon["parser.time.past_determiner"])
    units = lexicon["parser.time.unit"]
    reverse_units = _surface_to_canonical(units)
    unit_alt = _phrase_alt(reverse_units)
    if determiners and unit_alt:
        explicit = re.compile(
            rf"(?<!\w)(?:{determiners})(?!\w)\s+(\d{{1,4}})\s*"
            rf"(?P<unit>{unit_alt})(?!\w)",
            re.IGNORECASE | re.UNICODE,
        )
        for match in explicit.finditer(query):
            n = int(match.group(1))
            unit = reverse_units.get(match.group("unit").casefold())
            if 1 <= n <= 9999 and unit:
                candidates.append((0, match.start(), f"last-{n}{unit}"))
        # The established bare singular contract excludes weeks; the numeric
        # form above remains available for all registered units.
        singular_surfaces = _surface_to_canonical(
            lexicon["parser.time.singular_unit"])
        singular_alt = _phrase_alt(singular_surfaces)
        if singular_alt:
            singular = re.compile(
                rf"(?<!\w)(?:{determiners})(?!\w)\s+"
                rf"(?P<unit>{singular_alt})(?!\w)",
                re.IGNORECASE | re.UNICODE,
            )
            for match in singular.finditer(query):
                unit = singular_surfaces.get(match.group("unit").casefold())
                if unit:
                    candidates.append((0, match.start(), f"last-1{unit}"))

    for canonical in ("h", "d", "w", "m", "y"):
        forms = lexicon[f"parser.time.past_postfix.{canonical}"]
        unit_forms = units.get(canonical, ())
        unit_alt_for_kind = _phrase_alt(unit_forms)
        postfix_alt = _phrase_alt(forms)
        if not unit_alt_for_kind or not postfix_alt:
            continue
        rx = re.compile(
            rf"(?<!\w)(\d{{1,4}})\s*(?:{unit_alt_for_kind})(?!\w)\s+"
            rf"(?:{postfix_alt})(?!\w)",
            re.IGNORECASE | re.UNICODE,
        )
        for match in rx.finditer(query):
            n = int(match.group(1))
            if 1 <= n <= 9999:
                candidates.append((0, match.start(), f"last-{n}{canonical}"))
        # Historical singular postfix exists only for hour and day.
        if canonical in {"h", "d"}:
            singular_unit_alt = _phrase_alt(
                lexicon["parser.time.singular_unit"].get(canonical, ()))
            rx = re.compile(
                rf"(?<!\w)(?:{singular_unit_alt})(?!\w)\s+"
                rf"(?:{postfix_alt})(?!\w)",
                re.IGNORECASE | re.UNICODE,
            )
            candidates.extend((0, match.start(), f"last-1{canonical}")
                              for match in rx.finditer(query))

    relative = lexicon["parser.time.relative_day"]
    reverse_relative = _surface_to_canonical(relative)
    relative_alt = _phrase_alt(reverse_relative)
    if relative_alt:
        rx = re.compile(
            rf"(?<!\w)(?P<form>{relative_alt})(?!\w)",
            re.IGNORECASE | re.UNICODE,
        )
        for match in rx.finditer(query):
            canonical = reverse_relative.get(match.group("form").casefold())
            if canonical:
                candidates.append((1, match.start(), canonical))
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
