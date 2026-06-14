# SPDX-License-Identifier: AGPL-3.0-only
"""args_resolver — risoluzione deterministica degli arg di SCOPE mancanti o a
PLACEHOLDER, prima di validate_args. Riusabile per QUALSIASI executor: opera su
schema (args.required) + il vocab SCOPE_ARGS, non su nomi specifici.

Precedenza (§7.9, zero LLM):
  1. arg esplicito VALIDO (non placeholder)         → tieni
  2. inline dalla query (args_extractor, riusato)   → usa + (poi) ricorda
  3. valore RICORDATO (args_defaults, per dominio)  → usa
  4. default di CONFIG (cred per-dominio, es. github.repo) → usa

Se nemmeno questi bastano e l'arg è required, validate_args fallisce → il runtime
dialog chiede via form (e ricorda la risposta). La CATTURA (memorizzazione del
valore usato) avviene dopo un invoke OK via `remember_scope_args`.

Semplice, robusto, efficace: una sola funzione di normalizzazione + una di
cattura; tutta la conoscenza di dominio è nei vocab (SCOPE_ARGS, provider markers).
"""
from __future__ import annotations

from typing import Optional

import args_defaults
from args_defaults import domain_for, is_scope_arg
from args_extractor import _PLACEHOLDER_OWNERS, regex_extract


def _is_placeholder(arg_name: str, value) -> bool:
    """True se il valore è assente/vuoto o un placeholder noto (es. l'LLM copia
    'owner/name' dall'esempio del manifest)."""
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    if "/" in s and arg_name in ("repo", "repository"):
        if s.split("/", 1)[0].strip().lower() in _PLACEHOLDER_OWNERS:
            return True
    return False


def _config_default(domain: str, arg_name: str) -> Optional[str]:
    """Default da config: la cred del dominio (keyed per dominio, es. 'github')
    con l'arg come chiave (es. repo). Universale, non github-specifico."""
    try:
        import credentials
        payload = credentials.load(domain)
        if isinstance(payload, dict):
            v = payload.get(arg_name) or payload.get("default_" + arg_name)
            if isinstance(v, str) and v.strip():
                return v.strip()
    except Exception:
        pass
    return None


def _scope_args_for(args: dict, schema: dict) -> set:
    """Arg di scope rilevanti: i required dello schema + quelli già presenti,
    intersecati con SCOPE_ARGS."""
    out = {a for a in (args or {}) if is_scope_arg(a)}
    req = (schema or {}).get("required") or []
    out |= {a for a in req if is_scope_arg(a)}
    return out


def resolve_scope_args(executor_name: str, args: dict, schema: dict | None,
                       *, actor: str, query: str) -> dict:
    """Ritorna una COPIA di `args` con gli scope-arg mancanti/placeholder
    risolti per precedenza. Idempotente; non solleva. Non chiede (form a valle)."""
    if not isinstance(args, dict):
        return args
    domain = domain_for(executor_name)
    if not domain:
        return args
    candidates = _scope_args_for(args, schema or {})
    if not candidates:
        return args
    out = dict(args)
    inline: Optional[dict] = None
    for arg in candidates:
        if not _is_placeholder(arg, out.get(arg)):
            continue
        if inline is None:
            inline = regex_extract(query or "", schema or {})
        val = (inline.get(arg)
               or args_defaults.get_default(actor, domain, arg)
               or _config_default(domain, arg))
        if val:
            out[arg] = val
    return out


def remember_scope_args(executor_name: str, args: dict, *, actor: str) -> None:
    """Dopo un invoke OK: memorizza il valore degli scope-arg usati come default
    per il giro dopo (anche se introdotti inline o esplicitamente)."""
    if not isinstance(args, dict) or not actor:
        return
    domain = domain_for(executor_name)
    if not domain:
        return
    for arg, val in args.items():
        if is_scope_arg(arg) and not _is_placeholder(arg, val):
            args_defaults.set_default(actor, domain, arg, str(val))
