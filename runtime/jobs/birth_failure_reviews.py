# SPDX-License-Identifier: MIT
"""Consume the exact-execution failure review outbox (RM-0008 F5).

The quarantine already committed when the job was enqueued; this pass only
classifies it. The review publishes nothing, reactivates nothing and holds no
Birth key, so a missing verdict costs information, never safety.

An installation that still owns its lifecycle state in the name-based stores
has no outbox and reports no work. Without a configured frontier tier there
is no opt-in and no capability: the backlog is reported and left intact.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("metnos.birth_failure_reviews")

REVIEW_LIMIT = 20


def _outbox_path() -> Path | None:
    """Locate the configured outbox, or report that this owner has none."""
    import config
    from executor_birth_activation_mode import BirthStateOwner, read_birth_activation_state

    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        return None
    path = Path(config.PATH_USER_STATE) / "birth" / "failure_reviews.sqlite"
    return path if path.is_file() else None


def _frontier_consent() -> bool:
    """Configuring the optional frontier tier is this product's opt-in."""
    from llm_router import FRONTIER_TIER, LLMRouter

    try:
        return FRONTIER_TIER in LLMRouter().tiers
    except Exception as ex:
        log.warning("birth_failure_reviews: tier configuration unreadable: %r", ex)
        return False


def task_birth_failure_reviews() -> dict:
    """Review a bounded batch of quarantined executions, then retire the jobs."""
    from executor_birth_failure_review import FailureReviewError, review_failure_once
    from executor_birth_feedback import (
        FeedbackError, pending_failure_reviews, resolve_failure_review_job,
    )

    report: dict = {"ok": True, "reviewed": 0, "exhausted": 0,
                    "deferred": 0, "pending": 0, "verdicts": {}}
    try:
        outbox = _outbox_path()
    except Exception as ex:
        log.warning("birth_failure_reviews: outbox owner unresolved: %r", ex)
        return {"ok": False, "error_class": getattr(ex, "code", type(ex).__name__)}
    if outbox is None:
        report["status"] = "no_outbox"
        return report
    try:
        jobs = pending_failure_reviews(db_path=outbox, limit=REVIEW_LIMIT)
    except FeedbackError as ex:
        return {"ok": False, "error_class": ex.code, "error": ex.detail}
    report["pending"] = len(jobs)
    if not jobs:
        return report
    if not _frontier_consent():
        report["status"] = "consent_absent"
        report["deferred"] = len(jobs)
        return report
    for job_id, request in jobs:
        try:
            decision = review_failure_once(
                request, consent_valid=True, db_path=outbox,
            )
        except FailureReviewError as ex:
            if ex.code == "failure_review_already_attempted":
                # The single model attempt is durably consumed. Keeping the
                # job would promise a retry the store will always refuse.
                resolve_failure_review_job(job_id, db_path=outbox)
                report["exhausted"] += 1
                continue
            log.warning("birth_failure_reviews: %s deferred: %r", job_id, ex)
            report["deferred"] += 1
            report.setdefault("last_error", ex.code)
            continue
        resolve_failure_review_job(job_id, db_path=outbox)
        report["reviewed"] += 1
        verdict = decision.review.verdict.value
        report["verdicts"][verdict] = report["verdicts"].get(verdict, 0) + 1
    return report
