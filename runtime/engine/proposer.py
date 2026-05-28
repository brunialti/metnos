"""engine/proposer.py — Protocol + SimpleProposer (default).

Il Proposer produce un Framework JSON dalla query+intent+pool tool. È
l'unico componente del Layer 3 che dipende dal LLM (a parte filler resolve
nell'Executor). Implementazione default: 1-shot Gemma wise tier con GBNF
strict.

Implementazioni alternative (file separati):
  - proposer_metis.py    → multi-strategia 2-3 alternative ranked telos (β)
  - proposer_frontier.py → Sonnet 4 API single call

Selettore via METNOS_ENGINE env. Swap zero-rewrite del resto del sistema.

§7.9: deterministic dispatcher (engine selector), LLM solo dentro
SimpleProposer.propose().
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional, Callable, Protocol

from .types import Intent, Framework

log = logging.getLogger(__name__)


# ── Protocol ──────────────────────────────────────────────────────────────

class Proposer(Protocol):
    """Interface per qualunque proposer engine.

    Contratto:
      - propose() ritorna Framework valido o None se fallisce a generare.
      - Mai solleva eccezioni — return None su qualsiasi errore interno.
      - Deve rispettare excluded_hashes (set di framework_hash da NON
        riproporre, vedi recovery).
      - `catalog` opzionale: lista Executor per render tool schemas inline.
    """
    def propose(self, *, query: str, intent: Intent,
                pool: list[str], excluded_hashes: set[str],
                llm_call: Optional[Callable] = None,
                lang: str = "it",
                catalog: Optional[list] = None) -> Optional[Framework]: ...


# ── SimpleProposer (default) ──────────────────────────────────────────────

_FRAMEWORK_RE = re.compile(r"\{[\s\S]*\}")


def _render_tool_pool(pool: list[str], catalog: Optional[list]) -> str:
    """Costruisce blocco tools con schema per il prompt.

    Per ogni tool: nome + descrizione 1-frase + required args + requires_one_of.
    Fallback a solo nome se catalog mancante.
    """
    if not catalog:
        return "\n".join(f"- {n}" for n in pool)
    cat_by_name = {getattr(e, "name", None): e for e in catalog}
    lines = []
    for name in pool:
        e = cat_by_name.get(name)
        if e is None:
            lines.append(f"- {name}")
            continue
        desc = (getattr(e, "description", "") or "").strip()
        # Prendi solo prima frase, max ~120 char
        desc_short = desc.split(".")[0][:120] if desc else ""
        schema = getattr(e, "args_schema", None) or {}
        required = schema.get("required") or []
        roo = schema.get("requires_one_of") or []
        props = list((schema.get("properties") or {}).keys())[:8]
        bits = [f"- {name}"]
        if desc_short:
            bits.append(f" — {desc_short}")
        if required:
            bits.append(f" [required: {','.join(required)}]")
        if roo:
            bits.append(f" [requires_one_of: {roo}]")
        if props:
            bits.append(f" args=[{','.join(props)}]")
        lines.append("".join(bits))
    return "\n".join(lines)


def _parse_framework_json(raw: str) -> Optional[dict]:
    """Estrai primo blocco JSON {...} da raw output LLM. Tollerante a
    prefissi/suffissi (es. <think>...</think>, prosa attorno)."""
    if not raw:
        return None
    # Strip think blocks
    raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE)
    m = _FRAMEWORK_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class SimpleProposer:
    """Default: 1-shot Gemma wise + parse tollerante.

    Niente multi-strategia, niente telos ranking, niente preventive.
    Mētis-like minimal. Fallisce honest se LLM non genera framework JSON.
    """

    def __init__(self, *, prompt_loader: Optional[Callable] = None):
        """prompt_loader: callable (role, lang, **vars) -> str. Default usa
        runtime.prompt_loader.get."""
        if prompt_loader is None:
            try:
                from prompt_loader import get as _get
                prompt_loader = _get
            except Exception:
                prompt_loader = lambda role, lang, **kw: ""
        self._load_prompt = prompt_loader

    def propose(self, *, query: str, intent: Intent,
                pool: list[str], excluded_hashes: set[str],
                llm_call: Optional[Callable] = None,
                lang: str = "it",
                catalog: Optional[list] = None) -> Optional[Framework]:
        if not query or llm_call is None:
            return None
        # Tier downgrade per intent high-confidence.
        # Bench 28/5/2026 (15q + 446q FROZEN): think=True NON aumenta ok%
        # rispetto a think=False. Soglia abbassata 0.85→0.70 per coprire
        # piu' query con la fast path (3-5s vs 25-30s).
        # Override via env METNOS_PROPOSER_FAST_CONFIDENCE.
        import os
        threshold = float(os.environ.get(
            "METNOS_PROPOSER_FAST_CONFIDENCE", "0.70"))
        use_fast = intent.confidence >= threshold

        # §7.3 GBNF grammar opt-in (bench 28/5 → 100% parse rate, +1s vs
        # baseline). Quando attivo: think=False forzato (ADR 0133: grammar+
        # think collide). Setup via env METNOS_PROPOSER_GRAMMAR=1.
        use_grammar = os.environ.get("METNOS_PROPOSER_GRAMMAR", "0") == "1"
        if use_grammar:
            use_fast = True  # force think=False

        # §7.3 Task #40 — Verb-aware pool filter (env METNOS_PROPOSER_VERB_FILTER=1)
        # Restringe pool ai tool che matchano intent.verb + universal helpers.
        # Pool 79 → 6-19 (90% reduction) → grammar GBNF molto più stretta +
        # LLM non puo' sbagliare verb family. Bench 446q baseline 47% top-1
        # prefilter → atteso 75%+ con verb constraint.
        effective_pool = pool
        if os.environ.get("METNOS_PROPOSER_VERB_FILTER", "0") == "1" and intent.verb:
            try:
                from tool_grammar import filter_pool_by_intent_verb
                pool_objs = [next((e for e in catalog if e.name == n), None) for n in pool] \
                            if catalog else []
                pool_objs = [p for p in pool_objs if p is not None]
                if pool_objs:
                    kept, excluded = filter_pool_by_intent_verb(pool_objs, intent.verb)
                    if kept:
                        effective_pool = [e.name for e in kept]
                        log.info("verb-aware filter: pool %d → %d (verb=%s)",
                                  len(pool), len(effective_pool), intent.verb)
            except Exception as ex:
                log.warning("verb filter fallito: %r — fallback full pool", ex)

        # Render tool schemas inline (Mētis needs arg names + required)
        tools_inline = _render_tool_pool(effective_pool, catalog)
        try:
            system = self._load_prompt(
                "engine_proposer", lang,
                verb=intent.verb, obj=intent.object,
                keywords=", ".join(intent.keywords),
                tools=tools_inline,
                excluded=", ".join(excluded_hashes) or "(nessuno)",
            )
        except Exception as ex:
            log.warning("SimpleProposer prompt load failed: %r", ex)
            return None
        if not system:
            return None
        user = query
        # Costruisci kwargs LLM con opzionale grammar
        llm_kwargs: dict = {
            "max_tokens": 1024 if use_fast else 2048,
            "think": not use_fast,
        }
        if use_grammar:
            try:
                # Lazy import GRAMMAR_FRAMEWORK da praxis_propose (riuso §7.3)
                import sys as _sys
                from pathlib import Path as _P
                _legacy = _P("/opt/metnos/runtime/_legacy")
                if str(_legacy) not in _sys.path:
                    _sys.path.insert(0, str(_legacy))
                from praxis_propose import GRAMMAR_FRAMEWORK
                llm_kwargs["grammar"] = GRAMMAR_FRAMEWORK
            except Exception as ex:
                log.warning("GBNF grammar load fallita: %r — fallback no-grammar", ex)
        try:
            raw = llm_call(system, user, **llm_kwargs)
        except TypeError:
            # llm_call non supporta grammar/think kwargs → fallback
            llm_kwargs.pop("grammar", None)
            try:
                raw = llm_call(system, user, **llm_kwargs)
            except Exception as ex:
                log.warning("SimpleProposer LLM (fallback) call failed: %r", ex)
                return None
        except Exception as ex:
            log.warning("SimpleProposer LLM call failed: %r", ex)
            return None
        parsed = _parse_framework_json(raw or "")
        if not parsed:
            log.info("SimpleProposer parse fail. Raw head: %r", (raw or "")[:200])
            return None
        return Framework.from_dict(parsed)


# ── Factory (selettore engine) ────────────────────────────────────────────

def get_proposer() -> Proposer:
    """Ritorna istanza Proposer selezionata via METNOS_ENGINE.

    Caricamento lazy: i moduli proposer_metis / proposer_frontier sono
    importati solo se richiesti, così l'assenza del file non blocca il
    sistema (fallback su SimpleProposer).
    """
    from . import get_engine_name
    name = get_engine_name()
    if name == "metis":
        try:
            from . import proposer_metis
            return proposer_metis.MetisProposer()
        except Exception as ex:
            log.warning("MetisProposer unavailable (%r), fallback simple", ex)
    elif name == "frontier":
        try:
            from . import proposer_frontier
            return proposer_frontier.FrontierProposer()
        except Exception as ex:
            log.warning("FrontierProposer unavailable (%r), fallback simple", ex)
    return SimpleProposer()
