# SPDX-License-Identifier: AGPL-3.0-only
"""analogy_transfer.py — trasferimento strutturale di strategie.

Identifica una strategia di successo in UN dominio (uses elevato nel
mnestoma, accept rate alto in proposals) e la trasferisce a un dominio
STRUTTURALMENTE SIMILE dove non e' ancora applicata.

Es: "credentials hanno Fernet+HKDF lifecycle (rotate, expire, revoke)"
→ trasferimento → "API tokens per skill third-party hanno stesso
lifecycle? Aggiungiamo lo stesso pattern".
"""
from __future__ import annotations

from ._base import LensCtx

NAME = "analogy_transfer"
OPERATORS = ("transfer_struttura",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""Sei un agente Metnos in background che TRASFERISCE strategie
note tra domini strutturalmente simili.

Analogia: dominio A risolve un problema X con una strategia S.
Dominio B ha un problema isomorfo a X. Trasferire S da A a B.

Es: "git-style history (commit, blob, sha) e' usato in `signatures`
per integrita' → trasferimento a `proposals` (proposal id = sha del
contenuto, history dei verdict)".

REGOLA: le strategie trasferite cambiano cosa fa Metnos sui propri
domini, non cosa fa l'utente.

TELOS DA SERVIRE: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

CONTESTO:
- Mnestoma (dominio A da cui imparare): {ctx.mnestoma_summary}
- Pattern utente: {ctx.user_patterns_summary}

OPERATORE: transfer_struttura
COSA FARE: identifica una STRATEGIA gia' applicata in un dominio
Metnos (es. credentials Fernet, signatures hash chain, scheduler v2
trigger grammar) e TRASFERISCILA a un altro dominio dove
strutturalmente si applica.

Genera 1-3 trasferimenti. Ogni JSON:
  {{
    "executor_target": "<executor del dominio B (destinazione del transfer)>",
    "new_op_name": "<verb_object[_qualifier[_descriptor-kebab]]>" o null (descriptor RICHIEDE qualifier),
    "proposed_action": "ANALOGIA: <dominio A>:<strategia S> → <dominio B>:<applicazione>",
    "rationale": "<perche' l'isomorfismo regge, 1 riga>"
  }}

Executor disponibili (campione):
{chr(10).join(f"  - {e['name']}: {e.get('description', '')[:100]}" for e in ctx.executors_sample)}

Rispondi SOLO array JSON.
"""
