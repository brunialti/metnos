# SPDX-License-Identifier: AGPL-3.0-only
"""counterfactual.py — analisi controfattuale di turni insoddisfacenti.

Ref: Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement
Learning" (NeurIPS 2023). Idea: ispezionare traiettorie fallite o
inefficienti e produrre proposte verbali di correzione che guidino
iterazioni future.

In Metnos lens speculare a `inverse_rl`:
  inverse_rl   → telos non dichiarati estratti da turni SODDISFATTI
  counterfactual → executor/pipeline mancanti estratti da turni INSODDISFATTI
                   (lunghi, con retry, error_class non-zero, follow-up correttivo)

Lens naming-aware: il counterfattuale puo' proporre new_op_name per
indicare un executor mancante che avrebbe risolto il caso.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_SCHEMA,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "counterfactual"
OPERATORS = ("turno_problematico",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""{SHARED_PREAMBLE}

TELOS: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{context_block(ctx)}

OPERATORE: turno_problematico (Reflexion).
COSA FARE: osserva il mnestoma e i pattern. Individua un PATTERN DI
TURNO INSODDISFATTO ricorrente (lunghezza eccessiva, retry, sequenza
con error_class, follow-up correttivo). Proponi cosa MANCAVA — un
executor o una variante 4-livello — che avrebbe ridotto il turno a
1-2 step puliti.

DEVI: descrivere il CONTROFATTUALE ("se fosse esistito X, il turno
da N step si sarebbe risolto in M step").
NON DEVI: proporre una FEATURE generica. La proposta deve nascere
da un pattern di FALLIMENTO osservato.

Output: target = executor protagonista del turno problematico;
proposed_action: "CONTROFATTUALE: pattern=<descrizione> | turno
attuale=<N step> | con <new_op_name>=<M step> | risparmio=<delta>"

{SHARED_NAMING_SCHEMA}

{SHARED_OUTPUT_FORMAT}
"""
