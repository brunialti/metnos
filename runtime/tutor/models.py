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
    # Monotonic process-local deadline. It is never persisted or rendered.
    deadline_at: float = field(default=0.0, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class TutorEvidence:
    """Internal, privacy-minimized evidence retained only for F4 feedback.

    It is never rendered or written to TurnLog.  The F4 ledger persists the
    normalized vector for a short TTL and never the clear-text query.
    """

    query_vector: tuple[float, ...]
    embedding_fingerprint: str
    catalog_version: str
    primary_source_id: str = ""
    primary_content_hash: str = ""
    eligible_for_association: bool = False
    # Hashes of owner-scoped learned rows that materially changed retrieval
    # for this answer.  No clear query text crosses the evidence boundary.
    association_contributor_hashes: tuple[str, ...] = ()


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
    repair_remaining: tuple[str, ...] = ()
    # F3: literal user-authored action clause.  Only the trusted channel
    # boundary may turn it into a one-time pending handoff.
    handoff_query: str = ""
    handoff_created: bool = False
    pending_dialog_id: str = ""
    probe_statuses: tuple[tuple[str, str], ...] = ()
    # F4: closed gap taxonomy; empty means no gap was observed.
    gap_reason: str = ""
    evidence: TutorEvidence | None = field(
        default=None, repr=False, compare=False)
