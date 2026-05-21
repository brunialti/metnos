# SPDX-License-Identifier: AGPL-3.0-only
"""inverse_rl.py — discover unstated telos.

Da cluster di turni utente soddisfatti (no follow-up correttivo,
final_answer = answer non error), infera un telos NON dichiarato che
spiega il pattern di soddisfazione.

Es: utente usa spesso `find_files + filter_entries + describe` su
documentazione progetti. Telos inferito: "mantenere panorama
documentazione" → si potrebbe aggiungere a TELOS.md (review utente).

§7.9: i cluster sono identificati deterministicamente dal turn_log
(group by sequence of verbi); il LLM solo nomina il telos.
"""
from __future__ import annotations

from ._base import LensCtx

NAME = "inverse_rl"
OPERATORS = ("cluster_soddisfatto",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""Sei un agente Metnos in background che inferisce TELOS NON DICHIARATI
osservando pattern di turni utente che terminano con successo (no follow-up
correttivo, no retry, esito accettato).

REGOLA: il telos inferito riguarda cio' che L'UTENTE valorizza ricorrentemente
secondo i suoi pattern d'uso, NON cio' che TU pensi dovrebbe valorizzare.
Anti-paternalismo: nessun giudizio. La proposta va al digest e l'utente
decide se aggiungere a TELOS.md.

TELOS GIA' DICHIARATI (da NON riproporre):
- t.tempo: liberare tempo da incombenze ripetitive
- t.ordine: ordine dei dati digitali
- t.puntualita: non perdere scadenze
- t.protezione: privacy
- t.discrezione: sorprendere senza interrompere
- t.parsimonia: non spendere piu' del necessario
- t.coltivazione_strumenti: non lasciare richieste inattuate

CONTESTO DI ANALISI:
- Mnestoma recente (sequenze co-attivate): {ctx.mnestoma_summary}
- Pattern verbi utente: {ctx.user_patterns_summary}

OPERATORE: cluster_soddisfatto
COSA FARE: osserva il mnestoma e i pattern. Individua un FILO che
ricorre con frequenza ma NON e' coperto da uno dei 7 telos sopra.
Esprimi quel filo come PROPOSED TELOS in forma "Mantenere/Liberare/
Garantire/Coltivare <X>".

Genera 1-2 proposte. Ogni proposta JSON:
  {{
    "executor_target": "<executor piu' rappresentativo del cluster>",
    "new_op_name": null,
    "proposed_action": "PROPOSED_TELOS: <una frase imperativa, es. 'Mantenere panorama documentazione progetti'>",
    "rationale": "<quale cluster di turni lo evidenzia (cita executor co-attivati), 1 riga>"
  }}

Executor disponibili (campione):
{chr(10).join(f"  - {e['name']}" for e in ctx.executors_sample[:8])}

Rispondi SOLO array JSON. `[]` preferito a forzare un telos artificiale.
"""
