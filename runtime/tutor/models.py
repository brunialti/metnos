"""Small authority-neutral contracts used by the tutor boundary."""

from __future__ import annotations

from dataclasses import dataclass, field


AUDIENCES = frozenset({"user", "instance_admin"})


@dataclass(frozen=True, slots=True)
class TutorPrincipal:
    """Identity already authenticated by the channel boundary.

    The tutor never derives ``audience`` from the query, actor name or
    autonomy level.  Callers must map their trusted identity model explicitly.
    """

    user_id: str
    actor: str
    audience: str
    channel: str
    conversation_id: str = ""

    def __post_init__(self) -> None:
        if self.audience not in AUDIENCES:
            raise ValueError(f"unsupported tutor audience: {self.audience!r}")
        if not self.channel:
            raise ValueError("tutor principal requires a channel")


@dataclass(frozen=True, slots=True)
class TutorRequest:
    query_redacted: str
    lang: str
    principal: TutorPrincipal
    has_pending: bool = False
    catalog_version: str = ""
    probes: dict[str, object] = field(default_factory=dict)
    conversation_context: str = ""


@dataclass(frozen=True, slots=True)
class TutorAnswer:
    esito: str
    answer_md: str
    source_ids: tuple[str, ...] = ()
    card_ids: tuple[str, ...] = ()
    score_band: str = "high"
    elapsed_ms: int = 0
    detection: str = "pure_help"
    turn_id: str = ""
    # Correttore di bozze deterministico: 1 se la rilettura meccanica del
    # ledger ha imposto la singola ricomposizione; le voci che l'hanno chiesta.
    repair_pass: int = 0
    repair_missing: tuple[str, ...] = ()
