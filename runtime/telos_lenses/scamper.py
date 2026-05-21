# SPDX-License-Identifier: AGPL-3.0-only
"""scamper.py — lente SCAMPER (Eberle 1971, Osborn 1953).

Sette operatori di brainstorming applicati ai top-N executor del catalog:
  S - Substitute        sorgente diversa per stesso verbo
  C - Combine           due executor in pipeline non ancora vista
  A - Adapt             stesso executor adattato a contesto utente
  M - Modify            executor con parametri default diversi
  P - Put to other use  executor in contesto non canonico
  E - Eliminate         executor sostituito da scorciatoia
  R - Reverse           verbo inverso (write -> read; find -> ignore)

§7.9: la lente USA un LLM per generare (creativita' richiesta), ma il
GATING anti-paternalismo a valle e' deterministico.
"""
from __future__ import annotations

from ._base import LensCtx

NAME = "scamper"
OPERATORS = ("S", "C", "A", "M", "P", "E", "R")

_OP_NAMES = {
    "S": "Substitute",
    "C": "Combine",
    "A": "Adapt",
    "M": "Modify",
    "P": "Put_to_other_use",
    "E": "Eliminate",
    "R": "Reverse",
}
_OP_DESCRIPTIONS = {
    "S": "SOSTITUISCI una componente di un executor con un'alternativa funzionalmente analoga (es. sorgente, formato, scope)",
    "C": "COMBINA due executor in una pipeline che l'utente NON ha mai usato (cross-domain o cross-corpus)",
    "A": "ADATTA un executor a un contesto utente non standard (multi-account, multi-lingua, multi-canale)",
    "M": "MODIFICA i parametri di default di un executor per allinearli al pattern d'uso reale dell'utente",
    "P": "USA un executor in un contesto non canonico (es. compute_files_loc su README per stats progetti)",
    "E": "ELIMINA uno step ridondante / un parametro inutilizzato / un cap che non serve mai",
    "R": "INVERTI il verbo: se write fa X, prova read di X (es. log inverse, undo memoizzato, dry-run)",
}

_PREAMBLE_VINCOLI = """VINCOLI ARCHITETTURALI (proposte che li violano vengono scartate):
1. NON FONDERE due executor in uno. Ogni executor fa una sola operazione
   (vettoriale, batch-by-default). Le pipeline si compongono nel planner
   via from_step, non nel codice executor.
2. NON proporre default IMPLICITI che inferiscono argomenti dal contesto:
   gli executor sono deterministici al confine NL→codice.
3. NON ACCOPPIARE domini ortogonali: filter_entries non deve auto-fetch
   metadata del dominio file.
4. NON suggerire approvazione batch silenziosa o auto-conferma.
5. NON RIMUOVERE il supporto a input plurali con N=1.

COSA METNOS GIA' FA (NON re-inventare):
- Piping fra executor via `from_step: N` nel planner ReAct.
- Undo del turno corrente via `undo_last_turn`.
- Fast-path deterministico per query triviali (zero LLM).
- Memoization sequenze multi-tool (uses>=3) + promozione synth (uses>=50).
- Output formatter channel-agnostic (markdown), no LLM nel render.
- Dialog `needs_inputs` per parametri mancanti.
"""


def build_prompt(ctx: LensCtx, operator: str) -> str:
    op_name = _OP_NAMES[operator]
    op_desc = _OP_DESCRIPTIONS[operator]
    return f"""Sei un agente Metnos che genera proposte creative per servire un fine utente.
Operi in background: NON parli con l'utente, scrivi proposte per il Vaglio.

REGOLA CRUCIALE: le tue proposte cambiano cio' che fa METNOS, mai cio'
che fa l'utente. NIENTE "dire all'utente di X" o "impedire all'utente di Y".

{_PREAMBLE_VINCOLI}

TELOS DA SERVIRE:
  {ctx.telos.phrase}
  Note utente: {ctx.telos.notes}

CONTESTO:
- Mnestoma recente (executor co-attivati, catalog vivo):
{ctx.mnestoma_summary}

- Pattern d'uso utente (turn_log 30gg):
{ctx.user_patterns_summary}

- Executor disponibili (campione vivo):
{chr(10).join(f"  - {e['name']}: {e.get('description', '')[:120]}" for e in ctx.executors_sample)}

OPERATORE SCAMPER: {operator} = {op_name}
COSA FARE: {op_desc}

Genera 1-3 proposte concrete che applicano l'operatore {operator} a uno
degli executor del catalog VIVO sopra. Ogni proposta JSON:
  {{
    "executor_target": "<name esatto>",
    "new_op_name": "<canonical>" | "<canonical#kebab-descriptor>" | null,
    "proposed_action": "<descrizione 1-2 righe>",
    "rationale": "<perche' avvicina al telos, 1 riga>"
  }}

`new_op_name`: snake_case `<verb>_<object>[_<qualifier>]` (vocab §2.2);
descriptor 4-livello opzionale dopo `#` in kebab-case `[a-z0-9]+(-[a-z0-9]+)*`.
Esempi: `compute_files_loc#per-extension`, `find_dirs_empty`, null.

Rispondi SOLO array JSON 1-3 oggetti. `[]` preferito a proposta debole.
"""
