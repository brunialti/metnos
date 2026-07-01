"""engine/proposer_v3.py — Engine v3: redesign del proposer per compound complessi.

OBIETTIVO (blueprint project_proposer_compound_redesign, 18/6/2026): comporre in
modo UNIVERSALE query di complessita' pari a FASE 3 issue-flow — 4+ clausole con
PROVIDER corretto, ORDINE corretto, ARGS corretti. Su 1-2 clausole v2 (metis)
basta; su 4+ i tre layer LLM-liberi (GBNF solo-nomi + skeleton non-vincolante +
args ~100% LLM) collassano. v3 INVERTE l'architettura: scheletro VINCOLANTE per
struttura+ordine+provider+tipi, LLM solo per gli args testuali liberi.

SWAPPABLE con v2 (§7.1, Roberto 18/6): METNOS_ENGINE=v3 ↔ =metis e' un rollback
ISTANTANEO. v2 (MetisProposer/SimpleProposer) resta byte-intatto: v3 e' una
SOTTOCLASSE che riusa il wrapper multi-candidate/rank/cache di metis e ne swappa
il CORE di generazione (self._simple = SimpleProposerV3) via il seam _make_simple.
Niente ramo `if v3:` sparso nei moduli v2; il solo gate condiviso e' engine.is_v3()
nei guard deterministici di dispatch (comportamento AGGIUNTIVO, mai sottrattivo).

Roadmap a fasi bench-gated (routing_subset 29/29 + intent gold 25/25 + suite 2730):
  - Phase 0 (questo commit): skeleton swappable, v3 ≡ v2 (zero override sostanziali).
  - P1: provider-gating del pool (GAP-B) — override _effective_pool.
  - P2: scheletro vincolante + reorder deterministico (GAP-C ordine).
  - P3: args tipizzati nella GBNF + operation-defaults (GAP-C args) — override _build_grammar.
  - P4: tool-graph tipizzato + validatori deterministici + repair.

§7.9: il dispatcher (get_proposer) e i guard restano deterministici; l'LLM vive
solo dentro la generazione, come in v2.
"""
from __future__ import annotations

import logging

from .proposer import SimpleProposer
from .proposer_metis import MetisProposer

log = logging.getLogger(__name__)


class SimpleProposerV3(SimpleProposer):
    """Core di generazione v3 (pool→grammar→LLM→parse).

    P1 (GAP-B): override _effective_pool col provider-gating ANCHE sui compound.
    Le fasi successive sovrascrivono altri seam (_build_grammar per P3, ecc.).
    """

    def _effective_pool(self, *, query, intent, pool, catalog, exclude_tools):
        """P1 — provider-gating sul pool effettivo (GAP-B redesign).

        Il verb-filter di v2 e' SALTATO sui compound (perderebbe i sotto-intenti
        find+write+send) → lì il provider non era MAI gateato: `send_messages`
        generico restava in pool accanto a `send_messages_github` e l'LLM
        sceglieva il generico (FASE 3 publish). provider_gate_names forza UN
        solo provider per clausola quando il marker e' nella query, e nasconde
        la variante provider senza marker. Si applica DOPO il verb-filter v2,
        su QUALSIASI numero di clausole. Deterministico §7.9, no LLM."""
        base = super()._effective_pool(
            query=query, intent=intent, pool=pool,
            catalog=catalog, exclude_tools=exclude_tools)
        try:
            from tool_grammar import provider_gate_names
            gated, excluded = provider_gate_names(base, query)
            if excluded:
                log.info("v3 provider-gate: pool %d → %d (esclusi: %s)",
                         len(base), len(gated), excluded)
            return gated
        except Exception as ex:  # §2.8: traccia, pool invariato (mai bloccare)
            log.warning("v3 provider-gate fallito (%r) — pool invariato", ex)
            return base


class MetisV3Proposer(MetisProposer):
    """Engine v3 di produzione: wrapper metis (multi-candidate + telos-rank +
    cache LRU) sul core di generazione v3. Selezione via METNOS_ENGINE=v3.

    Eredita TUTTO da MetisProposer (propose/_generate_candidates/_rank_by_telos/
    cache) e swappa SOLO il core via _make_simple → ogni generazione passa per
    SimpleProposerV3. Drop-in di prod: la stessa hardening di metis
    (grammar+verb_filter ON) resta valida; rollback = METNOS_ENGINE=metis."""

    def _make_simple(self, prompt_loader):
        return SimpleProposerV3(prompt_loader=prompt_loader)
