"""Undo log per Metnos — append-only, fsync, flock.

Per ogni invocazione di executor con `revertible=true` il runtime scrive:
  pending  prima di invocare    {type, op_id, turn_id, ts, executor, args, plan}
  done     dopo successo        {type, op_id, ts, results}
  undone   dopo undo eseguito   {type, op_id, ts, reverse_results}

`undo_last_turn` cerca i `done` non `undone` del turno piu' recente e
chiama in ordine inverso `reverse(plan, results)` esposta dal modulo
dell'executor.

Crash safety: pending senza done ne' undone => orfano (op crashata in
volo); listato da `find_crashed`. Nessun auto-rollback.

TTL: `purge_older_than(days)` riscrive il file scartando record vecchi.
"""
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Iterator

import config as _C  # §7.11

DEFAULT_LOG = _C.PATH_USER_DATA / "undo.jsonl"
DEFAULT_BLOBS = _C.PATH_USER_DATA / "undo_blobs"


class UndoLog:
    def __init__(self, path: Path = DEFAULT_LOG):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def append_pending(self, op_id: str, turn_id: str, executor: str, args: dict, plan: dict, actor: str = "host", channel: str = "") -> None:
        self._append({
            "type": "pending",
            "op_id": op_id,
            "turn_id": turn_id,
            "ts": time.time(),
            "actor": actor,           # 'host' | 'guest:<id>' — chi ha emesso la richiesta
            "channel": channel,       # 'telegram:<chat_id>' | 'terminal' — per indirizzare notifiche
            "executor": executor,
            "args": args,
            "plan": plan,
        })

    def append_done(self, op_id: str, results: dict) -> None:
        self._append({"type": "done", "op_id": op_id, "ts": time.time(), "results": results})

    def append_undone(self, op_id: str, reverse_results: dict) -> None:
        self._append({"type": "undone", "op_id": op_id, "ts": time.time(), "reverse_results": reverse_results})

    def _iter_records(self) -> Iterator[dict]:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def _aggregate_ops(self) -> dict[str, dict]:
        """Raggruppa per op_id: {op_id: {pending, done, undone}}."""
        ops: dict[str, dict] = {}
        for rec in self._iter_records():
            op_id = rec.get("op_id")
            if not op_id:
                continue
            entry = ops.setdefault(op_id, {})
            entry[rec["type"]] = rec
        return ops

    def latest_turn_done(self) -> list[dict]:
        """Record `done` non `undone` del turno piu' recente con almeno un done.

        Ritorna i pending arricchiti con `results` (campo del done corrispondente),
        in ordine cronologico (dal primo all'ultimo eseguito).
        """
        ops = self._aggregate_ops()
        # Filtra ops con done E senza undone
        completed_ops = [
            (op["pending"], op["done"])
            for op in ops.values()
            if "pending" in op and "done" in op and "undone" not in op
        ]
        if not completed_ops:
            return []
        # Trova l'ultimo turn_id (quello del done piu' recente)
        completed_ops.sort(key=lambda pd: pd[1]["ts"])
        latest_turn_id = completed_ops[-1][0]["turn_id"]
        # Restituisci tutte le ops del medesimo turno, in ordine di esecuzione
        same_turn = [(p, d) for (p, d) in completed_ops if p["turn_id"] == latest_turn_id]
        same_turn.sort(key=lambda pd: pd[1]["ts"])
        return [
            {**pending, "results": done["results"]}
            for (pending, done) in same_turn
        ]

    def find_crashed(self) -> list[dict]:
        """Pending orfani: senza done ne' undone."""
        ops = self._aggregate_ops()
        return [
            op["pending"]
            for op in ops.values()
            if "pending" in op and "done" not in op and "undone" not in op
        ]

    def open_ops_for_executor(self, executor_name: str) -> list[dict]:
        """Op `done` non `undone` di un executor. Per notifiche deprecation:
        identifica utenti/canali che hanno op aperte e potrebbero perdere undo."""
        ops = self._aggregate_ops()
        out = []
        for op in ops.values():
            p = op.get("pending")
            if not p or p.get("executor") != executor_name:
                continue
            if "done" in op and "undone" not in op:
                out.append({
                    "op_id": p["op_id"],
                    "turn_id": p["turn_id"],
                    "ts": p["ts"],
                    "actor": p.get("actor", "host"),
                    "channel": p.get("channel", ""),
                    "executor": executor_name,
                })
        return out

    def purge_older_than(self, days: int = 30) -> int:
        """Riscrive il log scartando i record con ts < now - days*86400.
        Ritorna il numero di record purgati."""
        cutoff = time.time() - days * 86400
        kept: list[str] = []
        purged = 0
        if not self.path.exists():
            return 0
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    kept.append(line)  # preserve corrupted-but-not-our-fault
                    continue
                if rec.get("ts", 0) < cutoff:
                    purged += 1
                else:
                    kept.append(line)
        if purged:
            tmp = self.path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for line in kept:
                    f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        return purged
