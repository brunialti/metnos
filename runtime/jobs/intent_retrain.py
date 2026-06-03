"""intent_retrain.py — Job scheduler v2: re-train intent classifier weekly.

Trigger: scheduler v2 `daily@03:00` (registrato in builtin_callbacks).
Pipeline:
1. Carica seed pairs + turn log ultimi 7 giorni.
2. Re-train Qwen3-Embedding-0.6B se nuove pair >= 20 vs ultimo training.
3. Eval su holdout, promote LWW se acc > current.
4. Audit JSONL append.

Idempotente §7.9: se nessun nuovo dato → skip (audit "skip_no_new_data").
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def count_new_turnlog_pairs(since_ts: float, days: int = 7) -> int:
    """Count distinct (query, object) pairs in turn log since timestamp."""
    turn_dir = Path.home() / ".local" / "share" / "metnos" / "turns"
    if not turn_dir.exists():
        return 0
    import datetime as _dt
    cutoff = _dt.date.today() - _dt.timedelta(days=days)
    seen = set()
    for fp in sorted(turn_dir.glob("*.jsonl")):
        try:
            file_date = _dt.date.fromisoformat(fp.stem)
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        if fp.stat().st_mtime < since_ts:
            continue
        for ln in fp.read_text(errors="ignore").splitlines():
            try:
                t = json.loads(ln)
            except json.JSONDecodeError:
                continue
            q = t.get("user_query") or t.get("query")
            if not q or t.get("final_kind") != "answer":
                continue
            steps = t.get("plan") or t.get("steps") or []
            if not steps:
                continue
            tool = (steps[0] or {}).get("executor", "") or (steps[0] or {}).get("chosen_tool", "")
            parts = tool.split("_") if "_" in tool else []
            if len(parts) < 2:
                continue
            seen.add((q.lower().strip(), parts[1]))
    return len(seen)


def last_train_ts() -> float:
    audit_fp = Path.home() / ".local" / "share" / "metnos" / "intent_classifier" / "retrain_audit.jsonl"
    if not audit_fp.exists():
        return 0
    last = 0.0
    for ln in audit_fp.read_text().splitlines():
        try:
            r = json.loads(ln)
            if r.get("decision") == "promoted":
                last = max(last, float(r.get("ts", 0)))
        except json.JSONDecodeError:
            continue
    return last


def callback(*args, **kwargs) -> dict:
    """Scheduler v2 callback signature."""
    since = last_train_ts()
    new_pairs = count_new_turnlog_pairs(since, days=7)
    min_new = int(os.environ.get("METNOS_INTENT_RETRAIN_MIN_NEW", "20"))
    if new_pairs < min_new:
        return {
            "decision": "skip_no_new_data",
            "new_pairs": new_pairs,
            "min_required": min_new,
        }
    # Invoke train.py as subprocess (isolated, won't crash host)
    cmd = [sys.executable, "-m", "runtime.intent_classifier.train",
           "--epochs", "5", "--batch-size", "24"]
    t0 = time.time()
    try:
        out = subprocess.run(
            cmd, cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True, text=True, timeout=1800,
        )
        elapsed = time.time() - t0
        return {
            "decision": "trained",
            "exit_code": out.returncode,
            "new_pairs": new_pairs,
            "elapsed_s": elapsed,
            "stdout_tail": out.stdout[-500:],
            "stderr_tail": out.stderr[-500:] if out.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"decision": "timeout", "new_pairs": new_pairs}
    except Exception as e:
        return {"decision": "error", "error": str(e), "new_pairs": new_pairs}


if __name__ == "__main__":
    r = callback()
    print(json.dumps(r, indent=2, ensure_ascii=False))
