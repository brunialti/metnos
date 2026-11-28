"""Project four proposal adapters into change_intents.sqlite (ADR 0158).

Fingerprint upserts deduplicate proposals, count distinct converging sources,
and preserve decisions and effects already recorded. Gap scores follow the
last 30 days of requests; gaps not requested for 90 days are closed.

The nightly sequence started at 03:00 runs this callback before the observer.
It has no separate 01:00 timer. Audit records are appended as JSONL.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Any

import config as C
from change_intents import init_db, upsert_intent
from change_intent_adapters import iter_all


def task_change_intent_materialize(payload: dict | None = None) -> dict:
    """Materializer cron. Idempotente. Ritorna stats per audit + log."""
    init_db()
    ts_start = time.time()

    family_counts: Counter[str] = Counter()
    module_counts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    inserted_ids: set[str] = set()
    errors = 0

    for ci in iter_all():
        try:
            id_ = upsert_intent(ci)
            inserted_ids.add(id_)
            family_counts[ci.origin_family] += 1
            module_counts[f"{ci.origin_family}:{ci.origin_module}"] += 1
            kind_counts[ci.intent_kind] += 1
            state_counts[ci.state] += 1
        except Exception as exc:
            errors += 1
            if errors <= 5:
                # Log primi 5 in audit (full traceback skip per brevita')
                _audit({
                    "event": "error",
                    "family": ci.origin_family if ci else "unknown",
                    "module": ci.origin_module if ci else "unknown",
                    "exc": str(exc)[:200],
                })

    # Gap proposals follow the requests still recent; old gaps are dropped.
    gap_scores = gaps_expired = gaps_pruned = 0
    try:
        import learning_loop
        from engine import terminator
        gap_scores = learning_loop.refresh_gap_scores()
        gaps_expired = learning_loop.expire_gap_proposals()
        gaps_pruned = terminator.prune_gaps()
    except Exception as exc:
        errors += 1
        _audit({"event": "error", "family": "observation",
                "module": "learning_loop", "exc": str(exc)[:200]})

    elapsed_s = round(time.time() - ts_start, 2)
    # by_state reale (post-dedup) — interroga DB invece di contare yielded
    from runtime.change_intents import count_by_state
    real_state_counts = count_by_state()

    report: dict[str, Any] = {
        "ok": True,
        "elapsed_s": elapsed_s,
        "n_total_yielded": sum(family_counts.values()),
        "n_unique_intents": len(inserted_ids),
        "n_errors": errors,
        "by_family": dict(family_counts),
        "by_module": dict(module_counts),
        "by_kind": dict(kind_counts),
        "by_state_yielded": dict(state_counts),
        "by_state_real": real_state_counts,
        "gap_scores_recomputed": gap_scores,
        "gap_proposals_expired": gaps_expired,
        "gaps_pruned": gaps_pruned,
    }
    _audit({"event": "completed", **report})
    return report


def _audit(record: dict) -> None:
    """Append-only JSONL audit."""
    from audit_jsonl import append_jsonl
    audit_path = C.PATH_USER_DATA / "audit" / "change_intent_materialize.jsonl"
    record = {"ts": time.time(), **record}
    try:
        append_jsonl(audit_path, record)
    except OSError:
        pass


if __name__ == "__main__":
    import pprint
    pprint.pprint(task_change_intent_materialize())
