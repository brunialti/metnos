"""skill_audit — log audit strutturato per executor importati da skill.

Mini-version della Fase C del scaling roadmap (17/5/2026): foundation
per il sandbox per-skill enforcement futuro. Oggi solo audit log,
nessun enforcement.

Storage: `~/.local/share/metnos/skill_audit/<YYYY-MM-DD>.jsonl`
(sharded daily, append-only).

Razionale shard daily (ADR 0159):
- query per giorno facile (`grep audit_24_5`),
- retention semplice (rm file > 90 giorni),
- log rotation naturale (no rewrite del flat),
- statistiche granulari per finestra temporale.

Schema record:
    {ts, skill_id, skill_provenance, executor_name, args_sha,
     outcome, elapsed_ms, n_bytes_in, n_bytes_out, error_class}

Determinismo §7.9: zero LLM, scrittura atomica.

Migration: al primo accesso a `_legacy_flat_path()` esistente, splitta
i record per giorno (chiave `ts` UTC) e scrive nei nuovi shard; archivia
il flat in `<flat>.migrated_<ts>`. Idempotente: se gia' migrato (file
archivio presente OR flat assente), no-op.

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


# ── Constants (no magic numbers, §7.3) ────────────────────────────────
AUDIT_DIR = _C.PATH_USER_DATA / "skill_audit"
_SHARD_EXT = ".jsonl"
_DATE_FMT = "%Y-%m-%d"
_MIGRATION_SUFFIX = ".migrated"


def _shard_path_for_ts(ts: float) -> Path:
    """Path dello shard giornaliero per timestamp UTC."""
    day = time.strftime(_DATE_FMT, time.gmtime(ts))
    return AUDIT_DIR / f"{day}{_SHARD_EXT}"


def _legacy_flat_path() -> Path:
    """Path legacy pre-ADR-0159 (flat `skill_audit.jsonl`)."""
    return _C.PATH_USER_DATA / "skill_audit.jsonl"


def _migrate_legacy_flat_if_present() -> None:
    """Migra il flat legacy in shard giornalieri. Idempotente.

    DEVI: chiamare prima di ogni read aggregato (stats).
    NON DEVI: chiamare in hot path (write): l'overhead `stat()` per
    every call basta una volta per process, gestito da `_MIGRATION_DONE`.
    OK: chiamata silenziosa al primo `stats()`.
    ERRORE: chiamata in `audit_skill_invocation` (write rapido).
    """
    flat = _legacy_flat_path()
    if not flat.is_file():
        return
    archive = flat.with_suffix(flat.suffix + _MIGRATION_SUFFIX)
    if archive.exists():
        return  # gia' migrato
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[str]] = {}
    try:
        for line in flat.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts", 0.0)
            try:
                day = time.strftime(_DATE_FMT, time.gmtime(float(ts)))
            except (TypeError, ValueError):
                day = time.strftime(_DATE_FMT, time.gmtime(0))
            by_day.setdefault(day, []).append(line)
        for day, lines in by_day.items():
            shard = AUDIT_DIR / f"{day}{_SHARD_EXT}"
            with shard.open("a", encoding="utf-8") as fh:
                for ln in lines:
                    fh.write(ln + "\n")
        flat.rename(archive)
    except OSError:
        pass  # migration best-effort, non blocca audit


_MIGRATION_DONE = False


def _ensure_migrated_once() -> None:
    """Run migration una sola volta per process. Lazy."""
    global _MIGRATION_DONE
    if _MIGRATION_DONE:
        return
    _migrate_legacy_flat_if_present()
    _MIGRATION_DONE = True


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
    now = time.time()
    rec = {
        "ts": now,
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
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        shard = _shard_path_for_ts(now)
        with shard.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # fail-silent: l'audit non deve mai bloccare l'invocazione


def _iter_shards() -> list[Path]:
    """Ritorna shard ordinati per nome (== ordine cronologico ASCII)."""
    if not AUDIT_DIR.is_dir():
        return []
    return sorted(
        p for p in AUDIT_DIR.iterdir()
        if p.is_file() and p.suffix == _SHARD_EXT
    )


def stats(since_ts: float = 0.0) -> dict:
    """Aggregato leggibile dell'audit log. Per CLI / watchdog soglia.

    Legge tutti gli shard. Migra il flat legacy al primo accesso.
    """
    _ensure_migrated_once()
    from collections import Counter
    shards = _iter_shards()
    if not shards:
        return {"records": 0, "by_skill": {}, "by_outcome": {}}
    n_total = 0
    by_skill: Counter = Counter()
    by_outcome: Counter = Counter()
    by_executor: Counter = Counter()
    skills_seen: set = set()
    for shard in shards:
        try:
            text = shard.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
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


# Backward-compat alias (deprecato): qualche test/CLI puo' ancora
# referenziare AUDIT_PATH. Punta al shard del giorno corrente.
def _todays_shard() -> Path:
    return _shard_path_for_ts(time.time())


AUDIT_PATH = _todays_shard()
