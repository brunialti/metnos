# SPDX-License-Identifier: AGPL-3.0-only
"""coerce_args.py — FASE 3.1 provenienza args (spec
`internal/design/spec_args_provenance_architecture.md`, 6-7/7/2026).

Backstop deterministico UNICO sul confine LLM→pipeline: conforma gli args di
ogni step allo SCHEMA del tool proposto. Gira come PRIMO guard (prima che i
guard legittimi scrivano), quindi tocca SOLO l'output grezzo del proposer:

  1. chiave FUORI-SCHEMA (non dichiarata nelle properties) → DROP: per il
     contratto §2.5 il manifest è la verità; un arg inventato non esiste.
  2. chiave marcata `runtime_resolved` → DROP (leak): il proprietario è il
     runtime/i guard (backend_resolver, scope_sink, align_provider — che
     girano DOPO e quindi non vengono mai pestati da questo stage).
  3. enum: valore fuori dominio → match case-insensitive UNICO → normalizza
     al valore canonico (§2.4); altrimenti DROP della chiave (a valle
     `fill_clause_args` la ri-deriva dal chunk, o vince il default onesto
     dell'executor). MAI snap "al più vicino": indovinare è peggio del default.

Whitelist SEMPRE conservata: `from_step`/`entries` (piping §4.1, dominio del
runtime anche dove marcati). Le chiavi `_*` sono invece rimosse a questo
confine: l'input qui appartiene al proposer, mentre i metadati interni vengono
iniettati soltanto dopo dai resolver/runtime autenticati.

Proprietà (invariante PROV.3 per costruzione): lo stage scrive solo
provenienza `runtime` (marcati, drop) e `clause` (enum, normalize/drop) —
MAI un arg `semantic` in-schema, che resta dell'LLM. Idempotente. Tool senza
schema tipizzato (props assenti/vuote) → no-op, mai bloccare.
"""
from __future__ import annotations

import logging
from typing import Optional

from manifest_rules import UNIVERSAL_ARGS

log = logging.getLogger("engine.coerce_args")

def _typed_props(schema) -> Optional[dict]:
    """Properties dict utilizzabile, o None (= no-op, mai bloccare)."""
    if not isinstance(schema, dict):
        return None
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    return props


def coerce_step_args(args: dict, schema,
                     guard_owned: frozenset = frozenset()) -> tuple[dict, bool]:
    """Applica le 3 regole a UN dict di args. Ritorna (coerced, changed).
    Non muta l'input.

    `guard_owned`: nomi-arg che un guard A VALLE dichiara nei suoi `writes`
    (dal registro PROV.1, es. `client` di align_provider/scope_sink). Il
    backstop non li arbitra: sono dominio dei guard, e toccarli romperebbe
    l'idempotenza della catena (coerce droppa → guard riscrive → oscillazione
    alla ri-applicazione). Il costo onesto: un leak LLM su un arg guard-owned
    passa il coerce — lo arbitra il proprietario a valle (guard/resolver).

    L'esenzione e' una tolleranza DENTRO la pipeline, non un lasciapassare fino
    all'executor (6/8). Serve perche' un arg fuori-schema puo' essere la PROVA
    che una guardia a valle legge — `include_health` su `get_files` non
    appartiene al contratto di get_files ed e' esattamente per questo che
    dimostra un errore di instradamento. Toglierla qui accieca la guardia
    (misurato: rompe il ripristino del produttore health).

    Il cricchetto — un nome esente su OGNI tool per sempre, appena una guardia
    lo dichiara — si chiude all'USCITA: `strip_unknown_args`, ultima della
    pipeline, conforma allo schema quello che davvero parte per l'executor.
    Ingresso tollerante, uscita stretta."""
    if not isinstance(args, dict) or not args:
        return args, False
    # Confine di autorita': nessun metadato interno puo' provenire dal modello,
    # da un piano cachato o da un replay.  I valori legittimi (`_actor`,
    # `_confirmed`, `_pre_approved`, ...) sono iniettati piu' tardi da runtime,
    # engine o callback di resume e non attraversano questo guard.
    out = {key: val for key, val in args.items()
           if not (isinstance(key, str) and key.startswith("_"))}
    changed = len(out) != len(args)
    props = _typed_props(schema)
    if props is None:
        return out, changed
    coerced = {}
    for key, val in out.items():
        if key in UNIVERSAL_ARGS or key in guard_owned:
            coerced[key] = val
            continue
        decl = props.get(key)
        if decl is None:                      # 1. fuori-schema → drop
            changed = True
            continue
        if isinstance(decl, dict) and decl.get("runtime_resolved"):
            changed = True                    # 2. leak runtime → drop
            continue
        enum = decl.get("enum") if isinstance(decl, dict) else None
        if (isinstance(enum, list) and enum
                and all(isinstance(v, str) for v in enum)
                and isinstance(val, str)):
            if val in enum:                   # 3. enum: valido → intatto
                coerced[key] = val
                continue
            folded = [v for v in enum if v.lower() == val.lower()]
            if len(folded) == 1:              # case-insensitive unico → canonico
                coerced[key] = folded[0]
                changed = True
                continue
            changed = True                    # fuori dominio → drop
            continue
        coerced[key] = val
    return coerced, changed


def strip_unknown_args(framework, catalog):
    """USCITA della pipeline: toglie da ogni step gli arg che il suo tool non
    dichiara. Applica SOLO la regola fuori-schema — non tocca `runtime_resolved`
    ne' gli enum, che appartengono all'ingresso e alle guardie.

    Perche' esiste (6/8): l'esenzione `guard_owned` all'ingresso e' per nome
    nudo, quindi cresce da sola — ogni guardia nuova che dichiara `args.X`
    rende `X` accettabile su OGNI tool, per sempre. Erano 21 nomi, fra cui
    `path`, `paths`, `mode`, `exist_ok`, `dst_folder`: dove scrivere, che cosa
    cancellare, se sovrascrivere. Non si puo' chiudere all'ingresso senza
    accecare le guardie che leggono quegli arg come prova; si chiude qui, dove
    nessuno deve piu' leggerli. Cosi' l'esenzione resta una tolleranza interna
    e il piano che parte per l'executor e' conforme ai manifest.

    Chiavi sempre salve: `_*` (metadati iniettati dal runtime) e il piping
    universale (`from_step`, `entries`). Tool senza schema tipizzato: no-op."""
    by_name = {}
    for e in catalog or []:
        n = getattr(e, "name", None)
        if isinstance(n, str):
            by_name[n] = getattr(e, "args_schema", None)
    for s in getattr(framework, "steps", []) or []:
        args = getattr(s, "args", None)
        if not isinstance(args, dict) or not args:
            continue
        props = _typed_props(by_name.get(getattr(s, "tool", None)))
        if props is None:
            continue
        kept = {k: v for k, v in args.items()
                if (not isinstance(k, str)) or k.startswith("_")
                or k in UNIVERSAL_ARGS or k in props}
        if len(kept) != len(args):
            log.info("[strip_unknown_args] %s: fuori schema=%s",
                     s.tool, sorted(set(args) - set(kept)))
            s.args = kept
    return framework


def coerce_framework_to_schema(framework, catalog,
                               guard_owned_args: frozenset = frozenset()):
    """Guard fn (fw, catalog) → fw: conforma gli args di ogni step allo schema
    del SUO tool. Step con tool ignoto (final_answer, hallucination gestita a
    valle dal validator) → intatto. `guard_owned_args`: vedi coerce_step_args."""
    try:
        by_name = {}
        for e in catalog or []:
            n = getattr(e, "name", None)
            if isinstance(n, str):
                by_name[n] = getattr(e, "args_schema", None)
        for s in getattr(framework, "steps", []) or []:
            schema = by_name.get(getattr(s, "tool", None))
            if schema is None:
                continue
            coerced, changed = coerce_step_args(s.args, schema,
                                                guard_owned=guard_owned_args)
            if changed:
                dropped = sorted(set(s.args) - set(coerced))
                normal = sorted(k for k in coerced
                                if k in s.args and coerced[k] != s.args[k])
                log.info("[coerce_args] %s: drop=%s normalize=%s",
                         s.tool, dropped or "-", normal or "-")
                s.args = coerced
        return framework
    except Exception:  # noqa: BLE001 — backstop best-effort, mai bloccare
        return framework
