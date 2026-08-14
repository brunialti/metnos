"""proposals_eta_index.py — store sqlite di latenza per shape di percorso.

Indicizza, per ogni `path_shape_hash` (vedi `path_shape.py`), il p50/p95
di tempo wall-clock dei turni che hanno percorso quella shape negli
ultimi N giorni. Usato dall'auto-evaluator delle proposte synth
(ADR 0122) per il signal `eta_speedup`: se `path_eta_p50_ms /
new_executor_latency_p50_ms >= 2.0`, la sostituzione del path con il
nuovo executor accelera l'esperienza utente.

Storage: `~/.local/share/metnos/proposals_eta.sqlite`.
Schema:

    CREATE TABLE path_eta_index (
        path_hash TEXT PRIMARY KEY,
        sample_count INTEGER NOT NULL,
        p50_ms INTEGER NOT NULL,
        p95_ms INTEGER NOT NULL,
        last_seen REAL NOT NULL,
        sample_steps_json TEXT
    );

`sample_steps_json` = JSON list dei tool del primo turno osservato per
quella shape (debug/audit), non source-of-truth.

Determinismo §7.9: pure sqlite + math, niente LLM.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Iterable

import config as _C  # §7.11

DB_PATH = _C.PATH_USER_DATA / "proposals_eta.sqlite"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS path_eta_index (
            path_hash TEXT PRIMARY KEY,
            sample_count INTEGER NOT NULL,
            p50_ms INTEGER NOT NULL,
            p95_ms INTEGER NOT NULL,
            last_seen REAL NOT NULL,
            sample_steps_json TEXT
        )
        """
    )
    conn.commit()


def _open(db_path: Path | None = None) -> sqlite3.Connection:
    p = db_path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30.0)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _percentile(sorted_values: list[int], pct: float) -> int:
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return int(sorted_values[0])
    # Nearest-rank, deterministico.
    k = max(0, min(len(sorted_values) - 1, int(round((pct / 100.0) * (len(sorted_values) - 1)))))
    return int(sorted_values[k])


def upsert_aggregate(
    path_hash: str,
    samples_ms: Iterable[int],
    *,
    sample_steps: list[str] | None = None,
    db_path: Path | None = None,
    last_seen: float | None = None,
) -> dict:
    """Riscrive l'aggregato per `path_hash` con `samples_ms`.

    Idempotente: ricalcola p50/p95 su tutti i samples (non incrementale).
    Il caller (`aggregate_from_jsonls`) e' responsabile di passare la
    lista completa per la finestra desiderata.
    """
    if not path_hash:
        raise ValueError("path_hash non puo' essere vuoto")
    samples = sorted(int(x) for x in samples_ms if isinstance(x, (int, float)) and x > 0)
    if not samples:
        return {"path_hash": path_hash, "sample_count": 0, "p50_ms": 0, "p95_ms": 0}
    p50 = _percentile(samples, 50)
    p95 = _percentile(samples, 95)
    ts = float(last_seen) if last_seen is not None else time.time()
    sample_steps_json = json.dumps(sample_steps or [], ensure_ascii=False)
    with closing(_open(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO path_eta_index
                (path_hash, sample_count, p50_ms, p95_ms, last_seen, sample_steps_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(path_hash) DO UPDATE SET
                sample_count=excluded.sample_count,
                p50_ms=excluded.p50_ms,
                p95_ms=excluded.p95_ms,
                last_seen=excluded.last_seen,
                sample_steps_json=excluded.sample_steps_json
            """,
            (path_hash, len(samples), p50, p95, ts, sample_steps_json),
        )
        conn.commit()
    return {"path_hash": path_hash, "sample_count": len(samples),
            "p50_ms": p50, "p95_ms": p95, "last_seen": ts,
            "sample_steps": sample_steps or []}


def lookup(path_hash: str, db_path: Path | None = None) -> dict | None:
    """Ritorna il record per `path_hash`, oppure None se assente."""
    if not path_hash:
        return None
    p = db_path or DB_PATH
    if not p.exists():
        return None
    with closing(_open(p)) as conn:
        row = conn.execute(
            "SELECT * FROM path_eta_index WHERE path_hash = ?",
            (path_hash,),
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    try:
        out["sample_steps"] = json.loads(out.pop("sample_steps_json") or "[]")
    except (TypeError, ValueError):
        out["sample_steps"] = []
    return out


def aggregate_from_jsonls(
    *,
    since_ts: float,
    turns_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict:
    """Walk dei turn JSONL, calcola path_shape_hash + total_ms per ogni
    turno con `ts_start >= since_ts`, scrive aggregati in sqlite.

    Ritorna `{"shapes": int, "samples": int, "files_read": int}`.
    Idempotente: l'upsert riscrive l'aggregato per shape (no doppio conto).
    """
    from path_shape import extract_path_shape, turn_total_ms, steps_to_tools

    base = turns_dir or (_C.PATH_USER_DATA / "turns")
    if not base.exists():
        return {"shapes": 0, "samples": 0, "files_read": 0}

    by_shape_samples: dict[str, list[int]] = {}
    by_shape_steps: dict[str, list[str]] = {}
    by_shape_last_seen: dict[str, float] = {}
    files_read = 0
    samples_seen = 0

    for fp in sorted(base.glob("*.jsonl")):
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        files_read += 1
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (TypeError, ValueError):
                continue
            ts = rec.get("ts_start")
            if not isinstance(ts, (int, float)) or ts < since_ts:
                continue
            shape, n_steps = extract_path_shape(rec)
            if not shape or n_steps < 1:
                continue
            t_ms = turn_total_ms(rec)
            if not t_ms or t_ms <= 0:
                continue
            samples_seen += 1
            by_shape_samples.setdefault(shape, []).append(int(t_ms))
            if shape not in by_shape_steps:
                by_shape_steps[shape] = steps_to_tools(rec.get("steps") or [])
            by_shape_last_seen[shape] = max(by_shape_last_seen.get(shape, 0.0), float(ts))

    for shape, samples in by_shape_samples.items():
        upsert_aggregate(
            shape,
            samples,
            sample_steps=by_shape_steps.get(shape),
            last_seen=by_shape_last_seen.get(shape),
            db_path=db_path,
        )

    return {"shapes": len(by_shape_samples), "samples": samples_seen,
            "files_read": files_read}


def count_shape_calls(
    path_hash: str,
    *,
    since_ts: float,
    turns_dir: Path | None = None,
) -> int:
    """Conta i turni che hanno percorso `path_hash` in `[since_ts, now]`.

    Lettura diretta dei JSONL (sample_count nell'index e' filtered ma
    tipicamente piu' largo dell'orizzonte 60d). Usato per `call_freq_60d`
    nell'evaluator.
    """
    from path_shape import extract_path_shape

    base = turns_dir or (_C.PATH_USER_DATA / "turns")
    if not base.exists() or not path_hash:
        return 0
    n = 0
    for fp in sorted(base.glob("*.jsonl")):
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (TypeError, ValueError):
                continue
            ts = rec.get("ts_start")
            if not isinstance(ts, (int, float)) or ts < since_ts:
                continue
            shape, _ = extract_path_shape(rec)
            if shape == path_hash:
                n += 1
    return n


__all__ = [
    "DB_PATH",
    "upsert_aggregate",
    "lookup",
    "aggregate_from_jsonls",
    "count_shape_calls",
]


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m proposals_eta_index aggregate --days N`."""
    import argparse

    ap = argparse.ArgumentParser(prog="proposals_eta_index")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_agg = sub.add_parser("aggregate", help="Aggrega i turn JSONL ultimi N giorni")
    p_agg.add_argument("--days", type=int, default=7)
    p_lk = sub.add_parser("lookup", help="Stampa record per path_hash")
    p_lk.add_argument("hash")
    args = ap.parse_args(argv)
    if args.cmd == "aggregate":
        since = time.time() - args.days * 86400
        rep = aggregate_from_jsonls(since_ts=since)
        print(json.dumps(rep, indent=2))
        return 0
    if args.cmd == "lookup":
        rec = lookup(args.hash)
        print(json.dumps(rec, indent=2, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
