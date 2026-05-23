"""skill_audit — log audit strutturato per executor importati da skill.

Mini-version della Fase C del scaling roadmap (17/5/2026): foundation
per il sandbox per-skill enforcement futuro. Oggi solo audit log,
nessun enforcement.

Storage: `~/.local/share/metnos/skill_audit.jsonl` (append-only).

Schema record:
    {ts, skill_id, skill_provenance, executor_name, args_sha,
     outcome, elapsed_ms, n_bytes_in, n_bytes_out, error_class}

Determinismo §7.9: zero LLM, scrittura atomica.

Quando il sandbox passa a enforcement (Fase C full), questo modulo
viene esteso per loggare anche `sandbox_violations: list[str]`.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import config as _C  # §7.11 — rispetta METNOS_USER_DATA
AUDIT_PATH = _C.PATH_USER_DATA / "skill_audit.jsonl"


def _args_sha(args: Any) -> str:
    """SHA-256 deterministico dei args serializzati (escluso secrets)."""
    try:
        serialized = json.dumps(args, sort_keys=True, ensure_ascii=False,
                                 default=str)
    except Exception:
        serialized = str(args)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _bytes_approx(obj: Any) -> int:
    """Stima byte del payload (per audit volume tracking)."""
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str))
    except Exception:
        return len(str(obj))


def audit_skill_invocation(
    *,
    executor_name: str,
    provenance: dict | None,
    args: Any,
    result: Any,
    elapsed_ms: int,
    error_class: str | None = None,
) -> None:
    """Append un record audit per l'invocazione di un executor importato.

    `provenance` proviene da `manifest.toml::[provenance]` (ADR 0123):
    skill_id, imported_from, source_version, source_sha256, imported_at.

    NON registra args/result completi (potrebbero contenere PII). Solo
    SHA + byte count per audit volume + outcome ok/error.
    """
    if not provenance:
        return  # non e' un executor importato (builtin) → no audit
    skill_id = provenance.get("imported_from", "") or "unknown"
    outcome = "ok" if (
        isinstance(result, dict) and result.get("ok") is True
    ) else "error"
    rec = {
        "ts": time.time(),
        "skill_id": skill_id,
        "skill_version": provenance.get("source_version", "") or "",
        "skill_sha": (provenance.get("source_sha256", "") or "")[:16],
        "executor_name": executor_name,
        "args_sha": _args_sha(args),
        "outcome": outcome,
        "elapsed_ms": int(elapsed_ms),
        "n_bytes_in": _bytes_approx(args),
        "n_bytes_out": _bytes_approx(result),
    }
    if error_class:
        rec["error_class"] = error_class
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # fail-silent: l'audit non deve mai bloccare l'invocazione


def stats(since_ts: float = 0.0) -> dict:
    """Aggregato leggibile dell'audit log. Per CLI / watchdog soglia."""
    from collections import Counter
    if not AUDIT_PATH.exists():
        return {"records": 0, "by_skill": {}, "by_outcome": {}}
    n_total = 0
    by_skill: Counter = Counter()
    by_outcome: Counter = Counter()
    by_executor: Counter = Counter()
    skills_seen: set = set()
    for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("ts", 0) < since_ts:
            continue
        n_total += 1
        by_skill[r.get("skill_id", "?")] += 1
        by_outcome[r.get("outcome", "?")] += 1
        by_executor[r.get("executor_name", "?")] += 1
        skills_seen.add(r.get("skill_id", "?"))
    return {
        "records": n_total,
        "n_distinct_skills": len(skills_seen),
        "by_skill": dict(by_skill.most_common()),
        "by_outcome": dict(by_outcome.most_common()),
        "by_executor": dict(by_executor.most_common(10)),
    }
