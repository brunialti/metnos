# SPDX-License-Identifier: AGPL-3.0-only
"""pattern_language.py — grammatica componibile (Christopher Alexander).

Concept-only lens: identifica MICRO-PATTERN astratti ricorrenti nelle
pipeline turn_log (con placeholder, non sequenze esatte). Non variant
di executor. new_op_name resta null.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_NULL,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "pattern_language"
OPERATORS = ("micro_pattern",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""{SHARED_PREAMBLE}

TELOS: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{context_block(ctx)}

OPERATORE: micro_pattern.
COSA FARE: estrai dal mnestoma una RICETTA ASTRATTA con placeholder
(non sequenza esatta). Es:
  "ispeziona-poi-agisci": find_<obj> → describe_entries → <action>
  "verify-after-mutate": move/write/delete → compute_signatures

Output: target_executor = pivot del pattern;
proposed_action inizia con "PATTERN <kebab-name>: <step1> → <step2> → ..."

{SHARED_NAMING_NULL}

{SHARED_OUTPUT_FORMAT}
"""
