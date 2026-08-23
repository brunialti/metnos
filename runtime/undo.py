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

PER_EXECUTION_OUTCOME = "per_execution"
UNDO_OUTCOMES = frozenset({"reversible", "no_effect", "irreversible"})


def _mutated_result(results: object) -> bool:
    """Return whether a legacy unconditional receipt reports an effect."""
    if not isinstance(results, dict):
        return False
    return (bool(results.get("ok")) or bool(results.get("ok_count"))
            or bool(results.get("results")))


def execution_undo_outcome(results: object, *, contract: str = "") -> str:
    """Classify one execution without knowing its executor or provider.

    Unconditional reversible manifests retain their historical contract: a
    successful/mutating result is reversible.  Manifests that opt into the
    signed ``per_execution`` contract must emit ``_undo.outcome``.
    """
    if contract != PER_EXECUTION_OUTCOME:
        return "reversible" if _mutated_result(results) else "no_effect"
    metadata = results.get("_undo") if isinstance(results, dict) else None
    outcome = metadata.get("outcome") if isinstance(metadata, dict) else None
    return outcome if outcome in UNDO_OUTCOMES else "invalid"


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

    def append_pending(self, op_id: str, turn_id: str, executor: str, args: dict,
                       plan: dict, actor: str = "host", channel: str = "",
                       device: str = "", outcome_contract: str = "") -> None:
        # `device`: id del device remoto quando l'op ha girato LI' (C7 CP4) —
        # l'undo deve ribaltare sullo STESSO host, mai sul server (§2.9).
        self._append({
            "type": "pending",
            "device": device,
            "op_id": op_id,
            "turn_id": turn_id,
            "ts": time.time(),
            "actor": actor,           # 'host' | 'guest:<id>' — chi ha emesso la richiesta
            "channel": channel,       # 'telegram:<chat_id>' | 'terminal' — per indirizzare notifiche
            "executor": executor,
            "args": args,
            "plan": plan,
            **({"outcome_contract": outcome_contract}
               if outcome_contract else {}),
        })

    def append_done(self, op_id: str, results: dict) -> None:
        self._append({"type": "done", "op_id": op_id, "ts": time.time(), "results": results})

    def append_closed(self, op_id: str, outcome: str, *, reason: str = "") -> None:
        """Close a pending operation that has no reversible receipt."""
        if outcome not in {"no_effect", "irreversible"}:
            raise ValueError("invalid closed undo outcome")
        self._append({
            "type": "closed", "op_id": op_id, "ts": time.time(),
            "outcome": outcome,
            **({"reason": reason} if reason else {}),
        })

    def append_completion(self, op_id: str, results: dict, *,
                          outcome_contract: str = "") -> str:
        """Close one pending operation according to its signed contract."""
        outcome = execution_undo_outcome(results, contract=outcome_contract)
        if outcome == "reversible":
            self.append_done(op_id, results)
        elif outcome in {"no_effect", "irreversible"}:
            self.append_closed(op_id, outcome)
        else:
            # A conditional mutation without a valid receipt cannot be
            # offered as reversible.  Close it honestly instead of leaving a
            # false crash marker or guessing an inverse from arguments.
            self.append_closed(
                op_id, "irreversible", reason="invalid_execution_receipt")
        return outcome

    def close_pending_for_turn(self, turn_id: str, results: dict,
                               device: str | None = None) -> int:
        """Chiude (append_done) i pending ANCORA aperti (senza done/undone) del
        turno `turn_id`. Usato dall'A.0 (risultato-tardivo, fase 7): un'op remota
        di cui il turno aveva perso la conferma (timeout) ma che il device
        completa PIÙ TARDI → il pending era orfano e l'op non annullabile (§2.8).
        Alla submit tardiva si chiude qui con l'esito reale, ripristinando
        l'annullabilità. `device` (se dato) restringe il match. Ritorna il numero
        di record chiusi. Idempotente: salta i pending già `done`."""
        if not turn_id:
            return 0
        closed = 0
        for op_id, op in self._aggregate_ops().items():
            p = op.get("pending")
            if (not p or "done" in op or "closed" in op
                    or "undone" in op):
                continue
            if p.get("turn_id") != turn_id:
                continue
            if device is not None and (p.get("device") or "") != device:
                continue
            self.append_completion(
                op_id, results,
                outcome_contract=str(p.get("outcome_contract") or ""),
            )
            closed += 1
        return closed

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

    def latest_turn_done(self, actor: str | None = None) -> list[dict]:
        """Record `done` non `undone` del turno piu' recente con almeno un done.

        Ritorna i pending arricchiti con `results` (campo del done corrispondente),
        in ordine cronologico (dal primo all'ultimo eseguito).

        `actor`: isolamento multi-utente (7/7/2026) — se dato, considera SOLO
        le op emesse da QUEL richiedente (campo `actor` del pending): un guest
        che dice «annulla» NON deve ribaltare l'operazione di un altro utente.
        """
        ops = self._aggregate_ops()
        # A closed no-effect/irreversible operation is a turn boundary even
        # though it has nothing to reverse.  Otherwise "undo" after such an
        # action could unexpectedly reach into an older turn.
        completed_ops = []
        for op in ops.values():
            pending = op.get("pending")
            completion = (op.get("undone") or op.get("closed")
                          or op.get("done"))
            if pending and completion:
                completed_ops.append((
                    pending, completion,
                    op.get("done") if "undone" not in op else None,
                ))
        if actor is not None:
            completed_ops = [
                (p, completion, done)
                for (p, completion, done) in completed_ops
                if (p.get("actor") or "host") == actor
            ]
        if not completed_ops:
            return []
        completed_ops.sort(key=lambda item: item[1]["ts"])
        latest_turn_id = completed_ops[-1][0]["turn_id"]
        same_turn = [
            (pending, done)
            for (pending, _completion, done) in completed_ops
            if pending["turn_id"] == latest_turn_id and done is not None
        ]
        same_turn.sort(key=lambda item: item[1]["ts"])
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
            if ("pending" in op and "done" not in op
                and "closed" not in op and "undone" not in op)
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
