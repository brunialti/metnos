"""Trusted adapters between channel identity and the isolated tutor module."""

from __future__ import annotations

from dataclasses import dataclass
import functools

from logging_setup import get_logger
from messages import get as _msg
from tutor.telemetry import record_async, start_worker as _start_telemetry_worker

log = get_logger(__name__)
_start_telemetry_worker()


@dataclass(frozen=True, slots=True)
class BoundaryUnavailable:
    """Dependency-light terminal value used even during a partial deploy."""

    esito: str
    answer_md: str
    score_band: str = "none"
    detection: str = "semantic_unavailable"
    gap_reason: str = "mode_unavailable"
    turn_id: str = ""
    source_ids: tuple[str, ...] = ()
    card_ids: tuple[str, ...] = ()
    pending_dialog_id: str = ""
    handoff_query: str = ""


def unavailable_answer(*, has_pending: bool = False):
    """Terminal fail-closed answer for an unexpected Tutor boundary failure."""

    answer = _msg("MSG_TUTOR_UNAVAILABLE")
    if has_pending:
        answer = f"{answer.rstrip()}\n\n{_msg('MSG_TUTOR_PENDING_PRESERVED')}"
    return BoundaryUnavailable(
        esito="tutor_error",
        answer_md=answer,
    )


def http_principal(*, role: str, device_id: str | None, actor: str,
                   user_id: str = "", conversation_id: str = ""):
    """Map only middleware-authenticated HTTP fields; body cannot elevate."""

    from tutor.models import TutorPrincipal
    audience = "instance_admin" if role == "admin" else "user"
    return TutorPrincipal(
        # L'identita' della persona e' stabile tra browser; `device_id` non
        # deve spezzare il contesto quando la conversazione viene trasferita.
        user_id=str(user_id or actor or device_id or "http-user"),
        actor=str(actor or "host"),
        audience=audience,
        channel="http",
        conversation_id=str(conversation_id or ""),
    )


def telegram_principal(principal: dict, *, conversation_id: str = ""):
    """Map the users/pairing result resolved by ``ChannelDaemon``."""

    from tutor.models import TutorPrincipal
    role = str(principal.get("role") or "")
    audience = "instance_admin" if role == "host" else "user"
    return TutorPrincipal(
        user_id=str(principal.get("user_id") or principal.get("sender_id") or
                    principal.get("actor") or "telegram-user"),
        actor=str(principal.get("actor") or "guest"),
        audience=audience,
        channel="telegram",
        conversation_id=str(conversation_id or principal.get("sender_id") or ""),
    )


def _owner_scoped_answer(function):
    @functools.wraps(function)
    def guarded(query: str, principal, **kwargs):
        from user_lifecycle import owner_session
        with owner_session(principal.user_id):
            return function(query, principal, **kwargs)
    return guarded


@_owner_scoped_answer
def _answer(query: str, principal, *, has_pending: bool,
            pending_sender_id: str):
    """Execute one fully guarded Tutor lifecycle."""

    from credential_intake import contains_sensitive_input
    from i18n import current_lang
    from tutor import TutorRequest, answer_request
    from tutor.conversation import forget, recent_context, remember
    from tutor.deadline import new_deadline, remaining, require_commit_window
    # Direct callers cannot use a secret-shaped request to probe Tutor and
    # then fall through with the original value. Channel adapters normally
    # consume these before reaching this function; this is the final guard.
    if contains_sensitive_input(query):
        return unavailable_answer(has_pending=has_pending)
    deadline_at = new_deadline()
    lang = current_lang()
    conversation_context = recent_context(principal)
    remaining(deadline_at)
    request = TutorRequest(
        query_redacted=query,
        lang=lang,
        principal=principal,
        has_pending=bool(has_pending),
        conversation_context=conversation_context,
        deadline_at=deadline_at,
    )
    result = answer_request(request)
    remaining(deadline_at)
    if result is None:
        # An intervening operational/planner turn breaks Tutor adjacency.
        # Keeping the older exchange would let a later ellipsis inherit stale
        # retrieval context (never authority, but still the wrong topic).
        forget(principal)
        return None
    # One preflight before the lifecycle commit. After a handoff becomes
    # visible, best-effort memory/telemetry must never turn the ready answer
    # into an unavailable response and leave an invisible pending behind.
    require_commit_window(deadline_at, minimum_s=2.0)
    if result.handoff_query:
        if not has_pending and pending_sender_id:
            try:
                from tutor.handoff import create_pending
                catalog_version = (
                    result.evidence.catalog_version if result.evidence else "")
                result = create_pending(
                    sender_id=pending_sender_id,
                    principal=principal,
                    action_query=result.handoff_query,
                    catalog_version=catalog_version,
                    answer=result,
                    deadline_at=deadline_at,
                )
            except Exception:
                # A failed pending must not falsely claim that execution is ready.
                log.warning("Tutor handoff pending creation failed", exc_info=True)
                from dataclasses import replace
                result = replace(
                    result,
                    esito="clarification",
                    handoff_query="",
                    answer_md=(
                        f"{result.answer_md.rstrip()}\n\n"
                        f"{_msg('MSG_TUTOR_MIXED_CLARIFY')}"),
                    gap_reason="mode_ambiguity",
                )
        else:
            from dataclasses import replace
            notice = (
                _msg("MSG_TUTOR_PENDING_PRESERVED") if has_pending
                else _msg("MSG_TUTOR_MIXED_CLARIFY")
            )
            result = replace(
                result,
                esito="clarification",
                handoff_query="",
                answer_md=f"{result.answer_md.rstrip()}\n\n{notice}",
                gap_reason="mode_ambiguity",
            )
    try:
        remember(request, result)
    except Exception:
        log.warning("Tutor conversation memory unavailable", exc_info=True)
    try:
        return record_async(request, result)
    except Exception:
        # Telemetry is observability, never the semantic outcome. In
        # particular it must not strand a handoff whose prompt will be sent.
        log.warning("Tutor telemetry unavailable", exc_info=True)
        return result


def answer(query: str, principal, *, has_pending: bool = False,
           pending_sender_id: str = ""):
    """Narrow total boundary shared by HTTP and Telegram.

    ``None`` means the semantic gate positively classified a non-help request.
    Every technical failure is a localized terminal result, never implicit
    permission to continue into the planner.
    """

    try:
        return _answer(
            query, principal,
            has_pending=bool(has_pending),
            pending_sender_id=pending_sender_id,
        )
    except Exception:
        log.warning("Tutor boundary failed closed", exc_info=True)
        return unavailable_answer(has_pending=bool(has_pending))
