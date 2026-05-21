# SPDX-License-Identifier: AGPL-3.0-only
"""boden_transformational.py — revisione del contratto di un executor.

Boden (1990) "Mechanisms of Creativity": creativita' trasformazionale
= cambiare lo spazio di possibilita', non solo combinare al suo interno.
In Metnos: cambiare il CONTRATTO (firma args, schema, semantica
ritorno) di un executor invece di adattare i suoi parametri (M di
SCAMPER lavora sui default).

Es: oggi `move_files(paths, dst)` accetta UN dst. Trasformazione:
firma `move_files(paths, dst_template)` dove `dst_template` accetta
placeholder dinamici `{date}/{ext}/{size_bucket}` → un singolo move
ridistribuisce un corpus.

Differente da SCAMPER M (cambia default) e SCAMPER A (adatta a
contesto): qui cambiamo la SIGNATURE dell'executor.
"""
from __future__ import annotations

from ._base import LensCtx

NAME = "boden_transformational"
OPERATORS = ("nuovo_contratto",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""Sei un agente Metnos in background che propone TRASFORMAZIONI
DEL CONTRATTO (firma args, schema, semantica ritorno) di un executor
esistente. Non default, non parametri — CAMBIO DI FORMA.

Differente da SCAMPER:
- M (Modify) modifica default di un arg esistente.
- A (Adapt) adatta a contesto utente (multi-account, multi-canale).
- BODEN trasforma la signature stessa: nuovi args, nuovo schema, nuovo
  significato del ritorno.

REGOLA: cambiare contratto rompe backward compat (§7.1 OK in dev pre-1.0).
Ma cambiare contratto NON cambia il VOCABOLARIO canonical §2.2: il
nome dell'executor resta lo stesso (o eventualmente un canonical
nuovo con descriptor #v2 per indicare la variante).

TELOS DA SERVIRE: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

CONTESTO:
- Mnestoma: {ctx.mnestoma_summary}
- Pattern utente: {ctx.user_patterns_summary}

OPERATORE: nuovo_contratto
COSA FARE: scegli UN executor del catalog vivo. Proponi una
TRASFORMAZIONE del contratto: nuovi args, schema ridisegnato,
ritorno arricchito. Specifica l'incremento di potenza espressiva.

Genera 1-2 trasformazioni. Ogni JSON:
  {{
    "executor_target": "<executor del catalog vivo>",
    "new_op_name": "<canonical>" oppure "<canonical#v2>" o null,
    "proposed_action": "CONTRATTO ATTUALE: <descrizione args/schema> | NUOVO: <descrizione> | DELTA: <potenza espressiva guadagnata>",
    "rationale": "<come serve il telos, 1 riga>"
  }}

Executor disponibili (campione):
{chr(10).join(f"  - {e['name']}: {e.get('description', '')[:100]}" for e in ctx.executors_sample)}

Rispondi SOLO array JSON.
"""
