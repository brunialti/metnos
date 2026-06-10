"""engine/routing_pool.py — costruzione del pool di routing (funzione PURA).

Estratta da `dispatch.run_turn` (fix B3, 9/6/2026): prima il guard
anti-regressione `bench/routing_subset_bench.py` RE-implementava una versione
semplificata del pool (k=10 fisso, niente compound per-clausola, niente
universal-helpers, niente companions) e poteva restare verde mentre la
produzione regrediva su quei layer. Ora dispatch e bench chiamano la STESSA
funzione: ogni modifica al pool di produzione e' esercitata dal bench.

Contratto:
  - PURA rispetto al turno: ZERO esecuzione executor, ZERO chiamate LLM,
    ZERO scritture. Legge solo env (`METNOS_ENGINE_POOL_SIZE`; il prefilter
    legge `METNOS_PREFILTER_RULES`) e il catalog passato (mai mutato).
  - Comportamento IDENTICO al segmento storico di run_turn (ADR 0164):
    intent completo → rank_with_intent(k) → fallback BoW min_score=0 →
    unione pool per-clausola (intent.actions o verbi canonici) + famiglia
    object → append universal-helpers → companions producer→consumer.
    Intent incompleto o prefilter rotto → full catalog (il Proposer decide).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


# Producer → consumer naturale da iniettare sempre nel pool (§7.3 companion).
# Un producer il cui output non è azionabile senza il consumer.
_POOL_COMPANIONS = {
    "find_urls": ["read_urls_html", "read_urls_pdf"],
}


def build_routing_pool(query: str, intent, catalog: list, *,
                        k: int | None = None) -> list[str]:
    """Da (query, intent, catalog) → lista NOMI tool per il Proposer.

    Pool reduction via prefilter (ADR 0164 fix): invece di passare TUTTO
    il catalog (~80 tool, prompt 400+ righe) a Mētis, prefiltriamo per
    intent semantic match. Top-K (default 12) coprono >90% intent canonici
    con prompt 5-10× più piccolo → -30-40% latency Mētis.

    Args:
      query: testo utente grezzo (per ranking lessicale e verbi canonici).
      intent: engine.types.Intent (verb/object/keywords/actions).
      catalog: lista executor (oggetti con .name); MAI mutata.
      k: dimensione pool per clausola; None → env METNOS_ENGINE_POOL_SIZE
         (default 12). Il pool finale può eccedere k per universal-helpers,
         companions e completamento famiglia-object (come in produzione).
    """
    pool_size = (int(k) if k is not None
                 else int(os.environ.get("METNOS_ENGINE_POOL_SIZE", "12")))
    if intent.is_complete():
        try:
            from prefilter import rank_with_intent, rank as _rank_bow
            intent_dict = {"verb": intent.verb, "object": intent.object,
                            "keywords": intent.keywords}
            # Compound multi-verbo (§7.3): se la query ha >=2 verbi canonici,
            # il pool MONO-verbo di rank_with_intent escluderebbe i tool degli
            # altri sotto-intenti (es. find+write+send → "trova le issue,
            # salvale, mandami il riassunto"). Uniamo il ranking per OGNI verbo
            # canonico presente nella query cosi' il Proposer vede l'intera
            # pipeline. Bug 2/6/2026: senza unione il pool era solo find_* →
            # niente write_files/send_messages → "salva"/"manda" impossibili.
            filtered = None
            # Compound routing (4/6): PREFERISCI la decomposizione per-clausola
            # dell'intent LLM (`intent.actions` = [{verb,object}, ...]). Ogni
            # clausola rankizza il pool con il SUO object reale → i producer di
            # OGNI sotto-azione entrano nel pool. Prima il ramo rankizzava ogni
            # verbo con un UNICO `intent.object` (quello di UNA sola clausola, di
            # solito il bersaglio finale): "trova i processi ... scrivi un report"
            # → object=files per tutti → get_processes mai nel pool → Proposer
            # collassa su get_inputs (bug q20/q21 4/6). La decomposizione è
            # multilingue per costruzione (LLM) e NON usa dizionari di sinonimi.
            _pairs = []
            _acts = getattr(intent, "actions", None) or []
            if len(_acts) >= 2:
                _pairs = [((a.get("verb") or intent.verb),
                           (a.get("object") or intent.object)) for a in _acts]
            if not _pairs:
                # Fallback deterministico (LLM non ha decomposto): verbi canonici
                # rilevati nella query, con l'object PRIMARIO condiviso (storico).
                try:
                    from prefilter import (tokenize as _pf_tok,
                                            detect_canonical_verbs_all as _pf_dv)
                    _qverbs = list(dict.fromkeys(_pf_dv(_pf_tok(query))))
                except Exception:
                    _qverbs = []
                if len(_qverbs) >= 2:
                    _pairs = [(_v, intent.object) for _v in _qverbs]
            if _pairs:
                # Object-completezza per-clausola (4/6): l'LLM assegna verbi
                # ASTRATTI ("list"/"change") che spesso NON hanno un tool esatto
                # (no list_pulls, no change_issues) → rank_with_intent(verb,obj)
                # filtra per prefisso-verbo e PERDE il producer reale dell'object
                # (find_pulls_github, set_issues_github). Garantisci che TUTTI i
                # tool del catalog con QUELL'object (derivato dal NOME canonico,
                # 2° token in vocab.OBJECTS) siano candidati nel pool: il
                # Proposer (che vede le description) sceglie il verbo giusto.
                # Universale, deterministico §7.9, ZERO dizionari di sinonimi —
                # scala a nuove lingue (l'object è canonico, non NL). Fix q14
                # (pulls→find_pulls_github) + q15 (close→set_issues_github).
                try:
                    from vocab import OBJECTS as _VOBJ_SET
                    _VOBJ = set(_VOBJ_SET)
                except Exception:
                    _VOBJ = set()

                def _tool_object(_nm):
                    for _tok in (_nm or "").split("_")[1:]:
                        if _tok in _VOBJ:
                            return _tok
                    return ""

                _seen = {}
                _clause_objs = set()
                for _v, _o in _pairs:
                    if _o:
                        _clause_objs.add(_o)
                    _sub = rank_with_intent(
                        query, catalog,
                        {"verb": _v, "object": _o,
                         "keywords": intent.keywords},
                        k=pool_size) or []
                    for _e in _sub:
                        _seen[getattr(_e, "name", None)] = _e
                # Famiglia-object completa per ogni object delle clausole.
                for _e in catalog:
                    _nm = getattr(_e, "name", None)
                    if _nm and _nm not in _seen and _tool_object(_nm) in _clause_objs:
                        _seen[_nm] = _e
                if _seen:
                    filtered = list(_seen.values())
            if filtered is None:
                filtered = rank_with_intent(query, catalog, intent_dict,
                                            k=pool_size)
            # rank_with_intent ritorna None PER DESIGN quando il verbo intent
            # non matcha alcun executor (es. object=entries meta-oggetto, o
            # verbo intermedio di una query compound): non e' un errore, e' il
            # contratto di fallback bag-of-words (vedi prefilter.py §776). Senza
            # questo ramo il `len(pool_for_propose)` sotto crashava con
            # `len(None)` → except → full pool (80 tool) → grammar Mētis gigante
            # → wise LLM lentissimo (regressione web-search: "fondi ark" ~8min).
            if not filtered:
                filtered = _rank_bow(query, catalog, k=pool_size, min_score=0)
            # Cross-object recall affinity-based (10/6/2026, misroute live
            # "quali account mail hai?" → read_messages): l'intent puo'
            # classificare l'OBJECT sbagliato e il pool gated per object
            # esclude a monte il tool giusto (find_credentials). Un tag
            # affinity multi-parola interamente coperto dalla query (>=2
            # token distintivi, es. "quali account") forza il tool nel pool
            # ANCHE se verb/object differiscono. SCOPED (solo phrase-match
            # pieno, cap 3) per non gonfiare il pool. Deterministico §7.9,
            # zero dizionari per-frase: il dato e' l'affinity curata del
            # manifest. Vive QUI (choke-point del pool) cosi' copre il path
            # intent-driven, il fallback BoW e l'unione compound.
            try:
                from prefilter import affinity_phrase_recall
                _present = {getattr(e, "name", None) for e in filtered}
                for _x in affinity_phrase_recall(query, catalog,
                                                 exclude_names=_present):
                    filtered = filtered + [_x]
            except Exception as ex:  # §2.8: traccia, pool resta valido
                log.warning("routing_pool: affinity_phrase_recall fallita: %r",
                            ex)
            # Garantisci che fastpath / autopath catalog completo resti
            # disponibile a executor (callback usa il NOME, non il pool).
            # Pool ridotto è SOLO per il prompt Proposer.
            pool_for_propose = filtered or catalog
            # §7.3: universal helpers (describe_entries/classify_entries/...)
            # sono referenziati dai PATTERN STRUTTURALI del prompt Proposer
            # (es. READ/LIST = producer + describe_entries + final_answer) ma
            # il prefilter per-verbo non li include. Senza il loro schema nel
            # pool, il Proposer inventa valori (es. style fuori enum §8.3).
            # Append idempotente dei helper presenti nel catalog.
            try:
                from tool_grammar import _UNIVERSAL_HELPERS
                present = {getattr(e, "name", None) for e in pool_for_propose}
                for ex_obj in catalog:
                    nm = getattr(ex_obj, "name", None)
                    if nm in _UNIVERSAL_HELPERS and nm not in present:
                        pool_for_propose = pool_for_propose + [ex_obj]
                        present.add(nm)
                # §7.3 COMPANION injection (universale): un producer il cui
                # output è inutile senza un CONSUMER naturale porta sempre il
                # consumer nel pool, anche se il verbo del consumer non è nella
                # query. find_urls produce URL → senza read_urls_html/pdf la
                # catena web→contenuto è monca (il proposer non può chiuderla,
                # bug ROCm 3/6). Mappa estendibile a ogni coppia simile.
                for _prod, _comps in _POOL_COMPANIONS.items():
                    if _prod in present:
                        for _c in _comps:
                            if _c in present:
                                continue
                            _co = next((e for e in catalog
                                        if getattr(e, "name", None) == _c), None)
                            if _co is not None:
                                pool_for_propose = pool_for_propose + [_co]
                                present.add(_c)
            except Exception as ex:
                # §2.8: traccia l'injection helper/companion fallita (il pool
                # prefiltrato resta valido; flusso invariato).
                log.debug("routing_pool: injection universal-helper/companion "
                          "fallita: %r", ex)
            log.debug("routing_pool: pool reduced %d → %d via prefilter "
                       "(+helpers)", len(catalog), len(pool_for_propose))
        except Exception as ex:
            log.warning("routing_pool: prefilter failed (%r), full pool", ex)
            pool_for_propose = catalog
    else:
        pool_for_propose = catalog
    return [getattr(e, "name", None) for e in pool_for_propose
            if getattr(e, "name", None)]
