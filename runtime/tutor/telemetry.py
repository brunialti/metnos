"""TurnLog records with a Tutor-specific extension.

The turn record carries the request in the same form the ordinary engine
records it, plus the normalized hash the F4 ledger keys on.

Why the request is written here (16/8/2026).  It used to be represented only
by its hash, and the practical effect was that the Tutor's admissions could
not be audited: the turn history showed 167 Tutor turns, 13 of which closed
with no source at all, and nobody could see WHICH requests those were.  The
Tutor is an early exit placed before the engine, so an admission it should not
have made is a silent diversion; a store that already holds every engine
request of the same user, in the same file, gains no protection by hiding this
one.  Deletion of an owner removes both, as before.
"""

from __future__ import annotations

from dataclasses import replace
import json
import queue
import threading
import time
import uuid

import config
from logging_setup import get_logger

from .models import TutorAnswer, TutorRequest

log = get_logger(__name__)

_POST_COMMIT_QUEUE: queue.Queue = queue.Queue(maxsize=64)
_WORKER_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None


def _prepare(request: TutorRequest, answer: TutorAnswer):
    """Build a query-minimized job and assign its visible turn identifier."""

    turn_id = answer.turn_id or uuid.uuid4().hex[:16]
    now = time.time()
    from .associations import query_hash as normalized_query_hash
    query_hash = normalized_query_hash(request.query_redacted)
    recorded_answer = replace(answer, turn_id=turn_id)
    payload = {
        "ts_start": now - (max(answer.elapsed_ms, 0) / 1000.0),
        "ts_end": now,
        # Same field, same form as an engine turn: without it a Tutor
        # admission is not auditable after the fact.
        "user_query": request.query_redacted,
        "turn_id": turn_id,
        "mode": "tutor",
        "candidates": [],
        "steps": [],
        "final_message": answer.answer_md,
        "final_kind": "answer",
        "actor": request.principal.actor,
        "owner_user_id": request.principal.user_id,
        "channel": request.principal.channel,
        "conversation_id": request.principal.conversation_id,
        "redacted": True,
        "n_redacted_fields": 0,
        "effect_counts": None,
        "tutor_esito": answer.esito,
        "tutor_card_ids": list(answer.card_ids),
        "tutor_source_ids": list(answer.source_ids),
        "tutor_score_band": answer.score_band,
        "tutor_elapsed_ms": answer.elapsed_ms,
        "tutor_detection": answer.detection,
        "tutor_query_hash": f"sha256:{query_hash}",
        "tutor_pending_preserved": bool(request.has_pending),
        "tutor_repair_pass": int(answer.repair_pass),
        "tutor_repair_missing": list(answer.repair_missing),
        "tutor_repair_remaining": list(answer.repair_remaining),
        "tutor_probe_statuses": [list(row) for row in answer.probe_statuses],
        "tutor_gap_reason": answer.gap_reason,
        "tutor_handoff_created": bool(answer.handoff_created),
    }
    ledger = {
        "owner_user_id": request.principal.user_id,
        "normalized_query_hash": query_hash,
        "lang": request.lang,
        "audience": request.principal.audience,
        "answer": recorded_answer,
    }
    return recorded_answer, payload, ledger


def _persist(payload: dict, ledger: dict) -> None:
    """Perform best-effort durable writes outside the response critical path."""

    owner_user_id = str(ledger.get("owner_user_id") or "")
    try:
        import users
        if users.owner_deletion_started(owner_user_id):
            return
    except Exception:
        # Identity-store uncertainty must not create post-delete evidence.
        return
    try:
        config.ensure_private_dir(config.PATH_TURNS)
        path = config.PATH_TURNS / f"{time.strftime('%Y-%m-%d')}.jsonl"
        data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                + "\n").encode("utf-8")
        config.append_private_bytes(path, data)
    except Exception:
        log.warning("tutor telemetry append failed", exc_info=True)
    try:
        from .gaps import record_turn_hashed
        record_turn_hashed(**ledger)
    except Exception:
        log.warning("Tutor F4 ledger append failed", exc_info=True)


def record(request: TutorRequest, answer: TutorAnswer) -> TutorAnswer:
    """Synchronously append telemetry (kept for maintenance/tests)."""

    recorded_answer, payload, ledger = _prepare(request, answer)
    _persist(payload, ledger)
    return recorded_answer


def _worker_loop() -> None:
    while True:
        job = _POST_COMMIT_QUEUE.get()
        try:
            if job is None:
                return
            payload, ledger = job
            _persist(payload, ledger)
        except Exception:
            log.warning("Tutor post-commit worker failed", exc_info=True)
        finally:
            _POST_COMMIT_QUEUE.task_done()


def start_worker() -> None:
    """Start the single daemon writer during channel/server initialization."""

    global _WORKER
    if _WORKER is not None and _WORKER.is_alive():
        return
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(
            target=_worker_loop, name="metnos-tutor-postcommit",
            daemon=True)
        _WORKER.start()


def record_async(request: TutorRequest, answer: TutorAnswer) -> TutorAnswer:
    """Assign the turn ID and enqueue bounded telemetry without blocking."""

    recorded_answer, payload, ledger = _prepare(request, answer)
    if _WORKER is None or not _WORKER.is_alive():
        log.warning("Tutor post-commit worker unavailable; telemetry dropped")
        return recorded_answer
    try:
        _POST_COMMIT_QUEUE.put_nowait((payload, ledger))
    except queue.Full:
        log.warning("Tutor post-commit queue full; telemetry dropped")
    return recorded_answer
