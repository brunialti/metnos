# SPDX-License-Identifier: AGPL-3.0-only
"""endgame_book.py — precompute pattern di scadenze.

Per t.puntualita serve una "tabella di endgame": pattern noti del tipo
"fattura→scadenza+30gg→reminder-7gg", "appuntamento medico→reminder
24h", "scadenza fiscale→reminder-3gg+24h". Una volta che l'utente
conferma il pattern, lo scheduler v2 li applica automaticamente.

Concetto da scacchi: in endgame le posizioni sono studiate offline
(tablebases). In Metnos i pattern scadenza sono precomputed offline
e dispatch e' deterministico (zero LLM al request-time).
"""
from __future__ import annotations

from ._base import LensCtx

NAME = "endgame_book"
OPERATORS = ("pattern_scadenza",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""Sei un agente Metnos in background che PRECOMPUTA pattern
di scadenze (endgame book) per t.puntualita e altri telos.

Un pattern: dato un INPUT (entry di calendario, file con metadata,
messaggio con data citata), descrive la CASCATA di reminder/azioni
deterministiche (no LLM) da agganciare. Es:
  - Input: file `*.fattura.pdf` con scadenza nel nome
  - Pattern: reminder a -7gg + reminder -24h + flag visivo @scadenza

REGOLA: il pattern modifica scheduler/automazione di METNOS, non
l'utente. Niente "ricorda all'utente" coercitivo: i reminder vanno
nello stream proposte/notifiche dell'utente come ogni altro.

TELOS DA SERVIRE: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

CONTESTO:
- Mnestoma: {ctx.mnestoma_summary}
- Pattern utente: {ctx.user_patterns_summary}

OPERATORE: pattern_scadenza
COSA FARE: identifica un PATTERN-CON-DEADLINE comune (fatture, scadenze
fiscali, certificati, appuntamenti medici, scadenze contrattuali) e
descrivi la cascata di azioni Metnos.

Genera 1-2 pattern. Ogni pattern JSON:
  {{
    "executor_target": "<executor che intercetta input es. find_files o read_messages>",
    "new_op_name": "<canonical opzionale es. set_tasks#deadline-cascade>" o null,
    "proposed_action": "PATTERN: INPUT=<tipo> | CASCATA: <azione@T0>, <azione@T-Ngg>, <azione@T-24h>",
    "rationale": "<come serve t.puntualita o telos correlato, 1 riga>"
  }}

Executor disponibili (campione):
{chr(10).join(f"  - {e['name']}" for e in ctx.executors_sample[:8])}

Rispondi SOLO array JSON.
"""
