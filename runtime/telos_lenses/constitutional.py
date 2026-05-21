# SPDX-License-Identifier: AGPL-3.0-only
"""constitutional.py — anti-fragility lens.

Ref: Bai et al., "Constitutional AI: Harmlessness from AI Feedback"
(Anthropic 2022). Idea: per ogni capacita', identificare modi di
fallimento → proporre principi/defenses che li contrastano.

In Metnos: dato un executor del catalog, propone le sue MODALITA' DI
FALLIMENTO (silent failure, edge cases, side-effects inattesi) e i
LAYER DI SICUREZZA o gli arg-defaults che li mitigano. Servive
principalmente t.protezione (integrita'/privacy) e
t.coltivazione_strumenti (executor robusti by default).

Lens naming-aware: la defense puo' essere una variant 4-livello
(es. `move_files_size_verify-on-write` se la difesa e' "compute size
+ post-write verify") oppure una proposta di reverse-pattern aggiunta
al manifest.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_SCHEMA,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "constitutional"
OPERATORS = ("failure_mode",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""{SHARED_PREAMBLE}

TELOS: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{context_block(ctx)}

OPERATORE: failure_mode (Constitutional AI, Bai et al. 2022).
COSA FARE: scegli un executor del catalog vivo che opera su risorse
sensibili (filesystem mutante, messaggi outbound, credenziali,
calendario, signatures). Identifica UN modo di fallimento NON ovvio
(silent failure, edge case, side-effect inatteso, race condition,
truncation, perdita di idempotenza). Proponi la DIFESA: layer
addizionale, default piu' sicuro, reverse pattern, hook di verifica.

DEVI: la difesa cambia COMPORTAMENTO dell'executor, non istruisce
l'utente. Anti-paternalismo (vedi REGOLA CRUCIALE sopra).
NON DEVI: proporre validazioni esagerate su path comuni
("verifica X prima di Y" su ogni chiamata) — la difesa deve essere
mirata al failure mode identificato, non blanket.

Output: target = executor da rafforzare;
proposed_action: "FAILURE: <modo> | DEFENSE: <meccanismo>"

{SHARED_NAMING_SCHEMA}

{SHARED_OUTPUT_FORMAT}
"""
