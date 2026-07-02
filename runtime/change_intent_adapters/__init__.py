"""change_intent_adapters — proietta gli storage proposte in ChangeIntent
(ADR 0158). Ogni adapter espone un iteratore che yield ChangeIntent senza
side-effect (no upsert, no DB write).

L'orchestrazione (materializer) chiama gli adapter, fa upsert con dedup
cross-source via fingerprint, e aggiorna convergence.

Adapter ATTIVI (2/7/2026, potatura «se un meccanismo non serve non serve»):
  - telos         (cluster-head azionabili delle 10 lenti)
  - introvertiva  (dedupe attivo; generalize/specialize solo righe storiche)
  - synt          (request_new_executor + multistage final_state)
  - user_feedback (✗ → reject_pattern)

RITIRATI 2/7/2026 (store senza writer, superati dalla cache engine):
  - multi_tool  (multi_tool_paths.sqlite: writer rimosso l'11/6, righe
    ferme al 25/5 — le catene reali le impara L1 autopath)
  - canonical   (canonical_query_log: scriveva il planner legacy, gated
    dal 30/6, righe ferme al 25/5 — superato dal fastpath L0)
I moduli vivono in git; le righe storiche in change_intents restano
(bonificate a rejected il 2/7).
"""
from __future__ import annotations

from typing import Iterable

from change_intents import ChangeIntent

from .telos import iter_telos
from .introvertiva import iter_introvertiva
from .synt import iter_synt
from .user_feedback import iter_user_feedback


def iter_all() -> Iterable[ChangeIntent]:
    """Concatena gli adapter attivi. L'ordine non importa per il dedup
    (avviene per fingerprint), ma per stabilita' generiamo dai piu' stabili
    (synt: file su disco con id stabile) verso i piu' volatili."""
    yield from iter_synt()
    yield from iter_telos()
    yield from iter_introvertiva()
    yield from iter_user_feedback()


__all__ = [
    "iter_all",
    "iter_telos",
    "iter_introvertiva",
    "iter_synt",
    "iter_user_feedback",
]
