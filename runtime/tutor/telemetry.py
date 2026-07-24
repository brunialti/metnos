"""Privacy-minimized tutor records in the existing TurnLog JSONL stream."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
import time
import uuid

import config
from logging_setup import get_logger

from .models import TutorAnswer, TutorRequest

log = get_logger(__name__)


def record(request: TutorRequest, answer: TutorAnswer) -> TutorAnswer:
    """Append one TurnLog-compatible record; telemetry can never block help."""

    turn_id = uuid.uuid4().hex[:16]
    now = time.time()
    query_hash = hashlib.sha256(
        request.query_redacted.strip().encode("utf-8")
    ).hexdigest()
    payload = {
        "ts_start": now - (max(answer.elapsed_ms, 0) / 1000.0),
        "ts_end": now,
        # Deliberately no raw query in Tutor telemetry.
        "user_query": "",
        "turn_id": turn_id,
        "mode": "tutor",
        "candidates": [],
        "steps": [],
        "final_message": answer.answer_md,
        "final_kind": "answer",
        "actor": request.principal.actor,
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
    }
    try:
        config.PATH_TURNS.mkdir(parents=True, exist_ok=True)
        path = config.PATH_TURNS / f"{time.strftime('%Y-%m-%d')}.jsonl"
        data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                + "\n").encode("utf-8")
        descriptor = os.open(
            path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, data)
        finally:
            os.close(descriptor)
    except Exception:
        log.warning("tutor telemetry append failed", exc_info=True)
    return replace(answer, turn_id=turn_id)
