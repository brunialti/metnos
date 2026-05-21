# SPDX-License-Identifier: AGPL-3.0-only
"""oulipo.py — vincolo deliberato.

Ref: OuLiPo (Ouvroir de Litterature Potentielle), Queneau & Le Lionnais,
1960. Idea: vincoli formali deliberati liberano creativita' (es.
lipogrammi, palindromi). In Metnos: Metnos auto-impone restrizioni
temporanee su risorse/operazioni per esplorare path alternativi che
altrimenti non sceglierebbe.

Concept-only lens: il vincolo NON e' un new_op_name (e' una politica
applicata al runtime/scheduler), quindi new_op_name resta null.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_NULL,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "oulipo"
OPERATORS = ("vincolo_risorsa", "vincolo_tempo", "vincolo_executor")

_DESCRIPTIONS = {
    "vincolo_risorsa": "VINCOLO DI RISORSA: Metnos rinuncia temporaneamente a una risorsa esterna (API frontier, rete, provider cloud).",
    "vincolo_tempo": "VINCOLO TEMPORALE: Metnos limita le proprie azioni a una finestra (no mutating prima delle 09:00, batch solo notte).",
    "vincolo_executor": "VINCOLO DI ESECUTORE: Metnos non usa un executor specifico per N giorni/N operazioni.",
}


def build_prompt(ctx: LensCtx, operator: str) -> str:
    op_desc = _DESCRIPTIONS[operator]
    return f"""{SHARED_PREAMBLE}

TELOS: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{context_block(ctx)}

OPERATORE OULIPO: {operator}.
{op_desc}

REGOLA STRETTA: il vincolo e' UNA RESTRIZIONE auto-imposta da Metnos
per un tempo definito. NON e' una feature da implementare. NON e' un
problema da risolvere. Il vincolo CHIUDE una porta deliberatamente.

DEVI: usare il formato fisso "Per le prossime <N> <giorni|ore|operazioni>,
Metnos NON usera' <X>. Effetto previsto: <conseguenza concreta>."
NON DEVI: proporre l'implementazione di una capability, un fallback,
un meccanismo, un modulo, un'integrazione. Tutti quelli sono FEATURE,
non vincoli.

OK pattern: "Per i prossimi 7 giorni, Metnos NON usera' consult_frontier.
Effetto previsto: ricerca e ragionamento solo su catalog/mnestoma locale,
azzeramento costi API in quella finestra."

OK pattern: "Per le prossime 50 operazioni, Metnos NON usera' move_files
mutante. Effetto previsto: forza l'uso di find/filter/describe pipelines
prima di azioni distruttive."

ERRORE pattern: "Implementazione di un fallback lightweight per task
di reasoning." (questa e' una FEATURE, non un vincolo).

ERRORE pattern: "Introduzione di un modulo di triage automatico."
(questa e' una FEATURE).

Output: target = executor toccato dal vincolo (quello che NON si usera').
proposed_action: il vincolo nel formato OK sopra.

{SHARED_NAMING_NULL}

{SHARED_OUTPUT_FORMAT}
"""
