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


def _is_install_root_path(value) -> bool:
    """True se `value` è un path DENTRO l'install root di Metnos (PATH_ROOT,
    es. `/opt/metnos/executors/read_files`). Un path del genere non è MAI uno
    scope utente: entra dai pattern-by-example del proposer, e se ricordato
    come default si AUTO-RINFORZA (il listing dell'install dir riesce → viene
    ri-ricordato — turn 2cd8862a: 46 usi di `executors/read_files` come
    base_path). Filtro §7.3 su CATTURA e INIEZIONE."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        from pathlib import Path
        import config as _C
        v = Path(value.strip())
        if not v.is_absolute():
            return False
        root = Path(_C.PATH_ROOT).resolve()
        return v.resolve().is_relative_to(root)
    except Exception:  # noqa: BLE001 — best-effort, mai bloccare
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
        _remembered = args_defaults.get_default(actor, domain, arg)
        if _remembered and _is_install_root_path(_remembered):
            _remembered = None  # default AVVELENATO (install root) → mai iniettare
        val = (inline.get(arg)
               or _remembered
               or _config_default(domain, arg))
        if val:
            out[arg] = val
    return out


# §11 i18n: arg-scope → chiave messaggio (testo risolto via _msg per current_lang).
_PROMPT_KEYS = {
    "repo": "MSG_SCOPE_PROMPT_REPO",
    "calendar": "MSG_SCOPE_PROMPT_CALENDAR",
    "account": "MSG_SCOPE_PROMPT_ACCOUNT",
    "base_path": "MSG_SCOPE_PROMPT_BASE_PATH",
    "board": "MSG_SCOPE_PROMPT_BOARD",
    "project": "MSG_SCOPE_PROMPT_PROJECT",
    "workspace": "MSG_SCOPE_PROMPT_WORKSPACE",
}


def _scope_prompt(arg: str) -> str:
    """Prompt user-facing per uno scope-arg, risolto i18n (§11)."""
    from messages import get as _msg
    key = _PROMPT_KEYS.get(arg)
    if key:
        return _msg(key)
    return _msg("MSG_SCOPE_PROMPT_GENERIC", arg=arg)


def _verb_of(tool: str) -> Optional[str]:
    try:
        from naming_grammar import parse_name
        nc = parse_name(tool)
        return getattr(nc, "verb", None) if nc else None
    except Exception:
        return None


def scope_form_request(executor_name: str, args: dict, schema: dict | None,
                       query: str) -> Optional[dict]:
    """Da chiamare DOPO resolve_scope_args. Ritorna un'osservazione
    `needs_inputs` (form get_inputs) quando serve chiedere uno scope-arg,
    altrimenti None. Ibrido (decisione Roberto):
      - LETTURA (find/read/list/get): chiede SOLO se un required è ancora
        mancante dopo la risoluzione.
      - SCRITTURA (create/write/delete/set/…): chiede SEMPRE conferma del
        target required (pre-compilato col valore risolto) — §2.8: non scrivere
        su un oggetto risolto-in-silenzio senza conferma esplicita.
    Il resume (resume_executor_with_values) + cattura riusano i meccanismi
    esistenti. Determinismo §7.9."""
    if not isinstance(args, dict):
        return None
    if not domain_for(executor_name):
        return None
    try:
        from vocab import DESTRUCTIVE_VERBS
    except Exception:
        DESTRUCTIVE_VERBS = frozenset()
    is_write = (_verb_of(executor_name) or "") in DESTRUCTIVE_VERBS
    required = set((schema or {}).get("required") or [])
    fields: list[tuple[str, str]] = []
    for arg in _scope_args_for(args, schema or {}):
        if arg not in required:
            continue
        resolved = not _is_placeholder(arg, args.get(arg))
        if is_write:
            fields.append((arg, str(args.get(arg)) if resolved else ""))
        elif not resolved:
            fields.append((arg, ""))
    if not fields:
        return None
    from messages import get as _msg  # §11 i18n
    dialog = [{"var": a, "prompt": _scope_prompt(a),
               "schema": {"kind": "text"}, "optional": False, "default": d}
              for a, d in fields]
    return {
        "decision": "needs_inputs",
        "needs_inputs": {
            "title": _msg("MSG_SCOPE_TITLE_WRITE") if is_write
                     else _msg("MSG_SCOPE_TITLE_READ"),
            "dialog": dialog,
            "fmt": "auto",
            "on_complete": {
                "type": "resume_executor_with_values",
                "executor": executor_name,
                "args_base": {k: v for k, v in args.items()},
            },
            "timeout_s": 3600,
        },
    }


def remember_scope_args(executor_name: str, args: dict, *, actor: str) -> None:
    """Dopo un invoke OK: memorizza il valore degli scope-arg usati come default
    per il giro dopo (anche se introdotti inline o esplicitamente)."""
    if not isinstance(args, dict) or not actor:
        return
    domain = domain_for(executor_name)
    if not domain:
        return
    for arg, val in args.items():
        if not (is_scope_arg(arg) and not _is_placeholder(arg, val)):
            continue
        if _is_install_root_path(val):
            continue  # mai ricordare un path dell'install root come scope utente
        args_defaults.set_default(actor, domain, arg, str(val))
