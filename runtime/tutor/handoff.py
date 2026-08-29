"""F3 mixed-request segmentation and one-time pending creation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
import secrets
import time
import uuid

from messages import get as _msg
from timefmt import now_iso_offset

from .models import TutorAnswer, TutorPrincipal


class MixedSplitUnavailable(RuntimeError):
    """The semantic sub-classifier failed; the user was not ambiguous."""


@dataclass(frozen=True, slots=True)
class MixedSplit:
    """Literal MIXED clauses plus the exact EXPLAIN-clause decision."""

    explanation: str
    action: str
    explanation_decision: object


def split_mixed_query(query: str, lang: str, *, conversation_context: str = "",
                      deadline_at: float = 0.0) -> MixedSplit | None:
    """Return one literal MIXED split with clause-local semantic state.

    Segmentation comes from the engine's canonical compound splitter and each
    segment is classified by the same closed Tutor mode gate.  We accept one
    explanatory segment and one operational segment (read-only observation or
    state-changing action); no generated rewrite or guessed remainder is
    admitted.
    """

    from compound_decomposer import split_query_chunks
    from published_docs import resolve_reference
    from .mode import classify_mode_decision

    chunks = tuple(part.strip() for part in split_query_chunks(query)
                   if str(part).strip())
    if len(chunks) != 2:
        return None
    # The splitter must have retained literal spans from the user request.
    if any(chunk not in query for chunk in chunks):
        return None
    decisions = []
    for chunk in chunks:
        decision = classify_mode_decision(
            chunk, lang,
            conversation_context=conversation_context,
            deadline_at=deadline_at,
        )
        # The mode classifier intentionally knows nothing about source
        # identity.  Only an exact match admitted by the publication registry
        # can turn an observational "read this document" clause into static
        # explanation; mutations remain ACT and arbitrary files remain
        # operational observations.
        if (decision.mode == "OBSERVE"
                and resolve_reference(chunk, lang=lang) is not None):
            decision = replace(decision, mode="EXPLAIN")
        decisions.append((chunk, decision))
    decisions = tuple(decisions)
    unavailable = [
        decision for _chunk, decision in decisions if not decision.available
    ]
    if unavailable:
        raise MixedSplitUnavailable(unavailable[0].reason)
    explanations = [
        (chunk, decision) for chunk, decision in decisions
        if decision.mode == "EXPLAIN"
    ]
    operations = [
        chunk for chunk, decision in decisions
        if decision.mode in {"OBSERVE", "ACT"}
    ]
    if len(explanations) != 1 or len(operations) != 1:
        return None
    explanation, decision = explanations[0]
    return MixedSplit(explanation, operations[0], decision)


def create_pending(*, sender_id: str, principal: TutorPrincipal,
                   action_query: str, catalog_version: str,
                   answer: TutorAnswer,
                   deadline_at: float = 0.0) -> TutorAnswer:
    """Persist a principal-bound, expiring, one-shot action handoff."""

    if not sender_id or not action_query or not catalog_version:
        raise ValueError("incomplete Tutor handoff prerequisites")
    from .deadline import new_deadline, remaining, require_commit_window
    deadline_at = deadline_at or new_deadline()
    remaining(deadline_at)
    import dialog_pending

    dialog_id = uuid.uuid4().hex[:16]
    nonce = secrets.token_hex(16)
    query_hash = hashlib.sha256(action_query.encode("utf-8")).hexdigest()
    idempotency_key = hashlib.sha256(
        "\0".join((
            principal.user_id,
            principal.actor,
            principal.audience,
            principal.channel,
            principal.conversation_id,
            query_hash,
        )).encode("utf-8")
    ).hexdigest()
    prompt = _msg("MSG_TUTOR_HANDOFF_PROMPT")
    state = {
        "dialog_id": dialog_id,
        "title": _msg("MSG_TUTOR_HANDOFF_TITLE"),
        "description": _msg("MSG_TUTOR_HANDOFF_DESCRIPTION"),
        "dialog": [{
            "var": "decision",
            "prompt": prompt,
            "schema": {"kind": "choice", "choices": [
                {"value": "execute",
                 "label": _msg("MSG_TUTOR_HANDOFF_CONTINUE")},
                {"value": "cancel",
                 "label": _msg("MSG_TUTOR_HANDOFF_CANCEL")},
            ]},
            "optional": False,
        }],
        "fmt": "dialogue",
        "values_collected": {},
        "step_index": 0,
        "started_at": now_iso_offset(),
        "actor": principal.actor,
        "owner_user_id": principal.user_id,
        "channel": principal.channel,
        "conversation_id": principal.conversation_id,
        "sender_id": sender_id,
        "timeout_s": min(600, max(60, int(
            os.environ.get("METNOS_TUTOR_HANDOFF_TTL_S", "300")))),
        "completed": False,
        "cancelled": False,
        # Channel adapters may omit the generic var/value receipt: this
        # choice is a consent gate, not a data-entry form.
        "suppress_completion_summary": True,
        "on_complete": {
            "type": "tutor_handoff",
            "literal_query": action_query,
            "query_hash": query_hash,
            "catalog_version": catalog_version,
            "nonce": nonce,
            "owner_user_id": principal.user_id,
            "conversation_id": principal.conversation_id,
            "created_at": time.time(),
        },
    }
    require_commit_window(deadline_at)
    created = False
    try:
        outcome = dialog_pending.create_if_no_active(
            sender_id, dialog_id, state,
            idempotency_key=idempotency_key,
        )
        if outcome != "created":
            notice_key = (
                "MSG_TUTOR_HANDOFF_REPLAYED"
                if outcome == "already_claimed"
                else "MSG_TUTOR_PENDING_PRESERVED"
            )
            return replace(
                answer,
                esito="clarification",
                answer_md=(
                    f"{answer.answer_md.rstrip()}\n\n{_msg(notice_key)}"),
                handoff_query="",
                handoff_created=False,
                pending_dialog_id="",
                gap_reason="mode_ambiguity",
            )
        created = True
        # If the atomic replace crossed the deadline, do not leave an invisible
        # consent awaiting a reply that was never shown to the user.
        remaining(deadline_at)
    except Exception:
        if created:
            try:
                dialog_pending.cancel_pending(
                    sender_id, dialog_id,
                    owner_user_id=principal.user_id)
            except Exception:
                pass
        raise
    rendered = (
        f"{answer.answer_md.rstrip()}\n\n{prompt}\n\n"
        f"1. {_msg('MSG_TUTOR_HANDOFF_CONTINUE')}\n"
        f"2. {_msg('MSG_TUTOR_HANDOFF_CANCEL')}"
    )
    return replace(
        answer,
        answer_md=rendered,
        handoff_created=True,
        pending_dialog_id=dialog_id,
    )
