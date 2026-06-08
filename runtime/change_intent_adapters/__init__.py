"""change_intent_adapters — proietta i 6 storage legacy in ChangeIntent
(ADR 0158). Ogni adapter espone `iter_change_intents()` che yield
oggetti ChangeIntent senza side-effect (no upsert, no DB write).

L'orchestrazione (materializer) chiama gli adapter, fa upsert con dedup
cross-source via fingerprint, e aggiorna convergence.

Adapter implementati:
  - telos       (10 lenti)
  - introvertiva (dedupe/generalize/specialize)
  - synt         (request_new_executor + multistage final_state)
  - multi_tool   (L2 chain candidate/shadow)
  - canonical    (L1 single-tool candidate/shadow)
  - user_feedback (✗ → reject_pattern)
"""
from __future__ import annotations

from typing import Iterable

from change_intents import ChangeIntent

from .telos import iter_telos
from .introvertiva import iter_introvertiva
from .synt import iter_synt
from .multi_tool import iter_multi_tool
from .canonical import iter_canonical
from .user_feedback import iter_user_feedback


def iter_all() -> Iterable[ChangeIntent]:
    """Concatena tutti gli adapter. L'ordine non importa per il dedup
    (avviene per fingerprint), ma per stabilita' generiamo dai piu' stabili
    (synt: file su disco con id stabile) verso i piu' volatili (cache L1/L2)."""
    yield from iter_synt()
    yield from iter_telos()
    yield from iter_introvertiva()
    yield from iter_multi_tool()
    yield from iter_canonical()
    yield from iter_user_feedback()


__all__ = [
    "iter_all",
    "iter_telos",
    "iter_introvertiva",
    "iter_synt",
    "iter_multi_tool",
    "iter_canonical",
    "iter_user_feedback",
]
