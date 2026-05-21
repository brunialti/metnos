# SPDX-License-Identifier: AGPL-3.0-only
"""pattern_language.py — grammatica componibile alla Alexander.

Christopher Alexander (Pattern Language, 1977): una grammatica di
pattern in cui ogni pattern e' una soluzione tipica e componibile.
Applicato a Metnos: identifica MICRO-PATTERN ricorrenti nelle
pipeline turn_log, nominali e descrivili come "ricetta" che il
planner puo' riconoscere e applicare.

Es: pattern "ispeziona-poi-agisci" = find_<obj> → describe_entries →
[verb_azione]. Pattern "raccogli-aggrega-decidi" = find → filter →
compute → final_answer.

Differente da multi_tool memoization (memo deterministica di sequenze
esatte): qui sono PATTERN ASTRATTI, riusabili variando placeholder.
"""
from __future__ import annotations

from ._base import LensCtx

NAME = "pattern_language"
OPERATORS = ("micro_pattern",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""Sei un agente Metnos in background che IDENTIFICA MICRO-PATTERN
componibili nelle pipeline ricorrenti (alla Christopher Alexander).

Un pattern e' una "ricetta" astratta con placeholder, riusabile su
domini diversi. Es:
  Pattern "ispeziona-poi-agisci": find_<obj> → describe_entries → <action>
  Pattern "raccogli-aggrega-decidi": find → filter → compute → final_answer
  Pattern "verify-after-mutate": move/write/delete → compute_signatures

REGOLA: il pattern e' astratto (placeholder, non sequenze esatte).
Differente da `multi_tool_paths` (memo concreta di N step verbatim).

TELOS DA SERVIRE: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

CONTESTO:
- Mnestoma (sequenze osservate): {ctx.mnestoma_summary}
- Pattern utente: {ctx.user_patterns_summary}

OPERATORE: micro_pattern
COSA FARE: dai pattern del mnestoma astrai un MICRO-PATTERN
componibile non ancora codificato come capability esplicita. Dagli
un nome breve descrittivo (kebab-case) e descrivi la grammatica.

Genera 1-2 pattern. Ogni JSON:
  {{
    "executor_target": "<executor PIVOT del pattern, es. quello centrale>",
    "new_op_name": null,
    "proposed_action": "PATTERN <kebab-name>: <step1> → <step2> → <stepN>. Placeholder: <var1>, <var2>.",
    "rationale": "<dove ricorre nel mnestoma, frequenza, come serve il telos, 1 riga>"
  }}

Executor disponibili (campione):
{chr(10).join(f"  - {e['name']}" for e in ctx.executors_sample[:10])}

Rispondi SOLO array JSON.
"""
