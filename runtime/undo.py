"""Undo log per Metnos — append-only, fsync, flock.

Per ogni invocazione di executor con `revertible=true` il runtime scrive:
  pending  prima di invocare    {type, op_id, turn_id, ts, executor, args, plan}
  done     dopo successo        {type, op_id, ts, results}
  undone   dopo undo eseguito   {type, op_id, ts, reverse_results}

`undo_last_turn` cerca i `done` non `undone` della richiesta piu' recente e
chiama in ordine inverso `reverse(plan, results)` esposta dal modulo
dell'executor.

The unit of an undo is the REQUEST (17/11/2026): the turn where the person
asked and every turn that continues it (the rest of a long job before and
after a consent, a turn resumed after a yes in the chat). The code that
starts a continuation declares which request it continues
(`continue_request`); the writer of the pending record reads it
(`request_of`) and records it as `request_turn_id`. A request of one turn
writes no such field and is undone exactly as before.

Crash safety: pending senza done ne' undone => orfano (op crashata in
volo); listato da `find_crashed`. Nessun auto-rollback.

"""
import fcntl
import json
from contextvars import ContextVar
from contextlib import contextmanager
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Iterator

import config as _C  # §7.11

DEFAULT_LOG = _C.PATH_USER_DATA / "undo.jsonl"

PER_EXECUTION_OUTCOME = "per_execution"
UNDO_OUTCOMES = frozenset({"reversible", "no_effect", "irreversible"})


# The request each continuation turn of this process belongs to. Written by
# the code that mints the continuation's turn id, read by the writer of the
# pending records, which already has that turn id: no thread or context has to
# carry it. Bounded: a continuation runs within minutes of being declared.
_REQUEST_OF: "OrderedDict[str, str]" = OrderedDict()
_REQUEST_OF_LOCK = threading.Lock()
_REQUEST_OF_MAX = 4096


def continue_request(turn_id: str, request_turn_id: str) -> str:
    """Declare that the turn ``turn_id`` continues the request made in the
    turn ``request_turn_id``; return the turn of the request itself.

    A continuation of a continuation belongs to the first request: when
    ``request_turn_id`` is itself a declared continuation, its request is
    used. Every effect recorded under ``turn_id`` from now on is undone
    together with the request's own effects.
    """
    turn_id, request_turn_id = str(turn_id or ""), str(request_turn_id or "")
    if not turn_id or not request_turn_id or turn_id == request_turn_id:
        return request_turn_id or turn_id
    with _REQUEST_OF_LOCK:
        root = _REQUEST_OF.get(request_turn_id) or request_turn_id
        _REQUEST_OF[turn_id] = root
        _REQUEST_OF.move_to_end(turn_id)
        while len(_REQUEST_OF) > _REQUEST_OF_MAX:
            _REQUEST_OF.popitem(last=False)
    return root


def request_of(turn_id: str) -> str:
    """The turn of the request ``turn_id`` continues, or "" for a turn that
    is a request of its own."""
    with _REQUEST_OF_LOCK:
        return _REQUEST_OF.get(str(turn_id or ""), "")


def _request_unit(pending: dict) -> str:
    """The request an operation belongs to: its declared request, otherwise
    its own turn."""
    return str(pending.get("request_turn_id") or pending.get("turn_id") or "")


# A tool that stops to ask the person (data, a consent) has done nothing yet.
_ASKING_DECISIONS = frozenset({
    "needs_inputs", "input_required", "approval_required", "needs_confirmation",
})


def _mutated_result(results: object) -> bool:
    """Return whether a legacy unconditional receipt reports an effect."""
    if not isinstance(results, dict):
        return False
    # ``ok`` on a request for data means "asked", not "done": recording it as
    # done offered an undo with nothing to reverse (seen on 13/11/2026, a
    # contact creation that had only opened the consent form). Work already
    # done before the question still counts.
    if (results.get("decision") in _ASKING_DECISIONS
            and not results.get("ok_count") and not results.get("results")):
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


_ROWS = ContextVar("undo_rows", default=None)


def request_of_workload(workload_id, *, operations=None):
    """The request recorded before admission, shared by HTTP and worker processes."""
    if not workload_id:
        return ""
    operations = operations if operations is not None else UndoLog()._aggregate_ops()
    return _request_unit(operations.get(workload_id + "-boundary", {}).get("pending", {}))


@contextmanager
def row_execution(context, executor, args, store):
    """One job lock is also the barrier used before reversing its request."""
    import hashlib
    import lre_answers
    journal = UndoLog()
    lock = journal.path.with_name(journal.path.name + "." + context.workload_id + ".lock")
    with lock.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        if store.get_workload(context.owner_user_id, context.workload_id).state.value in {"cancel_requested", "cancelled"}:
            raise RuntimeError("durable job cancelled")
        op_id = hashlib.sha256((context.workload_id + ":" + context.unit_key).encode()).hexdigest()
        operations = journal._aggregate_ops()
        old = operations.get(op_id, {}).get("pending")
        request = request_of_workload(context.workload_id, operations=operations)
        origin = lre_answers.origin(context.workload_id, owner_user_id=context.owner_user_id)
        if not origin or not request:
            raise ValueError("durable request origin missing")
        pending = old or {"op_id": op_id, "turn_id": "durable-" + context.unit_key,
            "executor": executor.name, "args": args, "actor": origin.get("actor", context.owner_user_id),
            "channel": origin.get("channel", "durable_workload"),
            "request_turn_id": request,
            "plan": {"workload_id": context.workload_id, "owner": context.owner_user_id, "rows": {}}}
        checkpoint_pending(pending, journal)
        token = _ROWS.set(pending)
        try:
            yield pending
        finally:
            _ROWS.reset(token)


def checkpoint_pending(pending, journal=None):
    (journal or UndoLog()).append_pending(**{key: value for key, value in pending.items()
        if key in {"op_id", "turn_id", "executor", "args", "plan", "actor", "channel", "request_turn_id"}})


def row_checkpoint(row):
    from server_filesystem import MAX_ITEMS
    pending = _ROWS.get()
    if pending is None or not isinstance(row, dict) or set(row) != {"index", "item", "evidence", "pattern"}:
        raise ValueError("unexpected undo checkpoint")
    index = row["index"]
    if type(index) is not int or not 0 <= index < MAX_ITEMS:
        raise ValueError("unexpected undo row")
    pending["plan"]["rows"][str(index)] = row
    checkpoint_pending(pending)


def remember_rows(workload_id, owner, executor, context):
    UndoLog().append_pending(workload_id + "-boundary", context["turn_id"], executor.name, {},
        {"workload_id": workload_id, "owner": owner, "rows": {}}, actor=context["actor"],
        channel=context["channel"], request_turn_id=request_of(context["turn_id"]))


def stop_rows(journal, pending):
    """Cancel before draining the same lock every mutating unit must hold."""
    from durable_workloads.storage import DurableWorkloadStore
    from messages import get
    import server_filesystem as files
    plan = pending["plan"]
    job, owner = plan["workload_id"], plan["owner"]
    with DurableWorkloadStore.open() as store:
        current = store.get_workload(owner, job)
        if current.state.value not in {"completed", "completed_with_errors", "failed", "cancelled", "cancel_requested"}:
            store.request_cancel(owner, job, expected_version=current.version, idempotency_key="undo-" + job)
        with journal.path.with_name(journal.path.name + "." + job + ".lock").open("a") as held:
            while True:
                try:
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if files.undo_seconds_remaining() <= 0:
                        raise TimeoutError(get("ERR_UNDO_ROWS_DRAIN"))
                    time.sleep(min(0.05, files.undo_seconds_remaining()))
            for op in journal._aggregate_ops().values():
                row = op.get("pending", {})
                proof = row.get("plan", {})
                if proof.get("workload_id") != job or any(key in op for key in ("done", "closed", "undone")):
                    continue
                if proof.get("rows"):
                    result = files.execute(owner, proof["filesystem"], {**row["args"],
                        "_row_journal": proof["rows"], "_row_observe": True,
                        "_row_pattern": next(iter(proof["rows"].values()))["pattern"]}, row["turn_id"],
                        timeout_s=files.undo_seconds_remaining())
                    if not result.get("ok"):
                        raise RuntimeError(get("ERR_UNDO_ROWS_UNCERTAIN", done=result.get("ok_count", 0),
                            uncertain=result.get("fail_count", len(proof["rows"]))))
                    journal.append_completion(row["op_id"], result)
                else:
                    journal.append_closed(row["op_id"], "no_effect")


class UndoLog:
    def __init__(self, path: Path | None = None):
        self.path = Path(DEFAULT_LOG if path is None else path)
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
                       device: str = "", outcome_contract: str = "",
                       request_turn_id: str = "") -> None:
        # `device`: id del device remoto quando l'op ha girato LI' (C7 CP4) —
        # l'undo deve ribaltare sullo STESSO host, mai sul server (§2.9).
        # `request_turn_id`: the turn of the request a continuation turn
        # belongs to; absent for a turn that is a request of its own.
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
            **({"request_turn_id": request_turn_id}
               if request_turn_id and request_turn_id != turn_id else {}),
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
        stages = reverse_results.get("stages") or []
        progress = {stage["pattern"]: stage["result"]["_undo_progress"]
                    for stage in stages if isinstance(stage.get("result"), dict)
                    and "_undo_progress" in stage["result"]}
        pending = bool(reverse_results.get("fail_count")) or any(stage.get("pending_time", 0) for stage in progress.values())
        self._append({"type": "undo_progress" if pending else "undone",
                      "op_id": op_id, "ts": time.time(), "reverse_results": reverse_results,
                      **({"completed": {name: value["completed"] for name, value in progress.items()}}
                         if pending else {})})

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
        """Record `done` non `undone` della richiesta piu' recente.

        The most recently completed operation names the request; every
        reversible operation of that request is returned, whichever of its
        turns ran it (the request's own turn or a continuation, see
        `continue_request`). A request of one turn is its turn, as before.

        Ritorna i pending arricchiti con `results` (campo del done corrispondente),
        in ordine cronologico (dal primo all'ultimo eseguito).

        `actor`: isolamento multi-utente (7/7/2026) — se dato, considera SOLO
        le op emesse da QUEL richiedente (campo `actor` del pending): un guest
        che dice «annulla» NON deve ribaltare l'operazione di un altro utente.
        """
        ops = self._aggregate_ops()
        # An approved job may be between its effect and its durable receipt.
        # Its pending boundary participates in request selection. Stop and
        # reconcile that request before exposing any inverse, never an older
        # request or another person's work.
        candidates = []
        for op in ops.values():
            pending = op.get("pending", {})
            if not pending or actor is not None and (pending.get("actor") or "host") != actor:
                continue
            boundary = (op.get("undone") or op.get("undo_progress") or op.get("closed") or op.get("done")
                        or (pending if pending.get("plan", {}).get("workload_id") else None))
            if boundary:
                candidates.append((boundary["ts"], pending))
        budget_started = False
        if candidates:
            latest = max(candidates, key=lambda item: item[0])[1]
            request = _request_unit(latest)
            relevant = [op["pending"] for op in ops.values() if op.get("pending")
                        and _request_unit(op["pending"]) == request
                        and (op["pending"].get("actor") or "host") == (latest.get("actor") or "host")]
            jobs = {pending["plan"]["workload_id"]: pending for pending in relevant
                    if pending.get("plan", {}).get("workload_id")}
            if jobs:
                from server_filesystem import begin_undo_budget, undo_seconds_remaining
                begin_undo_budget()
                budget_started = True
                for pending in jobs.values():
                    stop_rows(self, pending)
                ops = self._aggregate_ops()
        # A closed no-effect/irreversible operation is a turn boundary even
        # though it has nothing to reverse.  Otherwise "undo" after such an
        # action could unexpectedly reach into an older turn.
        completed_ops = []
        for op in ops.values():
            pending = op.get("pending")
            completion = (op.get("undone") or op.get("undo_progress") or op.get("closed")
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
        latest_pending = completed_ops[-1][0]
        latest_request = _request_unit(latest_pending)
        # Legacy/corrupt records without a turn id are not one giant turn.
        # Treat only the latest operation as the boundary: grouping every
        # empty id would mix unrelated historical effects and could undo far
        # more than the person requested.  Continuations of a request carry
        # the request's turn (`request_turn_id`), so they share its boundary.
        def _same_boundary(pending: dict) -> bool:
            if latest_request:
                return _request_unit(pending) == latest_request
            return pending.get("op_id") == latest_pending.get("op_id")

        same_turn = [
            (pending, done)
            for (pending, _completion, done) in completed_ops
            if _same_boundary(pending) and done is not None
        ]
        same_turn.sort(key=lambda item: item[1]["ts"])
        records = []
        for pending, done in same_turn:
            progress = ops[pending["op_id"]].get("undo_progress", {})
            results = done["results"]
            if progress.get("completed"):
                results = {**results, "_undo_completed": progress["completed"]}
            records.append({**pending, "results": results})
        if not budget_started and any((row.get("results") or {}).get("_filesystem") for row in records):
            from server_filesystem import begin_undo_budget
            begin_undo_budget()
        return records

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
