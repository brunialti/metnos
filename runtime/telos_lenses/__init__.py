# SPDX-License-Identifier: AGPL-3.0-only
"""telos_lenses — pacchetto delle 9 lenti laterali per il telos engine.

Ogni lente e' una callable che genera proposte per servire un telos dato
mnestoma + user behavior. Ognuna ha:
- un env flag toggle METNOS_TELOS_LENS_<NAME>=1 per attivazione individuale
- telemetria specifica: hit_rate, accept_rate, distance_from_existing
- LLM tier middle, prompt dedicato

Lenti previste (vedi docs/it/drafts/telos_engine_v1.html):

  scamper           — 7 operatori brainstorming su top-N executor
  oulipo            — vincolo deliberato (settimana senza tier=wise)
  inverse_rl        — discover_unstated_telos da turni soddisfacenti
  endgame_book      — precompute pattern per t.puntualita
  analogy_transfer  — strategia A→B dominio strutturalmente simile
  boden_transform   — revisione contratto executor (es. move + reason)
  compression       — super-verbo che unifica N varianti
  pattern_language  — grammatica di pattern componibili
  generative_design — Pareto candidates per brief composto

Solo `scamper` implementato come pilota (task #12). Le altre attivabili
quando l'esperimento offline conferma il pattern (task #13).
"""
from .scamper import generate_proposals as scamper_generate

__all__ = ["scamper_generate"]


def is_lens_enabled(lens_name: str) -> bool:
    """True se la lente `<lens_name>` e' attiva via env."""
    import os
    flag = f"METNOS_TELOS_LENS_{lens_name.upper()}"
    return os.environ.get(flag, "0") == "1"
