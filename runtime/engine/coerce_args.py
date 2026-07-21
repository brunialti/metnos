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
runtime anche dove marcati) e le chiavi `_*` (iniettate dal runtime).

Proprietà (invariante PROV.3 per costruzione): lo stage scrive solo
provenienza `runtime` (marcati, drop) e `clause` (enum, normalize/drop) —
MAI un arg `semantic` in-schema, che resta dell'LLM. Idempotente. Tool senza
schema tipizzato (props assenti/vuote) → no-op, mai bloccare.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("engine.coerce_args")

# Piping/universali (§4.1): il runtime li possiede e li risolve; restano
# anche dove il manifest li marca runtime_resolved (es. write_files_spreadsheet
# .entries). Allineata a manifest_lint._UNIVERSAL_ARGS.
_UNIVERSAL_KEYS = frozenset({"from_step", "entries"})


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
    backstop NON li tocca MAI: sono dominio dei guard, e toccarli romperebbe
    l'idempotenza della catena (coerce droppa → guard riscrive → oscillazione
    alla ri-applicazione). Il costo onesto: un leak LLM su un arg guard-owned
    passa il coerce — lo arbitra il proprietario a valle (guard/resolver)."""
    props = _typed_props(schema)
    if props is None or not isinstance(args, dict) or not args:
        return args, False
    out = {}
    changed = False
    for key, val in args.items():
        if key in _UNIVERSAL_KEYS or key.startswith("_") or key in guard_owned:
            out[key] = val
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
                out[key] = val
                continue
            folded = [v for v in enum if v.lower() == val.lower()]
            if len(folded) == 1:              # case-insensitive unico → canonico
                out[key] = folded[0]
                changed = True
                continue
            changed = True                    # fuori dominio → drop
            continue
        out[key] = val
    return out, changed


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
