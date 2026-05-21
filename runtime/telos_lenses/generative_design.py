# SPDX-License-Identifier: AGPL-3.0-only
"""generative_design.py — Pareto candidates per brief composto.

Generative design (Bentley, Autodesk): per UN brief utente composto da
piu' vincoli/obiettivi, genera N candidati e mostra i trade-off
Pareto-ottimali invece di scegliere uno.

Es: "design_my_morning" — brief composto (tempo, ordine, puntualita).
Candidati: (a) sveglia 06:30 + workout 30min + colazione → tempo alto,
ordine medio; (b) sveglia 07:00 + workout 0min + colazione lunga →
tempo medio, ordine alto; (c) ... — l'utente sceglie esplicitamente.
"""
from __future__ import annotations

from ._base import LensCtx

NAME = "generative_design"
OPERATORS = ("pareto_brief",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""Sei un agente Metnos in background che propone candidati
Pareto-ottimali per brief composti (generative design).

Differente dalle altre lenti: invece di UNA proposta, generi 2-3
candidati con TRADE-OFF ESPLICITI. L'utente sceglie esplicitamente
nel digest serale; Metnos non decide per lui.

REGOLA: il brief riguarda un'attivita' di Metnos (es. "come Metnos
gestisce le notifiche mattutine"), non un'attivita' dell'utente
("come l'utente dovrebbe fare colazione"). Anti-paternalismo.

TELOS DA SERVIRE: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

ALTRI TELOS NEL REGISTRO (per Pareto trade-off):
- t.tempo: efficienza
- t.ordine: stabilita' strutturale
- t.puntualita: rispetto delle deadline
- t.protezione: privacy
- t.discrezione: minimo rumore
- t.parsimonia: minimo costo
- t.coltivazione_strumenti: capacita' locale

CONTESTO:
- Mnestoma: {ctx.mnestoma_summary}
- Pattern utente: {ctx.user_patterns_summary}

OPERATORE: pareto_brief
COSA FARE: scegli un BRIEF COMPOSTO che riguarda comportamento Metnos
(esempi: "design dello scheduler notturno", "design del digest
proposte serali", "design dei reminder per scadenze"). Genera
2-3 CANDIDATI Pareto-ottimali con trade-off esplicito su 2-3 telos
contrapposti.

Genera 2-3 proposte (UN candidate per oggetto). Ogni proposta JSON:
  {{
    "executor_target": "<executor centrale del candidato>",
    "new_op_name": "<verb_object[_qualifier[_descriptor-kebab]]>" o null (descriptor RICHIEDE qualifier),
    "proposed_action": "CANDIDATO <X>: <descrizione> | trade-off: ↑t.<telosA> ↓t.<telosB>",
    "rationale": "<quale telos serve meglio, 1 riga>"
  }}

Executor disponibili (campione):
{chr(10).join(f"  - {e['name']}" for e in ctx.executors_sample[:8])}

Rispondi SOLO array JSON con 2-3 candidati. `[]` se brief non
rappresentabile come Pareto.
"""
