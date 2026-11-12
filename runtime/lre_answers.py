# SPDX-License-Identifier: MIT
"""The answer of a long job goes back to who asked for it.

A request that needs a long tool is handed to LRE and its turn ends at once
with «the job is registered». The result used to stay inside the job: a search
never gave its list back and the person had to go and look for it. This module
remembers who asked (conversation, turn, words) and, when the job ends, writes
the answer as a turn linked to the request, in the same conversation, and
puts it in front of the person where they are: in the page they are using,
otherwise in their Telegram chat. Nothing points them to an answer kept
somewhere else (Roberto, 12/11/2026: a notice said «the answer is in the
conversation» and no conversation of his showed it).

The durable store keeps no request text on purpose. What is remembered here
lives beside the turn log, belongs to the same owner and has the same life.
A job nobody asked for in a conversation (a scheduled one) has no entry here
and therefore no answer to deliver: its outcome is told by the LRE notice.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

from logging_setup import get_logger

log = get_logger(__name__)

# Waiting for the end; told once that it stopped and still waiting; settled.
WAITING, ATTENTION, DONE = "waiting", "attention", "done"
_OPEN_STATES = (WAITING, ATTENTION)
# What a settled entry is kept for: naming the job in a late notice.
_KEEP_SETTLED_S = 30 * 24 * 3600
# An answer that cannot be composed is tried again a few times, not forever.
_MAX_FAILED_TRIES = 5
# An answer pushed to a chat is bounded: a long table would arrive as dozens
# of messages. The whole answer stays in the turn log.
_MAX_PUSHED_CHAT_CHARS = 3500
# How long after their last request in the web chat a person is taken to be
# still at that page. A tab left open on a computer nobody is sitting at
# must not keep the answer to itself. Within what the service remembers of a
# closed request (`turn_events._TURN_EVENT_TTL_S`).
_AT_THE_PAGE_S = 300
_MAX_QUERY = 600
_MAX_TEMPLATE = 4000
_MAX_ARGS_JSON = 64 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS origins (
    workload_id     TEXT PRIMARY KEY,
    owner_user_id   TEXT NOT NULL,
    channel         TEXT NOT NULL,
    actor           TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    turn_id         TEXT NOT NULL,
    query           TEXT NOT NULL,
    tool            TEXT NOT NULL,
    args_json       TEXT NOT NULL,
    final_template  TEXT NOT NULL,
    target_device   TEXT NOT NULL,
    state           TEXT NOT NULL,
    answer_turn_id  TEXT NOT NULL DEFAULT '',
    failed_tries    INTEGER NOT NULL DEFAULT 0,
    expects_answer  INTEGER NOT NULL DEFAULT 1,
    intent_verb     TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    settled_at      REAL
);
CREATE INDEX IF NOT EXISTS origins_open ON origins(state, created_at);
"""


def _path() -> Path:
    import config

    return Path(config.DB_LRE_ANSWERS)


@contextlib.contextmanager
def _open():
    """One short transaction on the origin store: committed, then closed."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.executescript(_SCHEMA)
        with connection:
            yield connection
    finally:
        connection.close()


def remember(workload_id: str, *, owner_user_id: str, channel: str, actor: str,
             conversation_id: str, turn_id: str, query: str, tool: str,
             args: dict, final_template: str = "", target_device: str = "",
             intent_verb: str = "", expects_answer: bool = True) -> bool:
    """Record who asked for the job. The same request submitted twice is the
    same job: the first origin stays. Never raises: a job already admitted
    must not be reported as failed because its origin was not written.

    ``expects_answer`` is false for a job with a plan of its own: it has no
    single result to give back, so it is kept only to name it in its notice.
    """
    try:
        encoded = json.dumps(args or {}, ensure_ascii=False, default=str)
        if (not workload_id or not owner_user_id or not channel
                or len(encoded) > _MAX_ARGS_JSON):
            return False
        now = time.time()
        with _open() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO origins(workload_id, owner_user_id, channel, "
                "actor, conversation_id, turn_id, query, tool, args_json, "
                "final_template, target_device, state, expects_answer, created_at, "
                "settled_at, intent_verb) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (workload_id, owner_user_id, channel, actor or "host",
                 conversation_id or "", turn_id or "", str(query or "")[:_MAX_QUERY],
                 tool, encoded, str(final_template or "")[:_MAX_TEMPLATE],
                 target_device or "", WAITING if expects_answer else DONE,
                 1 if expects_answer else 0, now, None if expects_answer else now,
                 str(intent_verb or "")[:40]))
            return cursor.rowcount == 1
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        log.warning("lre_answers: origin of %s not recorded: %r", workload_id[:16], exc)
        return False


def remember_request(
        admitted, framework, *, catalog_by_name, owner_user_id: str, channel: str,
        actor: str, conversation_id: str, turn_id: str, query: str,
        target_device: str, intent_verb: str = "") -> None:
    """Tie a long job to the conversation that asked for it.

    Its notice can then name it with the person's own words, and a plain
    tool's result comes back as the answer. A job with a plan of its own
    (several stages) has no single answer: it is remembered for its notice
    only, and nothing is promised. A scheduled job has no conversation.
    """
    if (not isinstance(admitted, dict) or admitted.get("ok") is not True
            or not admitted.get("workload_id") or not owner_user_id
            or channel not in {"http", "telegram"}):
        return
    steps = [step for step in (getattr(framework, "steps", ()) or ())
             if str(getattr(step, "tool", "") or "") != "final_answer"]
    if len(steps) != 1:
        return
    tool = str(getattr(steps[0], "tool", "") or "")
    executor = catalog_by_name.get(tool)
    plain_tool = executor is not None and not getattr(executor, "lre_plan", "")
    recorded = remember(
        str(admitted["workload_id"]), owner_user_id=owner_user_id,
        channel=channel, actor=actor, conversation_id=conversation_id,
        turn_id=turn_id, query=query, tool=tool,
        args=dict(getattr(steps[0], "args", None) or {}),
        final_template=str(getattr(framework, "final_message", "") or ""),
        target_device=target_device, intent_verb=intent_verb,
        expects_answer=plain_tool)
    if recorded and plain_tool:
        from messages import get as message

        admitted["final_message_hint"] = " ".join(part for part in (
            str(admitted.get("final_message_hint") or ""),
            message("MSG_LRE_ANSWER_WILL_FOLLOW")) if part)


def origin(workload_id: str, *, owner_user_id: str) -> dict | None:
    """Who asked for this job, for the owner only; None for a scheduled one."""
    try:
        if not _path().exists():
            return None
        with _open() as connection:
            row = connection.execute(
                "SELECT * FROM origins WHERE workload_id=? AND owner_user_id=?",
                (workload_id, owner_user_id)).fetchone()
    except (sqlite3.Error, OSError) as exc:
        log.warning("lre_answers: origin not readable: %r", exc)
        return None
    return dict(row) if row is not None else None


def _open_origins(limit: int) -> list[dict]:
    if not _path().exists():
        return []
    with _open() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM origins WHERE state IN (?,?) ORDER BY created_at LIMIT ?",
            (*_OPEN_STATES, limit))]


def _mark(workload_id: str, state: str, *, answer_turn_id: str = "") -> None:
    with _open() as connection:
        connection.execute(
            "UPDATE origins SET state=?, answer_turn_id=CASE WHEN ?='' THEN "
            "answer_turn_id ELSE ? END, settled_at=CASE WHEN ?=? THEN ? ELSE "
            "settled_at END WHERE workload_id=?",
            (state, answer_turn_id, answer_turn_id, state, DONE, time.time(),
             workload_id))


def _count_failure(entry: dict) -> bool:
    """One more failed try; True when it was the last one allowed."""
    tries = int(entry.get("failed_tries") or 0) + 1
    with _open() as connection:
        connection.execute(
            "UPDATE origins SET failed_tries=? WHERE workload_id=?",
            (tries, entry["workload_id"]))
    if tries < _MAX_FAILED_TRIES:
        return False
    log.error("lre_answers: answer of %s given up after %d tries",
              entry["workload_id"][:16], tries)
    _mark(entry["workload_id"], DONE)
    return True


def purge_owner(owner_user_id: str) -> int:
    """Forget every origin of one person (their memory is being erased)."""
    if not owner_user_id or not _path().exists():
        return 0
    with _open() as connection:
        return connection.execute(
            "DELETE FROM origins WHERE owner_user_id=?", (owner_user_id,)).rowcount


def _prune(now: float) -> None:
    with _open() as connection:
        # A job left stopped for a month is not waited for any longer.
        connection.execute(
            "UPDATE origins SET state=?, settled_at=? WHERE state IN (?,?) "
            "AND created_at<?", (DONE, now, *_OPEN_STATES, now - _KEEP_SETTLED_S))
        # A settled entry is kept as long as a late notice may need its name.
        connection.execute(
            "DELETE FROM origins WHERE state=? AND settled_at IS NOT NULL "
            "AND settled_at<?", (DONE, now - _KEEP_SETTLED_S))


# --- the answer -----------------------------------------------------------------

def _answer_text(entry: dict, result: dict) -> str:
    """The words the chat would have used, from the tool's own result.

    The engine composes the final message from the step it is given as
    already observed and from the template the planner wrote for this very
    request; nothing is run again."""
    from engine.executor import Executor
    from engine.types import Framework, StepRun

    def _nothing_runs(tool, args):
        raise RuntimeError(f"an answer never runs a tool again: {tool}")

    try:
        args = json.loads(entry["args_json"] or "{}")
    except ValueError:
        args = {}
    seed = StepRun(step_idx=1, tool=entry["tool"], args=args, result=result,
                   ok=True, latency_ms=0, kind="input")
    run = Executor(invoke_executor=_nothing_runs, seed_steps=[seed], catalog=[]).run(
        Framework(steps=[], final_message=entry["final_template"] or ""),
        query=entry["query"],
        runtime_ctx={
            "turn_id": "", "actor": entry["actor"], "channel": entry["channel"],
            "owner_user_id": entry["owner_user_id"],
            "user_query_raw": entry["query"],
            "conversation_id": entry["conversation_id"],
            "target_device": entry["target_device"],
        })
    return str(run.final_text or "")


def _write_turn(entry: dict, *, text: str, kind: str, result: dict | None,
                lost: str = ""):
    """The answer as a turn of its own, linked to the request that asked.

    ``lost`` says how many items the job could not handle. The turn writer
    declares them itself for the tools the chat knows how to speak about
    (its own rule, the same as in a live turn); only when it would stay
    silent is the job's count added, so the loss is said once, never twice
    and never left out."""
    from agent_runtime import StepLog, TurnLog, _apply_device_tag

    steps = []
    if result is not None:
        try:
            args = json.loads(entry["args_json"] or "{}")
        except ValueError:
            args = {}
        steps.append(StepLog(step_num=1, chosen_tool=entry["tool"],
                             resolved_args=args, result=dict(result)))
    now = time.time()
    turn = TurnLog(
        ts_start=now, ts_end=now, turn_id=uuid.uuid4().hex[:16],
        parent_turn_id=entry["turn_id"], user_query=entry["query"],
        actor=entry["actor"] or "host", channel=entry["channel"],
        owner_user_id=entry["owner_user_id"],
        conversation_id=entry["conversation_id"],
        mode="continuation", steps=steps, final_kind=kind,
        final_message=text,
        # The tool's own presentation is chosen by what was asked.
        intent_verb=entry.get("intent_verb") or "",
    )
    if entry["target_device"]:
        turn.target_device = entry["target_device"]
    if lost and not turn._collect_failure_notices():
        turn.final_message = "\n\n".join(part for part in (text, lost) if part)
    _apply_device_tag(turn)
    turn.write()
    return turn


def _bounded_for_chat(text: str) -> str:
    """The beginning of a long answer, cut at a line, with the cut declared."""
    if len(text) <= _MAX_PUSHED_CHAT_CHARS:
        return text
    from messages import get as message

    head = text[:_MAX_PUSHED_CHAT_CHARS]
    head = head[:head.rfind("\n")] if "\n" in head else head
    return "\n\n".join((head.rstrip(), message(
        "MSG_LRE_ANSWER_CUT", shown=len(head.rstrip()), total=len(text))))


def _show_in_open_pages(entry: dict, completion) -> bool:
    """Show it in every open page of the person; False when there is none."""
    import active_sessions

    if not active_sessions.listening_pages(entry["owner_user_id"]):
        return False
    from concurrent.futures import Future

    from orchestration import _consegna_esito_tardivo
    from turn_events import TurnEventLog

    settled: Future = Future()
    settled.set_result(completion)
    _consegna_esito_tardivo(
        settled, channel="http", owner_user_id=entry["owner_user_id"],
        executor=entry["tool"], loop=TurnEventLog.get().bound_loop())
    return True


def _at_the_page(owner_user_id: str) -> bool:
    import person_activity

    idle = person_activity.seconds_since_web_request(owner_user_id)
    return idle is not None and idle < _AT_THE_PAGE_S


def _send_to_telegram(entry: dict, text: str) -> str:
    """"sent", "deferred" to the person's next message there, or "unpaired"."""
    from channels import OutboundMessage
    from channels.telegram import TelegramChannel
    from durable_workloads.events import resolve_telegram_recipient

    try:
        # Resolved now: the chat may have been unpaired while the job ran.
        recipient = resolve_telegram_recipient(entry["owner_user_id"])
        if not recipient:
            return "unpaired"
        reply = TelegramChannel().send(recipient, OutboundMessage(text=text))
        if not (isinstance(reply, dict) and reply.get("ok") is False):
            return "sent"
    except Exception as exc:  # noqa: BLE001 - the answer must not be lost
        log.warning("lre_answers: telegram send failed: %r", exc)
    # Not lost: it opens the person's next message in that chat.
    import user_notices

    user_notices.append("telegram", entry["actor"], text,
                        owner_user_id=entry["owner_user_id"])
    return "deferred"


def _push(entry: dict, turn, *, title: str = "") -> str:
    """Put what was written in front of the person, where they are now.

    A request made in the web chat is shown in the person's open pages. It
    also goes to their Telegram chat unless they are at the page: asking
    them to come back and look is what fails (the same reason a question
    that waits for the person is pushed). A request made on Telegram is
    answered there. ``title`` heads the Telegram text when the text does not
    name the job by itself: it may arrive long after the request, or in a
    chat where the request was never written.
    """
    from orchestration import _completion_from_turnlog

    completion = _completion_from_turnlog(turn)
    asked_on_the_web = entry["channel"] == "http"
    in_page = False
    if asked_on_the_web:
        try:
            in_page = _show_in_open_pages(entry, completion)
        except Exception:  # noqa: BLE001 - then it goes the other way
            log.exception("lre_answers: open pages of %s not reached",
                          entry["workload_id"][:16])
    if in_page and _at_the_page(entry["owner_user_id"]):
        return "page"
    if not asked_on_the_web and entry["channel"] != "telegram":
        return "recorded"
    told = _send_to_telegram(entry, "\n\n".join(
        part for part in (title, _bounded_for_chat(completion.text)) if part))
    if told == "sent":
        return "telegram"
    if in_page:
        return "page"
    if entry["channel"] == "telegram":
        return "telegram_" + told
    # Asked in the web chat, no page open, no Telegram chat to write to: the
    # answer waits in the conversation, where the page shows it when opened.
    return "recorded"


def _tell_outcome(entry: dict, store, state) -> str:
    """Say how the job ended, when there is no answer of its own to give."""
    from durable_workloads.events import ended_notice
    from durable_workloads.models import WorkloadState

    text = ended_notice(store, entry["owner_user_id"], entry["workload_id"],
                        state=state, origin=entry)
    stopped = state in (WorkloadState.NEEDS_ATTENTION, WorkloadState.FAILED)
    turn = _write_turn(entry, text=text, kind="error" if stopped else "answer",
                       result=None)
    # In attention the job may still be resumed and end: the answer is due.
    _mark(entry["workload_id"],
          ATTENTION if state is WorkloadState.NEEDS_ATTENTION else DONE,
          answer_turn_id=turn.turn_id)
    return _push(entry, turn)


def _deliver_one(entry: dict, store) -> str:
    from durable_workloads.events import lost_items_note, request_title
    from durable_workloads.models import WorkloadState

    try:
        state = store.get_workload(entry["owner_user_id"], entry["workload_id"]).state
    except Exception as exc:  # noqa: BLE001 - a job removed from the store has no answer
        log.info("lre_answers: job %s is gone: %r", entry["workload_id"][:16], exc)
        _mark(entry["workload_id"], DONE)
        return "gone"
    if state in (WorkloadState.COMPLETED, WorkloadState.COMPLETED_WITH_ERRORS):
        results = store.committed_tool_results(
            entry["owner_user_id"], entry["workload_id"], tool=entry["tool"])
        if len(results) != 1:
            # Not the single answer of a plain tool. Its notice was silenced
            # because an answer was due: how it ended is said all the same.
            return _tell_outcome(entry, store, state)
        text = _answer_text(entry, results[0])
        # Partly done is done, with the rest declared.
        lost = (lost_items_note(store, entry["owner_user_id"], entry["workload_id"])
                if state is WorkloadState.COMPLETED_WITH_ERRORS else "")
        turn = _write_turn(entry, text=text, kind="answer", result=results[0],
                           lost=lost)
        _mark(entry["workload_id"], DONE, answer_turn_id=turn.turn_id)
        return _push(entry, turn, title=request_title(entry))
    if state is WorkloadState.CANCELLED:
        # Stopped by the person in the page: they know.
        _mark(entry["workload_id"], DONE)
        return "cancelled"
    stopped = state in (WorkloadState.NEEDS_ATTENTION, WorkloadState.FAILED)
    if not stopped or (state is WorkloadState.NEEDS_ATTENTION
                       and entry["state"] == ATTENTION):
        return "working"
    return _tell_outcome(entry, store, state)


def _tell_given_up(entry: dict, store) -> str:
    """The answer could not be composed: how the job ended is said instead.

    Its notice was silenced because an answer was due; leaving it at that
    would be a job that ends and nobody is told (§2.8)."""
    try:
        state = store.get_workload(entry["owner_user_id"], entry["workload_id"]).state
        return _tell_outcome(entry, store, state)
    except Exception:  # noqa: BLE001 - nothing more can be tried for this one
        log.exception("lre_answers: outcome of %s not told", entry["workload_id"][:16])
        return "given_up"


def deliver_ready(*, limit: int = 20, open_store=None) -> dict:
    """Deliver the answers of the jobs that ended. One pass, bounded.

    Without an answer waiting nothing is opened or read."""
    entries = _open_origins(limit)
    outcome = {"ok": True, "open": len(entries), "delivered": 0, "details": []}
    if not entries:
        return outcome
    if open_store is None:
        from durable_workloads.storage import DurableWorkloadStore

        open_store = DurableWorkloadStore.open
    with open_store() as store:
        for entry in entries:
            try:
                result = _deliver_one(entry, store)
            except Exception as exc:  # noqa: BLE001 - one answer never holds the others
                log.exception("lre_answers: answer of %s failed", entry["workload_id"][:16])
                result = f"error:{type(exc).__name__}"
                if _count_failure(entry):
                    result = _tell_given_up(entry, store)
            if result not in {"working"}:
                outcome["details"].append(
                    {"workload": entry["workload_id"][:16], "result": result})
            if result in {"page", "telegram", "recorded"}:
                outcome["delivered"] += 1
    _prune(time.time())
    return outcome
