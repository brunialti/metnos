# SPDX-License-Identifier: AGPL-3.0-only
"""compression.py — super-verbo Schmidhuber (compression gain).

Schmidhuber (formal theory of creativity, 2010): un sistema impara
quando trova una compressione piu' breve dei suoi dati. Applicato a
Metnos: se N executor hanno args overlappanti e semantica vicina, si
puo' collassarli in UN super-verbo parametrizzato. Compression gain
= meno codice + meno carico cognitivo sul planner.

Es: `find_files`, `find_dirs`, `find_messages` hanno argomenti simili
(pattern, time_window, max_total). Super-verbo `find_<obj>` con
`object_kind` param. Compression gain misurabile (linee codice,
token planner).
"""
from __future__ import annotations

from ._base import LensCtx

NAME = "compression"
OPERATORS = ("super_verbo",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""Sei un agente Metnos in background che cerca COMPRESSION GAIN:
identifica 2-3 executor del catalog con args overlappanti e semantica
vicina, e proponi un SUPER-VERBO PARAMETRIZZATO che li sussume.

REGOLA §2.2: il super-verbo deve essere VOCAB-COMPLIANT — pattern
`<verb>_<object>[_<qualifier>]` con verb e object dai set chiusi.
NON inventare nuovi token.

REGOLA §2.1: il super-verbo resta vettoriale (input lista, output lista)
e single-purpose. Non e' un mega-executor che fa N operazioni —
e' UN executor che astrae N varianti dello stesso pattern.

TELOS DA SERVIRE: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

CONTESTO:
- Mnestoma (executor co-attivati e loro pattern): {ctx.mnestoma_summary}
- Pattern utente: {ctx.user_patterns_summary}

OPERATORE: super_verbo
COSA FARE: scegli 2-3 EXECUTOR DEL CATALOG VIVO con signature simile.
Proponi UN super-verbo che li sussume con un param di disambiguazione.

Genera 1-2 proposte di compressione. Ogni JSON:
  {{
    "executor_target": "<primo executor del cluster da cui parte la compressione>",
    "new_op_name": "<canonical del super-verbo, es. find_files#unified>",
    "proposed_action": "COMPRESS: <executor_A>, <executor_B>, [<executor_C>] → <super-verbo> con param <param_disambiguator>. Compression: N_args → M_args (delta tokens).",
    "rationale": "<gain attesa: meno codice, meno carico planner, 1 riga>"
  }}

Executor disponibili (campione, scegli da QUI):
{chr(10).join(f"  - {e['name']}: {e.get('description', '')[:100]}" for e in ctx.executors_sample)}

Rispondi SOLO array JSON.
"""
