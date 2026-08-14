# SPDX-License-Identifier: AGPL-3.0-only
"""scamper.py — lente SCAMPER (Eberle 1971, Osborn 1953).

7 operatori brainstorming applicati ai top-N executor del catalog.
Naming-aware: puo' proporre new_op_name canonical o 4-livello.

I blocchi VINCOLI/MET-FEATURES/SCHEMA/OUTPUT sono iniettati da _base
(SHARED_*) — non ripetere qui per evitare prompt explosion.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_SCHEMA,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "scamper"
OPERATORS = ("S", "C", "A", "M", "P", "E", "R")

_OP_NAMES = {
    "S": "Substitute",
    "C": "Combine",
    "A": "Adapt",
    "M": "Modify",
    "P": "Put_to_other_use",
    "E": "Eliminate",
    "R": "Reverse",
}
_OP_DESCRIPTIONS = {
    "S": "SOSTITUISCI una componente di un executor con un'alternativa funzionalmente analoga (sorgente, formato, scope).",
    "C": "COMBINA due executor in una pipeline non ancora vista (cross-domain o cross-corpus).",
    "A": "ADATTA un executor a un contesto utente non standard (multi-account, multi-lingua, multi-canale).",
    "M": "MODIFICA i parametri di default di un executor per allinearli al pattern d'uso reale.",
    "P": "USA un executor in un contesto non canonico (es. compute_files_loc su README).",
    "E": "ELIMINA uno step ridondante, un parametro inutilizzato, un cap che non serve mai.",
    "R": "INVERTI il verbo: se write fa X, prova read di X (log inverse, undo memoizzato, dry-run).",
}


def _anti_fixation_block(ctx: LensCtx, operator: str) -> str:
    """Anti-lazy-fixation: lista i nomi gia' emessi da operator precedenti
    e impone diversita'. Vuoto se siamo al primo operator."""
    if not ctx.previous_proposals:
        return ""
    prev_names = sorted({
        p.get("new_op_name") for p in ctx.previous_proposals
        if p.get("new_op_name")
    })
    prev_targets = sorted({
        p.get("executor_target") for p in ctx.previous_proposals
        if p.get("executor_target")
    })
    if not prev_names and not prev_targets:
        return ""
    lines = ["ANTI-FIXATION (operator precedenti hanno gia' emesso):"]
    if prev_names:
        lines.append(f"  nomi: {', '.join(prev_names)}")
    if prev_targets:
        lines.append(f"  target: {', '.join(prev_targets)}")
    lines.append(f"DEVI: produrre proposta strutturalmente DIVERSA per l'operatore {operator}.")
    lines.append("NON DEVI: ripetere un new_op_name gia' emesso o ricalcare lo stesso pattern.")
    return "\n".join(lines)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    anti_fix = _anti_fixation_block(ctx, operator)
    return f"""{SHARED_PREAMBLE}

TELOS: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{context_block(ctx)}

OPERATORE SCAMPER {operator} ({_OP_NAMES[operator]}):
{_OP_DESCRIPTIONS[operator]}

{anti_fix}

Genera 1-3 proposte concrete che applicano l'operatore {operator} a uno
degli executor del catalog vivo sopra.

{SHARED_NAMING_SCHEMA}

{SHARED_OUTPUT_FORMAT}
"""
