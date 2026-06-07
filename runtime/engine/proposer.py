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
from typing import Optional, Callable, Protocol, Sequence

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
                catalog: Optional[list] = None,
                exclude_tools: Sequence[str] = ()) -> Optional[Framework]: ...


# ── SimpleProposer (default) ──────────────────────────────────────────────

_FRAMEWORK_RE = re.compile(r"\{[\s\S]*\}")

# Budget di prompt PER-TOOL + logica di troncamento: SoT in `manifest_rules`
# (il "DNA"), così synt/lint/proposer condividono gli stessi numeri e la stessa
# regola di taglio. Description verbose distraggono il modello medio (§2.5:
# description = sola testa). `manifest_lint` e synt importano dalla stessa SoT.
try:
    from manifest_rules import RENDER_BUDGET as TOOL_DESC_BUDGET, render_head as _render_head
except Exception:  # pragma: no cover — CLI senza runtime sul path
    TOOL_DESC_BUDGET = 260

    def _render_head(desc):
        desc = (desc or "").strip().replace("\n", " ")
        if "PATTERN:" in desc:
            c = desc.find("OUT:")
            return (desc[:c] if c > 0 else desc)[:TOOL_DESC_BUDGET].strip()
        return desc.split(".")[0][:180].strip()


def _render_tool_pool(pool: list[str], catalog: Optional[list]) -> str:
    """Costruisce blocco tools con schema per il prompt.

    Per ogni tool: nome + descrizione 1-frase + required args + requires_one_of.
    Fallback a solo nome se catalog mancante.
    """
    if not catalog:
        return "\n".join(f"- {n}" for n in pool)
    from date_tokens import substitute_date_tokens  # §7.11: anni-esempio freschi
    cat_by_name = {getattr(e, "name", None): e for e in catalog}
    lines = []
    for name in pool:
        e = cat_by_name.get(name)
        if e is None:
            lines.append(f"- {name}")
            continue
        # Troncamento via SoT manifest_rules.render_head (DNA): testa §2.5 fino a
        # OUT: (cap RENDER_BUDGET) per i capitoli; prima frase ROBUSTA (cap
        # RENDER_LEGACY_MAX, non spezza a ".html") per i legacy in attesa di bonifica.
        desc_short = _render_head(substitute_date_tokens(getattr(e, "description", "") or ""))
        schema = getattr(e, "args_schema", None) or {}
        required = schema.get("required") or []
        roo = schema.get("requires_one_of") or []
        props_map = schema.get("properties") or {}
        # Arg di CONFIGURAZIONE (non intento) marcati `runtime_resolved`: NON
        # esposti all'LLM. Lesson A3/B1 (lessons_learned.md): l'enum di un arg
        # come `client`/`account`/`provider` induce un BIAS (il pattern vince
        # sul colloquiale "OMETTI") → il backend lo risolve il RUNTIME, non il
        # proposer. L'arg resta nello schema per validazione/umani/iniezione.
        props = [p for p in props_map.keys()
                 if not (isinstance(props_map.get(p), dict)
                         and props_map[p].get("runtime_resolved"))][:8]
        bits = [f"- {name}"]
        if desc_short:
            bits.append(f" — {desc_short}")
        if required:
            bits.append(f" [required: {','.join(required)}]")
        if roo:
            bits.append(f" [requires_one_of: {roo}]")
        if props:
            bits.append(f" args=[{','.join(props)}]")
        # §8.3 anti-invenzione: esponi gli enum degli arg così il Proposer
        # sceglie un valore valido invece di inventarlo (universal §7.3 — vale
        # per qualunque tool con enum: style, via_channel, ecc.).
        enum_bits = []
        for pname in props:
            decl = props_map.get(pname) or {}
            enum_vals = decl.get("enum")
            if enum_vals:
                vals = ",".join(str(v) for v in enum_vals[:8])
                enum_bits.append(f"{pname}∈{{{vals}}}")
        if enum_bits:
            bits.append(f" enums=[{'; '.join(enum_bits)}]")
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
                catalog: Optional[list] = None,
                exclude_tools: Sequence[str] = ()) -> Optional[Framework]:
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
        # Compound-aware (§7.3): per query multi-azione (>=2 verbi canonici) il
        # filtro mono-verbo escluderebbe i tool degli altri sotto-intenti
        # (find+write+send) — il pool e' gia' multi-verbo da dispatch. Skip il
        # filtro sui compound, cosi' la pipeline completa resta proponibile
        # (bug 2/6/2026: "trova le issue, salvale, mandami il riassunto" perdeva
        # write_files/send_messages col verb-filter).
        # Compound signal PRIMARIO = decomposizione intent LLM (multilingue, no
        # dizionari di sinonimi): >=2 clausole {verb,object} → compound. Il
        # detector lessicale resta SOLO come fallback se l'LLM non ha decomposto
        # (es. "Prendi le issue ... mettile in un foglio" — "prendi"/"mettile"
        # non sono nel dizionario lessicale → mono-verbo falso → verb-filter
        # stripava create_files_spreadsheet, bug q21 4/6).
        _is_compound = len(getattr(intent, "actions", None) or []) >= 2
        if not _is_compound:
            try:
                from prefilter import (tokenize as _vf_tok,
                                        detect_canonical_verbs_all as _vf_dv)
                _is_compound = len(set(_vf_dv(_vf_tok(query)))) >= 2
            except Exception:
                _is_compound = False
        if (os.environ.get("METNOS_PROPOSER_VERB_FILTER", "0") == "1"
                and intent.verb and not _is_compound):
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

        # Esclusione esplicita (es. guard get_inputs misroute in dispatch):
        # applicata DOPO la costruzione del pool, così sopravvive alla
        # re-iniezione degli universal helpers in filter_pool_by_intent_verb.
        # Toglie i nomi sia dal prompt sia dalla grammar GBNF (entrambi usano
        # effective_pool). Universale, deterministico §7.9.
        if exclude_tools:
            _excl = set(exclude_tools)
            effective_pool = [n for n in effective_pool if n not in _excl]

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
                from .grammar_framework import build_framework_grammar
                # Vincola `tool` ai nomi del pool effettivo: l'LLM non puo'
                # piu' allucinare nomi inesistenti (es. get_issues) ne' uscire
                # dal pool. Bug 2/6/2026: grammar vincolava solo la FORMA JSON,
                # non i nomi tool → find_urls/get_issues invece di
                # find_issues_github (in pool).
                llm_kwargs["grammar"] = build_framework_grammar(effective_pool)
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
