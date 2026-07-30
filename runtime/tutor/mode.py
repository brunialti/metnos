"""Closed semantic classifiers at the Tutor boundary.

The first classifier sees only the current request and describes what the
person is asking for.  Conversation context is evaluated by a separate
classifier and can therefore resolve an ellipsis without changing the request
mode or granting live-data authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from logging_setup import get_logger

from .deadline import mode_budget_s, new_deadline, phase_deadline, remaining


log = get_logger(__name__)


MODES = frozenset({"EXPLAIN", "OBSERVE", "ACT", "MIXED", "UNKNOWN"})
RELATIONS = frozenset({"FOLLOWUP", "NEW"})
_MAX_CLASSIFIER_PAYLOAD_CHARS = 8_000


def _classifier(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        execution_policy={
            "effect": "read_only",
            "parallelism_class": 0,
            "resource_class": "llm",
            "concurrency_key": "none",
            "equivalence_gate": "verified",
        },
    )


_MODE = _classifier("tutor_mode")
_RELATION = _classifier("tutor_relation")


@dataclass(frozen=True, slots=True)
class ModeDecision:
    """Semantic mode plus technical availability of that decision."""

    mode: str
    available: bool
    reason: str = ""
    is_followup: bool = False

    @property
    def current_state_requested(self) -> bool:
        """Compatibility name for the sole mode that may request live data."""

        return self.mode == "OBSERVE"


def _invoke_closed_classifier(*, prompt_name: str, executor,
                              payload: dict, lang: str,
                              allowed: frozenset[str],
                              deadline_at: float) -> tuple[str | None, str]:
    """Invoke one local classifier and validate its single-token vocabulary."""

    from executor_scheduler import SchedulerAdmissionTimeout, invoke_scheduled
    from llm_helpers import call_llm
    from prompt_loader import get as get_prompt

    call_deadline = phase_deadline(deadline_at, mode_budget_s())
    try:
        prompt = get_prompt(prompt_name, lang)
    except Exception:
        log.warning("Tutor %s prompt unavailable", prompt_name, exc_info=True)
        return None, "prompt_unavailable"

    def _call() -> dict:
        try:
            text, _metadata = call_llm(
                payload,
                prompt,
                tier="fast",
                max_tokens=12,
                max_query_chars=_MAX_CLASSIFIER_PAYLOAD_CHARS,
                timeout_s=remaining(call_deadline),
            )
            return {"ok": True, "text": text}
        except TimeoutError:
            return {"ok": False, "reason": "deadline_exhausted"}
        except Exception:
            log.warning("Tutor %s provider unavailable", prompt_name,
                        exc_info=True)
            return {"ok": False, "reason": "provider_unavailable"}

    try:
        result = invoke_scheduled(
            executor,
            _call,
            admission_timeout_s=remaining(call_deadline),
        )
    except SchedulerAdmissionTimeout:
        return None, "scheduler_timeout"
    except TimeoutError:
        return None, "deadline_exhausted"
    except Exception:
        log.warning("Tutor %s scheduler unavailable", prompt_name,
                    exc_info=True)
        return None, "scheduler_unavailable"
    if not isinstance(result, dict) or not result.get("ok"):
        reason = (str(result.get("reason") or "provider_unavailable")
                  if isinstance(result, dict) else "provider_unavailable")
        return None, reason
    value = str(result.get("text") or "").strip().upper()
    if value not in allowed:
        return None, "malformed_response"
    return value, "semantic_decision"


def classify_mode_decision(query: str, lang: str, *,
                           conversation_context: str = "",
                           deadline_at: float = 0.0) -> ModeDecision:
    """Classify current intent, then independently test follow-up relation.

    Source identity, probe inventory, retrieval results and learned associations
    are intentionally absent.  ``OBSERVE`` describes a request for data; it
    never authorizes Tutor to obtain those data.
    """

    outer_deadline = deadline_at or new_deadline(mode_budget_s())
    mode, reason = _invoke_closed_classifier(
        prompt_name="tutor_mode",
        executor=_MODE,
        payload={"language": lang, "user_query": query},
        lang=lang,
        allowed=MODES,
        deadline_at=outer_deadline,
    )
    if mode is None:
        return ModeDecision("UNKNOWN", False, reason)

    is_followup = False
    context = str(conversation_context or "").strip()
    if context:
        relation, relation_reason = _invoke_closed_classifier(
            prompt_name="tutor_relation",
            executor=_RELATION,
            payload={
                "language": lang,
                "user_query": query,
                "conversation_context": context,
            },
            lang=lang,
            allowed=RELATIONS,
            deadline_at=outer_deadline,
        )
        if relation is None:
            # Context is optional enrichment.  Its failure must not change the
            # independently established mode or inherit stale authority.
            log.warning("Tutor relation unavailable reason=%s",
                        relation_reason)
        else:
            is_followup = relation == "FOLLOWUP"
    return ModeDecision(
        mode, True, "semantic_decision", is_followup=is_followup)


def classify_mode(query: str, lang: str, *,
                  conversation_context: str = "",
                  deadline_at: float = 0.0) -> str:
    """Return only the closed semantic mode."""

    return classify_mode_decision(
        query,
        lang,
        conversation_context=conversation_context,
        deadline_at=deadline_at,
    ).mode
