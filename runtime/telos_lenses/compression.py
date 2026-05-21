# SPDX-License-Identifier: AGPL-3.0-only
"""compression.py — super-verbo Schmidhuber (compression gain).

Identifica 2-3 executor con args overlappanti e semantica vicina,
proponi UN super-verbo parametrizzato che li sussume. Compression gain
= meno codice + meno carico cognitivo planner.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_SCHEMA,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "compression"
OPERATORS = ("super_verbo",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""{SHARED_PREAMBLE}

TELOS: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{context_block(ctx)}

OPERATORE: super_verbo.
COSA FARE: scegli 2-3 EXECUTOR DEL CATALOG VIVO con signature simile.
Proponi UN super-verbo che li sussume con param di disambiguazione.
Il super-verbo deve restare vettoriale (§2.1) e single-purpose.

Output: target = uno dei 2-3 del cluster;
proposed_action: "COMPRESS: <execA>, <execB>[, <execC>] → <super-verbo> con param <P>. Compression: N args → M args."

{SHARED_NAMING_SCHEMA}

{SHARED_OUTPUT_FORMAT}
"""
