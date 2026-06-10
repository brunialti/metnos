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
  METNOS_PROPOSER_GRAMMAR=1 (default) → single-shot grammar (perde multi-candidate)

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
from .proposer import (SimpleProposer, _iter_balanced_json_objects,
                       _render_excluded_signal, _render_tool_pool,
                       _strip_think)

log = logging.getLogger(__name__)


def _is_action_verb(verb: str) -> bool:
    """True se `verb` e' un verbo d'AZIONE side-effecting (§7.9, SoT vocab):
    canonico (ACTIONS) ma NON safe-by-construction (SAFE_VERBS = produttori
    find/get/read/list/filter + trasformatori in-memory). La query chiede un
    EFFETTO (move/delete/send/create/write/...) → rischio producer-bias: il
    proposer tende a preferire il produttore anche quando il tool d'azione
    e' nel pool. Segnale REALE che sostituisce il gating-confidence morto
    (B2: intent_extractor non emette mai `confidence` → resta il default 1.0
    di types.Intent → il gate `conf >= thr` era sempre vero → Metis girava
    sempre col floor N=1, niente hedge ne' re-rank)."""
    if not verb:
        return False
    try:
        from vocab import ACTIONS, SAFE_VERBS
    except Exception:
        return False
    v = verb.lower().strip()
    return v in ACTIONS and v not in SAFE_VERBS


# Proxy deterministico §4.2 (caso degenere N=1 con literal): filename con
# estensione («relazione.pdf», «config.json») oppure URL esplicito. NB: un
# path-directory nudo («/tmp/cache») o un plurale («i pdf», «gli eventi di
# ieri») indicano un INSIEME da individuare → niente match.
_EXPLICIT_TARGET_RE = re.compile(
    r"https?://\S+|\b[\w][\w-]*\.[A-Za-z][A-Za-z0-9]{1,4}\b")


def _has_explicit_target(query: str) -> bool:
    """True se la query NOMINA un target puntuale afferrabile inline (§4.2).

    Solo allora l'hedge azione-first ha senso: il tool d'azione puo' ricevere
    il literal direttamente (paths/entries inline) senza producer davanti.
    Su un INSIEME da selezionare (pattern, finestra temporale) il piano
    producer→azione resta quello giusto e l'hedge NON va generato — cosi'
    i multi-step legittimi find→move/read→delete non regrediscono."""
    return bool(_EXPLICIT_TARGET_RE.search(query or ""))


def _n_candidates(intent=None) -> int:
    """N candidati ADATTIVO (deterministico, no ML — §7.9).

    Il proposer metis genera N piani-candidato (N chiamate LLM grammar-constrained
    sulla GPU SINGOLA → costo dominante del turno) e ne sceglie uno via telos-rank
    euristico. Su query ad ALTA confidenza e NON-compound la prima proposta è
    affidabile → N=1 (niente spreco, ~3-5x più veloce). Budget hedge N=ceiling su:
      - COMPOUND (>=2 azioni): champion+challenger full-pool (invariato);
      - verbo d'AZIONE side-effecting (anti producer-bias, 9/6/2026): il
        gating-confidence era MORTO (B2, vedi _is_action_verb) → il segnale
        reale e' il verbo stesso. La spesa EFFETTIVA del budget la decide
        _generate_grammar_multi (early-stop se il primo candidato e' gia'
        azione-first → nessuna latenza extra nel caso sano);
      - BASSA confidenza (gate legacy, torna utile se l'extractor la emettera').
    Nessun ranker ML ([[no_training_amplify_reality]]). Env: ceiling
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
    if _is_action_verb(getattr(intent, "verb", "") or ""):
        return ceil
    return fast if (conf >= thr and not compound) else ceil


# Handicap del challenger compound generato con esclusione HARD del primo
# tool del campione (B15, vedi _rank_by_telos). Calibrazione: < malus dup
# identici (0.3: un campione con step duplicati DEVE perdere contro il
# challenger pulito) e >= gap-brevita' tipico 3-vs-5 step (0.25: a parita'
# vince il campione per stabilita' del sort).
_EXCLUDED_HEDGE_HANDICAP = 0.25


def _cache_max() -> int:
    """Max entries cache, default 200. Fix #3 (no unbounded growth).
    Min 1 (no zero/negative) — clamp basso permette test cap=2,3,..."""
    try:
        return max(1, int(os.environ.get("METNOS_METIS_CACHE_MAX", "200")))
    except ValueError:
        return 200


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
                    except Exception as ex:
                        # §2.8: traccia l'hash fallito (il candidato cached
                        # viene saltato; flusso invariato).
                        log.debug("MetisProposer: compute_framework_hash "
                                  "fallito su candidato cached: %r", ex)
                        h = ""
                    if h and h not in excluded_hashes:
                        log.info(
                            "MetisProposer: retry serving cached alternative "
                            "(hash=%s) without LLM call", h[:8])
                        return cached

        candidates = self._generate_candidates(
            query=query, intent=intent, pool=pool,
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
            if excluded_hashes:
                # Tutti i candidati sono in excluded_hashes: rispetta il
                # contratto del Protocol (proposer.py: «rispetta excluded_hashes»)
                # e NON riconsegnare un framework gia' bocciato → return None.
                # Il caller (guard/validator/recovery in dispatch) ritenta o
                # termina onesto (§2.8). Prima si ritornava ranked[0] escluso:
                # il validator (dispatch.py) accettava framework2==framework
                # senza ri-validarlo (B1, esame 9/6/2026).
                return None
        except Exception:
            pass
        return ranked[0] if ranked else candidates[0]

    def _generate_candidates(self, *, query, intent, pool,
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
        # B6 — default "1", allineato a proposer.py e alla produzione (drop-in
        # proposer-hardening.conf): prima era "0" e con env non settata il
        # path metis ignorava la grammar che il path simple applicava
        # (determinismo §7.9 e parse rate degradati in silenzio).
        use_grammar = os.environ.get("METNOS_PROPOSER_GRAMMAR", "1") == "1"
        n_cands = _n_candidates(intent)
        if use_grammar:
            return self._generate_grammar_multi(
                query=query, intent=intent, pool=pool,
                excluded_hashes=excluded_hashes, llm_call=llm_call,
                lang=lang, catalog=catalog, n=n_cands,
                exclude_tools=exclude_tools)
        # Default: 1 call, array output. Il render del pool serve SOLO a
        # questo path (perf 10/6/2026): sul path grammar SimpleProposer
        # renderizza il SUO pool effettivo (post verb-filter) — renderlo in
        # propose() prima del branch era lavoro morto in produzione.
        tools_inline = _render_tool_pool(pool, catalog)
        try:
            system = self._load_prompt(
                "engine_proposer_metis", lang,
                verb=intent.verb,
                obj=intent.object,
                keywords=", ".join(intent.keywords),
                tools=tools_inline,
                # B15: forma leggibile dei piani esclusi + istruzione di
                # diversificazione (non hash sha opachi che il modello ignora).
                excluded=_render_excluded_signal(excluded_hashes, lang),
                n_candidates=n_cands,
            )
        except Exception as ex:
            # §2.8: prompt-load fallito = zero candidati dal path array
            # (il caller degrada a SimpleProposer); traccia la causa.
            log.warning("MetisProposer: load prompt 'engine_proposer_metis' "
                        "fallito (lang=%s): %r", lang, ex)
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
        diversità. Costo: N× LLM call vs 1× del default path.

        Hedge anti producer-bias (9/6/2026, §7.9): per intent MONO-azione con
        verbo side-effecting il budget extra (N>=2 da _n_candidates) si spende
        SOLO come HEDGE-DIVERGENZA: una call col pool ristretto ai tool del
        verbo d'azione, generata SOLO se (a) il primo candidato NON inizia
        gia' col tool d'azione E (b) la query nomina un target puntuale
        (_has_explicit_target, §4.2). Cosi' il candidato azione-first ESISTE
        per costruzione e il telos-rank (bonus verb-match) puo' preferirlo —
        con seed fisso una seconda call full-pool sarebbe un quasi-duplicato
        (stesso prompt ±hash escluso → stesso output). NON e' il verb-filter
        morbido B8: la generazione #1 resta full-pool e l'hedge e' un
        candidato AGGIUNTIVO che il rank confronta, non una restrizione
        dell'unica proposta. COMPOUND: comportamento invariato (N call
        full-pool: il piano deve coprire tutte le clausole)."""
        out: list[Framework] = []
        seen_hashes: set[str] = set(excluded_hashes or set())
        verb = (getattr(intent, "verb", "") or "").lower().strip()
        is_compound = len(getattr(intent, "actions", None) or []) >= 2
        # Pool hedge = SOLI tool del verbo d'azione (la GBNF aggiunge sempre
        # `final_answer`). None = hedge non applicabile.
        hedge_pool = None
        if (not is_compound and _is_action_verb(verb)
                and _has_explicit_target(query)):
            hedge_pool = [t for t in pool
                          if isinstance(t, str)
                          and t.split("_", 1)[0] == verb] or None
        for i in range(n):
            cur_pool = pool
            cur_excl = exclude_tools
            hard_excluded = False
            if i > 0 and not is_compound:
                # Budget extra mono-azione: si spende SOLO come hedge.
                if hedge_pool is None:
                    break
                if any(fw.steps and (fw.steps[0].tool or "").split("_", 1)[0]
                       == verb for fw in out):
                    break  # gia' azione-first: hedge superfluo (latenza)
                cur_pool = hedge_pool
            elif i > 0:
                # B15 — challenger COMPOUND: diversita' STRUTTURALE per
                # COSTRUZIONE (§7.9), non per speranza. Il primo tool dei
                # candidati gia' generati esce dal pool del challenger
                # (prompt + grammar GBNF): con seed fisso e think=False il
                # solo segnale testuale non basta (misura 10/6: 3/3 stessa
                # sequenza) e una seconda call full-pool e' un quasi-
                # duplicato → spreco. Cosi' l'alternativa parte da un tool
                # fratello (es. read_* vs find_*) e il telos-rank confronta
                # due strade REALI; se il challenger e' peggiore il campione
                # vince comunque (sort stabile, pari merito → primo).
                _firsts = {(fw.steps[0].tool or "") for fw in out
                           if fw.steps and fw.steps[0].tool
                           and fw.steps[0].tool != "final_answer"}
                if _firsts:
                    cur_excl = tuple(set(exclude_tools) | _firsts)
                    hard_excluded = True
            try:
                fw = self._simple.propose(
                    query=query, intent=intent,
                    pool=cur_pool,  # propaga pool reale (SimpleProposer rendera' inline)
                    excluded_hashes=seen_hashes,
                    llm_call=llm_call, lang=lang, catalog=catalog,
                    exclude_tools=cur_excl)
            except Exception as ex:
                log.warning(
                    "MetisProposer grammar-multi call %d/%d failed: %r",
                    i + 1, n, ex)
                continue
            if not fw:
                break  # nessun framework piu' generabile
            if hard_excluded:
                # Marca il challenger generato con pool AMPUTATO: il rank gli
                # applica _EXCLUDED_HEDGE_HANDICAP (non compete alla pari
                # sulla brevita' — vedi _rank_by_telos).
                fw._metis_excluded_hedge = True
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
        a structural-only.

        Handicap challenger hard-escluso (B15): un candidato generato col
        primo tool del campione FUORI dal pool non e' i.i.d. col campione
        (scelta full-pool, piu' informata) — senza handicap il solo bonus-
        brevita' (max 0.25 fra 3 e 5 step) bastava a farlo vincere anche
        quando semanticamente storto. Con 0.25: a parita' il campione vince
        (pareggio → sort stabile → primo); il challenger passa SOLO per
        difetti strutturali del campione (dup identici -0.3, monco -0.4) o
        vantaggio strutturale maggiore. L'hedge MONO-azione (azione-first,
        9/6) NON e' marcato: deve vincere col verb-match +0.2 a parita'."""
        scored = []
        for fw in candidates:
            try:
                sig = self._framework_signature(fw, intent)
                score = _heuristic_telos_score(sig, intent=intent)
                if getattr(fw, "_metis_excluded_hedge", False):
                    score -= _EXCLUDED_HEDGE_HANDICAP
            except Exception:
                score = 0.0
            scored.append((score, fw))
        scored.sort(key=lambda x: -x[0])
        return [fw for _, fw in scored]

    def _framework_signature(self, fw, intent):
        return {
            "n_steps": len(fw.steps),
            "tools": [s.tool for s in fw.steps],
            # (tool, args) per il malus dup: distingue la ripetizione ROTTA
            # (step identico) dal multi-step verboso (stesso tool, args
            # diversi) — vedi _heuristic_telos_score.
            "steps_sig": [
                (s.tool, json.dumps(s.args or {}, sort_keys=True,
                                    ensure_ascii=False, default=str))
                for s in fw.steps
            ],
            "verb": intent.verb,
            "object": intent.object,
        }


def _parse_candidates(raw: str) -> list[Framework]:
    """Estrae N framework JSON dall'output Mētis. Tollera:
      - JSON array `[{...}, {...}]` (le parentesi quadre sono prosa per
        l'iteratore bilanciato: gli oggetti-graffa interni vengono comunque
        yieldati uno a uno)
      - Multiple JSON objects separati da prosa/newline
      - `<think>` chiusi o lasciati aperti dal troncamento (B5)

    B4: le vecchie regex (array greedy + oggetti a max 1 livello di nesting)
    perdevano i framework annidati steps→step→args → candidati persi. Ora
    estrazione a oggetti `{...}` BILANCIATI (`_iter_balanced_json_objects`),
    tenendo solo i dict con "steps" non vuoto."""
    if not raw:
        return []
    out: list[Framework] = []
    for block in _iter_balanced_json_objects(_strip_think(raw)):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("steps"):
            out.append(Framework.from_dict(parsed))
    return out


def _heuristic_telos_score(sig: dict, *, intent: Optional[Intent] = None) -> float:
    """Telos-aware score basato su signature framework. Fix #7: include
    anche bonus structural (verb-match), ereditato dal vecchio
    `_heuristic_score` (rimosso 9/6/2026: mai chiamato, dead code).

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
    # Malus pipeline rotta (§7.9, enforcement deterministico della regola
    # prompt «NON DEVI ripetere lo stesso tool consecutivo»): un candidato
    # con step consecutivi IDENTICI — stesso tool E stessi args (es. doppio
    # get_files di fila, compound issue→spreadsheet 9/6/2026) — e' SEMPRE
    # peggiore dell'alternativa pulita → declassato. Lo stesso tool con args
    # DIVERSI (read_urls_html per url-1, url-2, ... quando il modello non
    # usa la projection `*`) e' VERBOSO ma legittimo: il malus tool-only lo
    # declassava (falso positivo 10/6, «cerca online cos'e' ROCm») e
    # promuoveva un challenger semanticamente storto.
    seq = sig.get("steps_sig") or tools
    if any(a == b for a, b in zip(seq, seq[1:])):
        score -= 0.3
    # Malus compound-MONCO (B15, difesa dell'hedge §7.9): query con >=2
    # clausole {verb,object} ma piano con MENO step-executor delle clausole
    # → quasi certamente non copre la richiesta (challenger degenerato o
    # collasso). Senza questo, il bonus-brevita' premiava un challenger
    # 2-step assurdo (es. find_pulls_github→final per «trova i .log e
    # cancellali») sopra il campione completo a 4 step. -0.4 > gap massimo
    # brevita'+verb (0.35): un piano completo vince SEMPRE su un monco; fra
    # due piani entrambi sotto-clausola (es. 1 tool copre 2 clausole §4.2)
    # il malus e' pari e l'ordine relativo resta invariato.
    acts = len(getattr(intent, "actions", None) or []) if intent else 0
    if acts >= 2:
        n_exec = sum(1 for t in tools if t != "final_answer")
        if n_exec < acts:
            score -= 0.4
    # Fix #7: verb-match bonus (ereditato dal vecchio _heuristic_score).
    # B12: match sul PRIMO SEGMENTO del nome tool (= verbo canonico §2.2),
    # NON substring — "get" non deve matchare "widgets"/"forget_*".
    verb = sig.get("verb") or (intent.verb if intent else None)
    if verb and tools:
        real_tools = [t for t in tools if t != "final_answer"]
        if real_tools and real_tools[0].split("_", 1)[0] == verb:
            score += 0.2
    return score
