# SPDX-License-Identifier: AGPL-3.0-only
"""oulipo.py — lente vincolo deliberato (OuLiPo).

Propone vincoli a tempo (settimana/giorno) che obbligano Metnos a essere
piu' ingegnoso con meno risorse. Tipici: "no tier=wise per N gg", "no
network outbound per giorno", "no usage di executor X per N gg".

L'effetto e' di esplorare path alternativi che oggi non vengono scelti
perche' c'e' la via piu' facile. Serve in particolare t.parsimonia
(non spendere) e t.coltivazione_strumenti (cresce capacita' locali).
"""
from __future__ import annotations

from ._base import LensCtx

NAME = "oulipo"
OPERATORS = ("vincolo_risorsa", "vincolo_tempo", "vincolo_executor")

_DESCRIPTIONS = {
    "vincolo_risorsa": "Proponi un VINCOLO DI RISORSA temporaneo (no API frontier per N gg, no rete, no provider Google) che esercita Metnos a trovare path alternativi locali.",
    "vincolo_tempo": "Proponi un VINCOLO TEMPORALE (es. nessun executor mutante prima delle 09:00, nessun batch oltre la finestra notturna) che modula i ritmi di Metnos.",
    "vincolo_executor": "Proponi un VINCOLO DI ESECUTORE (es. no consult_frontier per una settimana, no executor X) che obbliga Metnos a scegliere alternative.",
}


def build_prompt(ctx: LensCtx, operator: str) -> str:
    op_desc = _DESCRIPTIONS[operator]
    return f"""Sei un agente Metnos in background che propone vincoli deliberati
(OuLiPo: Ouvroir de Litterature Potentielle, vincoli che liberano creativita').

REGOLA CRUCIALE: i vincoli vincolano METNOS, mai l'utente. NIENTE
"impedisci all'utente X" — il vincolo riguarda solo cosa Metnos fa
da solo in autonomia (proposals, scheduler, fallback chain).

TELOS DA SERVIRE: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

CONTESTO:
- Mnestoma recente: {ctx.mnestoma_summary}
- Pattern utente: {ctx.user_patterns_summary}

OPERATORE OULIPO: {operator}
COSA FARE: {op_desc}

Genera 1-2 proposte concrete di vincolo. Ogni proposta JSON:
  {{
    "executor_target": "<executor del catalog vivo a cui il vincolo si applica, o quello rappresentativo>",
    "new_op_name": null,
    "proposed_action": "<descrizione del vincolo: cosa, per quanto, su quale scope>",
    "rationale": "<come libera creativita' Metnos per servire il telos, 1 riga>"
  }}

Executor disponibili (campione):
{chr(10).join(f"  - {e['name']}" for e in ctx.executors_sample[:8])}

Rispondi SOLO array JSON. `[]` se nessun vincolo sensato.
"""
