"""Short-lived, process-local context for genuine Tutor follow-up questions.

Raw Tutor questions remain absent from telemetry and durable stores.  This
bounded cache only bridges adjacent turns from the same authenticated
principal and conversation; entries disappear on restart or after the TTL.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
import time

from .models import TutorAnswer, TutorPrincipal, TutorRequest


_TTL_SECONDS = 15 * 60
_MAX_ENTRIES = 256
_MAX_QUERY_CHARS = 1200
_MAX_ANSWER_CHARS = 2400
_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class _Exchange:
    stored_at: float
    query: str
    answer: str


_EXCHANGES: dict[tuple[str, str, str, str, str], _Exchange] = {}


def _key(principal: TutorPrincipal) -> tuple[str, str, str, str, str] | None:
    if not principal.conversation_id:
        return None
    return (
        principal.user_id,
        principal.actor,
        principal.audience,
        principal.channel,
        principal.conversation_id,
    )


def _prune(now: float) -> None:
    expired = [
        key for key, value in _EXCHANGES.items()
        if now - value.stored_at > _TTL_SECONDS
    ]
    for key in expired:
        _EXCHANGES.pop(key, None)
    if len(_EXCHANGES) <= _MAX_ENTRIES:
        return
    oldest = sorted(
        _EXCHANGES, key=lambda item: _EXCHANGES[item].stored_at,
    )[:len(_EXCHANGES) - _MAX_ENTRIES]
    for key in oldest:
        _EXCHANGES.pop(key, None)


def recent_context(principal: TutorPrincipal) -> str:
    """Return one same-principal exchange, or an empty string."""

    key = _key(principal)
    if key is None:
        return ""
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        exchange = _EXCHANGES.get(key)
    if exchange is None:
        return ""
    return (
        f"PREVIOUS_USER_QUESTION: {exchange.query}\n"
        f"PREVIOUS_TUTOR_ANSWER: {exchange.answer}"
    )


def recent_question(principal: TutorPrincipal) -> str:
    """Solo la DOMANDA precedente, per la sonda di retrieval.

    La domanda precedente risolve il riferimento ellittico; la risposta
    precedente no: e' un testo lungo generato DALLE fonti del turno prima,
    quindi concatenarla rende il vettore quasi-duplicato di quelle stesse
    fonti e ogni follow-up sembra «guadagnare» contesto (misurato: 0,9317
    contro 0,8465 verso la pagina del turno precedente, che vinceva
    l'ereditarieta' anche quando non c'entrava). La risposta resta ammessa
    nel contesto del composer, dove serve a capire a cosa ci si riferisce.
    """

    key = _key(principal)
    if key is None:
        return ""
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        exchange = _EXCHANGES.get(key)
    return exchange.query if exchange is not None else ""


def remember(request: TutorRequest, answer: TutorAnswer) -> None:
    """Retain one bounded exchange in memory, never on disk."""

    key = _key(request.principal)
    query = request.query_redacted.strip()
    response = answer.answer_md.strip()
    if key is None or not query or not response:
        return
    now = time.monotonic()
    exchange = _Exchange(
        stored_at=now,
        query=query[:_MAX_QUERY_CHARS],
        answer=response[:_MAX_ANSWER_CHARS],
    )
    with _LOCK:
        _prune(now)
        _EXCHANGES[key] = exchange


def _clear_for_tests() -> None:
    with _LOCK:
        _EXCHANGES.clear()
