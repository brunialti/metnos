"""engine/proposer_metis.py — MetisProposer multi-strategy + telos rank (β).

Differenze vs SimpleProposer:
  1. Genera 2-3 framework ALTERNATIVI (LLM 1 chiamata con array, oppure
     N call single-shot quando GBNF grammar attivo).
  2. Rank candidati via euristica telos-aware (ADR 0157 weights, no LLM).
  3. Sceglie candidato con score migliore non in excluded_hashes.

Trade-off vs simple:
  + +3-5% coverage (alternative coperta)
  - 2-3× latency Proposer call (3× su grammar path)

§7.3: ranking deterministico, niente LLM dentro _rank_by_telos.

Selezione via METNOS_ENGINE=metis. Tuning via:
  METNOS_METIS_N_CANDIDATES=3
  METNOS_METIS_CACHE_MAX=200
  METNOS_PROPOSER_GRAMMAR=1 → single-shot grammar (perde multi-candidate)

NOTA 28/5/2026: file ricostruito da `.pyc` cache + 7 fix issue trovate
durante rebuild. Vedi git log per detail dei fix.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import OrderedDict
from typing import Optional, Callable

from .types import Intent, Framework
from .proposer import SimpleProposer, _parse_framework_json, _render_tool_pool

log = logging.getLogger(__name__)


def _n_candidates(intent=None) -> int:
    """N candidati ADATTIVO per confidenza (deterministico, no ML — §7.9).

    Il proposer metis genera N piani-candidato (N chiamate LLM grammar-constrained
    sulla GPU SINGOLA → costo dominante del turno) e ne sceglie uno via telos-rank
    euristico. Su query ad ALTA confidenza e NON-compound la prima proposta è
    affidabile → N=1 (niente spreco, ~3-5x più veloce). Su BASSA confidenza o
    COMPOUND (>=2 azioni) si mantiene l'hedge champion+challenger → N=ceiling.
    Gating sul segnale `intent.confidence` GIÀ esistente (stessa soglia di
    `use_fast`). Nessun ranker ML ([[no_training_amplify_reality]]). Env: ceiling
    `METNOS_METIS_N_CANDIDATES` (default 2), floor `_FAST` (default 1)."""
    try:
        ceil = max(1, int(os.environ.get("METNOS_METIS_N_CANDIDATES", "2")))
    except ValueError:
        ceil = 2
    try:
        fast = max(1, int(os.environ.get("METNOS_METIS_N_CANDIDATES_FAST", "1")))
    except ValueError:
        fast = 1
    if intent is None:
        return ceil
    try:
        conf = float(getattr(intent, "confidence", 0.0) or 0.0)
        thr = float(os.environ.get("METNOS_PROPOSER_FAST_CONFIDENCE", "0.70"))
        compound = len(getattr(intent, "actions", None) or []) >= 2
    except Exception:
        return ceil
    return fast if (conf >= thr and not compound) else ceil


def _cache_max() -> int:
    """Max entries cache, default 200. Fix #3 (no unbounded growth).
    Min 1 (no zero/negative) — clamp basso permette test cap=2,3,..."""
    try:
        return max(1, int(os.environ.get("METNOS_METIS_CACHE_MAX", "200")))
    except ValueError:
        return 200


# Costante backward-compat (legacy callers / tests). Preferire `_n_candidates()`.
N_CANDIDATES = _n_candidates()


class MetisProposer:
    """Multi-strategy proposer con cache LRU + telos rank."""

    def __init__(self, *, prompt_loader: Optional[Callable] = None):
        self._simple = SimpleProposer(prompt_loader=prompt_loader)
        if prompt_loader is None:
            try:
                from prompt_loader import get as _get
                prompt_loader = _get
            except Exception:
                prompt_loader = lambda role, lang, **kw: ""
        self._load_prompt = prompt_loader
        # Fix #3: LRU bounded cache (OrderedDict, move_to_end + popitem(last=False))
        self._candidate_cache: OrderedDict = OrderedDict()

    def _cache_key(self, query: str, intent: Intent, lang: str):
        """Cache key tuple. Fix #2: include lang. Fix #6: full sha256."""
        import hashlib
        # Fix #6: full sha256 hex (256-bit, no collision risk)
        h = hashlib.sha256(query.encode("utf-8")).hexdigest()
        # Fix #2: lang separa IT/EN
        return (h, intent.verb, intent.object, lang)

    def _cache_put(self, key, value):
        """LRU insert: move to end (most recent), evict oldest if over cap."""
        self._candidate_cache[key] = value
        self._candidate_cache.move_to_end(key)
        while len(self._candidate_cache) > _cache_max():
            self._candidate_cache.popitem(last=False)

    def _cache_get(self, key):
        """LRU access: move to end if present."""
        if key in self._candidate_cache:
            self._candidate_cache.move_to_end(key)
            return self._candidate_cache[key]
        return None

    def propose(self, *, query: str, intent: Intent,
                pool: list[str], excluded_hashes: set[str],
                llm_call: Optional[Callable] = None,
                lang: str = "it",
                catalog: Optional[list] = None,
                exclude_tools: tuple = ()) -> Optional[Framework]:
        if not query or llm_call is None:
            return None
        # exclude_tools (guard get_inputs-misroute in dispatch): rimuovi dal
        # pool PRIMA del render/grammar e propaga a SimpleProposer (i tool
        # esclusi possono rientrare come universal-helper → SimpleProposer li
        # toglie DOPO la re-iniezione). Senza questo param MetisProposer.propose
        # crashava con TypeError sulla chiamata-guard di dispatch (bug 4/6).
        _excl = set(exclude_tools or ())
        if _excl:
            pool = [n for n in pool if n not in _excl]
        cache_key = self._cache_key(query, intent, lang)

        # Retry path: serve alternativa cached senza LLM call. SKIP se
        # exclude_tools attivo: la cache NON è filtrata per tool esclusi →
        # ri-servirebbe il framework con get_inputs che il guard sta escludendo.
        if excluded_hashes and not _excl:
            cached_list = self._cache_get(cache_key)
            if cached_list:
                for cached in cached_list:
                    try:
                        from .executor import compute_framework_hash
                        h = compute_framework_hash(cached)
                    except Exception:
                        h = ""
                    if h and h not in excluded_hashes:
                        log.info(
                            "MetisProposer: retry serving cached alternative "
                            "(hash=%s) without LLM call", h[:8])
                        return cached

        tools_inline = _render_tool_pool(pool, catalog)
        candidates = self._generate_candidates(
            query=query, intent=intent, pool=pool, tools_inline=tools_inline,
            excluded_hashes=excluded_hashes, llm_call=llm_call, lang=lang,
            catalog=catalog, exclude_tools=tuple(_excl))

        if not candidates:
            # Fallback a SimpleProposer (preserva produzione anche su LLM fail).
            return self._simple.propose(
                query=query, intent=intent, pool=pool,
                excluded_hashes=excluded_hashes, llm_call=llm_call,
                lang=lang, catalog=catalog, exclude_tools=tuple(_excl))

        ranked = self._rank_by_telos(candidates, intent=intent, lang=lang)
        self._cache_put(cache_key, ranked)

        try:
            from .executor import compute_framework_hash
            for fw in ranked:
                h = compute_framework_hash(fw)
                if h not in excluded_hashes:
                    return fw
        except Exception:
            pass
        return ranked[0] if ranked else candidates[0]

    def _generate_candidates(self, *, query, intent, pool, tools_inline,
                              excluded_hashes, llm_call, lang, catalog,
                              exclude_tools=()):
        """Genera N candidati. Fix #1: gestisce grammar+metis path.

        Due modi:
          (a) Default: 1 LLM call, prompt richiede JSON array di N framework.
          (b) METNOS_PROPOSER_GRAMMAR=1: N LLM call single-shot ciascuna con
              GBNF grammar (single framework per call). Triplica latency
              ma garantisce 100% parse rate (ADR 0133).

        Senza fix #1: grammar+metis attivi nello stesso service = grammar
        ignorata dal metis path → parse rate degrada vs claim hardening.
        """
        use_grammar = os.environ.get("METNOS_PROPOSER_GRAMMAR", "0") == "1"
        n_cands = _n_candidates(intent)
        if use_grammar:
            return self._generate_grammar_multi(
                query=query, intent=intent, pool=pool,
                excluded_hashes=excluded_hashes, llm_call=llm_call,
                lang=lang, catalog=catalog, n=n_cands,
                exclude_tools=exclude_tools)
        # Default: 1 call, array output.
        try:
            system = self._load_prompt(
                "engine_proposer_metis", lang,
                verb=intent.verb,
                obj=intent.object,
                keywords=", ".join(intent.keywords),
                tools=tools_inline,
                excluded=", ".join(excluded_hashes) or "(nessuno)",
                n_candidates=n_cands,
            )
        except Exception:
            return []
        if not system:
            return []
        try:
            raw = llm_call(system, query, max_tokens=4096, think=True)
        except Exception as ex:
            log.warning("MetisProposer LLM call failed: %r", ex)
            return []
        return _parse_candidates(raw or "")

    def _generate_grammar_multi(self, *, query, intent, pool,
                                  excluded_hashes, llm_call, lang, catalog, n,
                                  exclude_tools=()):
        """Fix #1: N single-shot via SimpleProposer (riusa grammar+verb-filter
        path). Ogni call esclude i framework_hash già generati per forzare
        diversità. Costo: N× LLM call vs 1× del default path."""
        out: list[Framework] = []
        seen_hashes: set[str] = set(excluded_hashes or set())
        for i in range(n):
            try:
                fw = self._simple.propose(
                    query=query, intent=intent,
                    pool=pool,  # propaga pool reale (SimpleProposer rendera' inline)
                    excluded_hashes=seen_hashes,
                    llm_call=llm_call, lang=lang, catalog=catalog,
                    exclude_tools=exclude_tools)
            except Exception as ex:
                log.warning(
                    "MetisProposer grammar-multi call %d/%d failed: %r",
                    i + 1, n, ex)
                continue
            if not fw:
                break  # nessun framework piu' generabile
            out.append(fw)
            try:
                from .executor import compute_framework_hash
                seen_hashes.add(compute_framework_hash(fw))
            except Exception:
                pass
        return out

    def _rank_by_telos(self, candidates, *, intent, lang):
        """Rank deterministico telos-aware. Fix #4: no dead alignment_engine
        import branch. Fix #7: sempre _heuristic_telos_score, no fallback
        a structural-only."""
        scored = []
        for fw in candidates:
            try:
                sig = self._framework_signature(fw, intent)
                score = _heuristic_telos_score(sig, intent=intent)
            except Exception:
                score = 0.0
            scored.append((score, fw))
        scored.sort(key=lambda x: -x[0])
        return [fw for _, fw in scored]

    def _framework_signature(self, fw, intent):
        return {
            "n_steps": len(fw.steps),
            "tools": [s.tool for s in fw.steps],
            "verb": intent.verb,
            "object": intent.object,
        }


def _parse_candidates(raw: str) -> list[Framework]:
    """Estrae N framework JSON dall'output Mētis. Tollera:
      - JSON array `[{...}, {...}]`
      - Multiple JSON objects separati da newline
      - `<think>` blocks (strip)
    """
    if not raw:
        return []
    raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE)
    out: list[Framework] = []

    # Path 1: JSON array completo
    arr_match = re.search(r"\[\s*\{[\s\S]*\}\s*\]", raw)
    if arr_match:
        try:
            arr = json.loads(arr_match.group(0))
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, dict):
                        out.append(Framework.from_dict(item))
                if out:
                    return out
        except json.JSONDecodeError:
            pass

    # Path 2: multiple JSON objects separati.
    blocks = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw)
    for b in blocks:
        try:
            parsed = json.loads(b)
            if isinstance(parsed, dict) and parsed.get("steps"):
                out.append(Framework.from_dict(parsed))
        except json.JSONDecodeError:
            continue
    return out


def _heuristic_score(fw: Framework, intent: Intent) -> float:
    """Score deterministic structural per framework. Più alto = preferito.

    Penalità: troppi step, tool ripetuto consecutivo, request_new_executor.
    Bonus: producer corretto per verbo intent.

    Esposto per backward-compat. Preferire `_heuristic_telos_score`.
    """
    if not fw.steps:
        return 0.0
    score = 1.0
    n_real = sum(1 for s in fw.steps if s.tool != "final_answer")
    score -= max(0, n_real - 3) * 0.15
    tools = [s.tool for s in fw.steps if s.tool != "final_answer"]
    for i in range(1, len(tools)):
        if tools[i] == tools[i - 1]:
            score -= 0.3
    if any(s.tool == "request_new_executor" for s in fw.steps):
        score -= 0.5
    if intent.verb and tools and intent.verb in tools[0]:
        score += 0.2
    return max(0.0, score)


def _heuristic_telos_score(sig: dict, *, intent: Optional[Intent] = None) -> float:
    """Telos-aware score basato su signature framework. Fix #7: include
    anche bonus structural (verb-match) per consistenza con _heuristic_score
    nel fallback path.

    Telos pesi (ADR 0157):
      t.tempo 0.25 (preferire pipeline brevi)
      t.puntualita 0.20
      t.protezione 0.20
      t.ordine 0.15
      t.discrezione 0.10
      t.parsimonia 0.10 (preferire fewer LLM calls)

    Mapping euristico:
      n_steps basso → +tempo +parsimonia
      tools coerenti con verb → +ordine
      final_answer presente → +ordine (pipeline terminato pulito)
    """
    n = sig.get("n_steps", 0)
    score = 0.5
    if n <= 3:
        score += 0.25
    elif n <= 5:
        score += 0.1
    else:
        score -= 0.1
    score += 0.1 if n <= 4 else 0.0
    tools = sig.get("tools", [])
    if tools and "final_answer" in tools:
        score += 0.15
    # Fix #7: include verb-match bonus (era solo in _heuristic_score)
    verb = sig.get("verb") or (intent.verb if intent else None)
    if verb and tools:
        real_tools = [t for t in tools if t != "final_answer"]
        if real_tools and verb in real_tools[0]:
            score += 0.2
    return score
