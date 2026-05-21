# SPDX-License-Identifier: AGPL-3.0-only
"""generative_design.py — Pareto candidates per brief composto.

Per UN brief composto da piu' vincoli/obiettivi, genera 2-3 candidati
con trade-off espliciti. L'utente sceglie esplicitamente nel digest;
Metnos non decide per lui.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_SCHEMA,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "generative_design"
OPERATORS = ("pareto_brief",)

_TELOS_REGISTRY = """ALTRI TELOS NEL REGISTRO (per trade-off Pareto):
t.tempo (efficienza) / t.ordine (stabilita') / t.puntualita (deadline) /
t.protezione (privacy) / t.discrezione (no rumore) / t.parsimonia (no costo) /
t.coltivazione_strumenti (capacita' locale)."""


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""{SHARED_PREAMBLE}

TELOS DI RIFERIMENTO: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{_TELOS_REGISTRY}

{context_block(ctx)}

OPERATORE: pareto_brief.
COSA FARE: scegli un BRIEF COMPOSTO che riguarda comportamento Metnos
(es. "design scheduler notturno", "design digest serale"). Genera
2-3 CANDIDATI Pareto-ottimali con trade-off espliciti su 2-3 telos
contrapposti.

Output: target = executor centrale del candidato;
proposed_action: "CANDIDATO X: <descrizione> | trade-off: ↑t.<A> ↓t.<B>"

{SHARED_NAMING_SCHEMA}

{SHARED_OUTPUT_FORMAT}
"""
