"""runtime.channels.approval — rendering della "carta a 3 righe" di approval.

Pattern dal dialog manager (vedi `project_dialog_manager_authorization_ux`):
3 righe canoniche, modulazione per ricorrenza, segno visivo per concessioni
di territorio (livello di sandbox a cui si concede latitudine).

    Riga 1:  domanda imperativa breve  (Vuoi che scarichi?)
    Riga 2:  oggetto + sorgente -> destinazione (~peso)
    Riga 3:  classificazione meta: reversibile/irreversibile, classe capability

Niente dispatching dei callback in v1.1 (Vaglio e' stub always-approve, quindi
gli approval reali non emergono ancora). Quando il Vaglio passa a reale,
l'orchestrazione qui dentro viene completata con un dispatcher di callback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from . import OutboundMessage


Reversibility = Literal["reversible", "irreversible", "partial"]
TerritoryConcession = Literal["none", "session", "permanent"]


@dataclass(frozen=True)
class ApprovalRequest:
    """Dati per generare la carta di approval. Il chiamante (Vaglio/Policy)
    riempie questi campi; render_approval_card produce l'OutboundMessage."""
    action_verb: str        # "scarichi", "scriva", "invii", ...
    target_summary: str     # "rapporto_marzo.pdf da drive.google.com -> ~/downloads/ (~2.4 MB)"
    capability_class: str   # "write_files:~/downloads/**"
    reversibility: Reversibility = "reversible"
    territory_concession: TerritoryConcession = "none"
    recurrence_count: int = 0  # quante volte e' gia' stata posta una richiesta simile
    token: str = ""         # opaco; finira' nei callback_data dei bottoni
    extra: dict = field(default_factory=dict)


# Chiavi del catalogo: il renderer non possiede prosa in una lingua concreta.
_TERRITORY_KEY = {
    "none": None,
    "session": "MSG_APPROVAL_TERRITORY_SESSION",
    "permanent": "MSG_APPROVAL_TERRITORY_PERMANENT",
}
_REVERSIBILITY_KEY = {
    "reversible": "MSG_REVERSIBILITY_REVERSIBLE",
    "irreversible": "MSG_REVERSIBILITY_IRREVERSIBLE",
    "partial": "MSG_REVERSIBILITY_PARTIAL",
}

# Modulazione per ricorrenza: dopo N volte, la carta si accorcia (Roberto sa cosa sta approvando)
_RECURRENCE_THRESHOLDS = (3, 8)  # < 3: full | 3..7: medium | >= 8: short


def _shape_for_recurrence(n: int) -> Literal["full", "medium", "short"]:
    if n >= _RECURRENCE_THRESHOLDS[1]:
        return "short"
    if n >= _RECURRENCE_THRESHOLDS[0]:
        return "medium"
    return "full"


def render_approval_card(req: ApprovalRequest) -> OutboundMessage:
    """Produce un OutboundMessage con la carta a 3 righe + due bottoni."""
    from messages import get as _msg

    shape = _shape_for_recurrence(req.recurrence_count)
    territory_key = _TERRITORY_KEY[req.territory_concession]
    territory = _msg(territory_key) if territory_key else ""
    reversibility = _msg(_REVERSIBILITY_KEY[req.reversibility])
    question = _msg("MSG_APPROVAL_QUESTION", action=req.action_verb)
    metadata = _msg(
        "MSG_APPROVAL_METADATA",
        reversibility=reversibility,
        capability_class=req.capability_class,
        territory=territory,
    )

    if shape == "full":
        text = f"{question}\n{req.target_summary}\n{metadata}"
    elif shape == "medium":
        text = f"{question} ({metadata})\n{req.target_summary}"
    else:  # short
        text = f"{question} {req.target_summary} [{metadata}]"

    buttons = [[
        {"text": _msg("MSG_BTN_APPROVE"), "data": f"approve:{req.token}"},
        {"text": _msg("MSG_BTN_REJECT"), "data": f"reject:{req.token}"},
    ]]
    return OutboundMessage(text=text, buttons=buttons)
