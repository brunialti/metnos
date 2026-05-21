# SPDX-License-Identifier: AGPL-3.0-only
"""telos_lenses — pacchetto delle 9 lenti laterali del telos engine.

Ogni lens espone:
- NAME (str)
- OPERATORS (tuple di label, una call LLM per operator)
- build_prompt(ctx: LensCtx, operator: str) -> str

Il loop LLM + parse + paternalism + grammar wiring e' centralizzato in
`_base.run_lens()`. Le lenti diventano dati (prompt + operatori), non
codice duplicato.

Env toggle individuale: METNOS_TELOS_LENS_<NAME>=1.

Lenti registrate (10/5/2026 -> 21/5/2026 batch):
  scamper                — 7 operatori SCAMPER (Eberle 1971)
  oulipo                 — vincolo deliberato (OuLiPo)
  inverse_rl             — discover_unstated_telos da turni soddisfatti
  endgame_book           — precompute pattern di scadenze
  analogy_transfer       — strategia A→B dominio strutturalmente simile
  boden_transformational — revisione contratto executor
  compression            — super-verbo Schmidhuber
  pattern_language       — grammatica componibile (Alexander)
  generative_design      — Pareto candidates per brief composto
"""
import os
import importlib

from ._base import LensCtx, LensProposal, run_lens, paternalism_check

# Tutte le lenti vivono in moduli con nome = NAME della lens.
_LENS_NAMES = (
    "scamper",
    "oulipo",
    "inverse_rl",
    "endgame_book",
    "analogy_transfer",
    "boden_transformational",
    "compression",
    "pattern_language",
    "generative_design",
)


def _load_lens(name: str):
    """Carica il modulo lens (cached da Python's import system)."""
    return importlib.import_module(f"telos_lenses.{name}")


LENSES = {name: _load_lens(name) for name in _LENS_NAMES}

# Lenti che propongono CONCETTI (telos, vincolo) anziche' executor:
# il loro output non si adatta allo schema GBNF canonical
# (new_op_name=null sempre). Per queste il dispatcher disabilita
# la grammar e si affida ai soli vincoli prompt + paternalism filter.
#
# NB: `compression` propone super-verbi che DEVONO restare vocab-compliant
# (verb_object canonical); resta sotto grammar. Se Gemma 26B non riesce a
# trovare un canonical valido, ritorna [] (preferito a invenzione).
LENSES_NO_GRAMMAR = frozenset({"inverse_rl"})


def is_lens_enabled(lens_name: str) -> bool:
    """True se la lente `<lens_name>` e' attiva via env."""
    flag = f"METNOS_TELOS_LENS_{lens_name.upper()}"
    return os.environ.get(flag, "0") == "1"


def active_lenses() -> list[str]:
    """Ritorna i nomi delle lenti attive (env-toggled)."""
    return [n for n in _LENS_NAMES if is_lens_enabled(n)]


__all__ = [
    "LensCtx", "LensProposal", "run_lens", "paternalism_check",
    "LENSES", "LENSES_NO_GRAMMAR", "is_lens_enabled", "active_lenses",
]
