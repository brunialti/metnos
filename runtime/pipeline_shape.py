# SPDX-License-Identifier: AGPL-3.0-only
"""pipeline_shape.py — invariante di forma del pipeline (FSM deterministico).

Regola universale di Metnos: la sequenza degli step di un turno matcha
la regex

    E+ (F | A)?

seguita opzionalmente da `final_answer` (sempre lecito come terminatore).

Categorie di output-role, derivate da vocab:

    E  produces-out    read/find/list/get + filter/sort/group/classify/
                       compute/compare/extract. Emette entries riusabili.
    F  formatter-out   describe, render. Terminale, output user-facing.
    A  action-out      move, delete, send, share, write, set, create,
                       change, order, compress. Terminale, output = metadata
                       di esito che final_answer aggrega.

System pseudo-verbi (final_answer, undo_last_turn, request_new_executor,
admin) bypassano la FSM: sono meta-operazioni del runtime, non parte del
data-flow canonico.

Stato a 3 valori: START -> CHAIN -> TERMINAL.

Errori canonici su transizioni illegali:

    needs_data_source        consumer/formatter senza sorgente
                             -> cascade auto (object->primary->find_urls
                                ->consult_frontier)
    needs_action_target      azione senza target
                             -> dialog get_inputs (mai cascade esterna)
    pipeline_already_closed  step dopo terminatore F/A
                             -> skip + final_answer immediato

Source-presence detection nei raw_args:

    from_step: int>=1  -> sorgente upstream presente
    any list arg !=[]  -> literal source (equivalente a producer implicito)

§7.9 deterministico: nessun LLM nella decisione, nessun keyword matching.
"""
from __future__ import annotations

from vocab import ACTIONS, PRODUCER_VERBS

# Output-role: verbi che producono PRESENTAZIONE (terminale).
FORMATTER_OUT_VERBS = frozenset({"describe", "render"})

# Output-role: verbi che producono MUTAZIONE (terminale).
ACTION_OUT_VERBS = frozenset({
    "move", "delete", "send", "share", "write",
    "set", "create", "change", "order", "compress",
})

# Output-role E (produces-out) = ACTIONS - FORMATTER_OUT - ACTION_OUT.
# Include PRODUCER_VERBS (read/find/list/get) e i transformer (filter,
# sort, group, classify, compute, compare, extract).

# System pseudo-executors: bypass FSM, meta-operazioni del runtime.
SYSTEM_PSEUDO = frozenset({
    "final_answer",
    "undo_last_turn",
    "request_new_executor",
    "admin",
    "request_disambiguation_from_user",
})


def verb_of(name: str) -> str:
    """Estrae il verbo canonico dal name dell'executor."""
    return name.split("_", 1)[0] if name else ""


def category(name: str) -> str:
    """Output-role: 'E' | 'F' | 'A' | '' (sconosciuto / system verb)."""
    v = verb_of(name)
    if v in FORMATTER_OUT_VERBS:
        return "F"
    if v in ACTION_OUT_VERBS:
        return "A"
    if v in ACTIONS:
        return "E"
    return ""


def has_literal_source(raw_args: dict | None) -> bool:
    """True se i raw_args contengono sorgente esplicita.

    Determinismo: nessun heuristic sul nome dell'arg, solo type+truthiness.
    - from_step: int>=1 -> sorgente upstream
    - any list arg non vuoto -> literal source (paths, urls, ids, entries, ...)
    """
    if not isinstance(raw_args, dict):
        return False
    fs = raw_args.get("from_step")
    if isinstance(fs, int) and fs >= 1:
        return True
    if isinstance(fs, str) and fs.isdigit() and int(fs) >= 1:
        return True
    for k, v in raw_args.items():
        if k == "from_step":
            continue
        if isinstance(v, list) and v:
            return True
    return False


def compute_state(history) -> str:
    """Ricalcola lo stato FSM dalla sequenza degli step gia' eseguiti.

    `history` puo' essere una list[StepLog] (con attributi chosen_tool e
    raw_args) o una list[dict] (con chiavi 'tool'/'chosen_tool' e
    'args'/'raw_args'). Saltati gli step senza chosen_tool (step vuoti
    o virtuali). Determinismo: nessun side-effect, pura funzione.
    """
    state = "START"
    for s in history or ():
        if hasattr(s, "chosen_tool"):
            tool = getattr(s, "chosen_tool", "") or ""
            args = getattr(s, "raw_args", {}) or {}
        elif isinstance(s, dict):
            tool = s.get("chosen_tool") or s.get("tool") or ""
            args = s.get("raw_args") or s.get("args") or {}
        else:
            continue
        if not tool:
            continue
        state, _ = next_state(state, tool, args)
    return state


def next_state(state: str, name: str,
                raw_args: dict | None = None
                ) -> tuple[str, str | None]:
    """Transizione FSM. Ritorna (new_state, error_class | None).

    Stati: START, CHAIN, TERMINAL.
    Errori: needs_data_source | needs_action_target | pipeline_already_closed.
    """
    # final_answer: terminatore universale, lecito da qualunque stato.
    if name == "final_answer":
        return "TERMINAL", None
    # System pseudo-verbi: non transizionano (meta-op del runtime).
    if name in SYSTEM_PSEUDO:
        return state, None
    cat = category(name)
    if not cat:
        # Verbo sconosciuto (skill custom, executor sintetizzato con
        # verbo nuovo non ancora classificato): non interferire.
        return state, None

    if state == "START":
        if cat == "E":
            return "CHAIN", None
        # F o A all'inizio: serve sorgente upstream OR literal in args.
        if has_literal_source(raw_args):
            # Literal e' implicito producer -> chiude direttamente.
            return "TERMINAL", None
        return "ERROR", (
            "needs_action_target" if cat == "A" else "needs_data_source"
        )

    if state == "CHAIN":
        if cat == "E":
            return "CHAIN", None
        # F o A in chain: chiude.
        return "TERMINAL", None

    # TERMINAL: nessuno step dovrebbe seguire.
    return "ERROR", "pipeline_already_closed"
