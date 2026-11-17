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

A request may ask for more than its long step («find the duplicates and put
them in a sheet»). The plan of the turn is then kept with the origin and,
when the job ends, the whole plan runs through the same engine a chat turn
uses, with the long step answered by the job's own result: the answer is the
answer of the request, not of its first step (Roberto, 15/11/2026; until then
such a request was refused). After a long job nothing is deleted or moved
without asking first, whatever the number of items: the job may end when the
person is not looking.
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

# Waiting for the end; told once that it stopped and still waiting; the rest
# of the request is running; settled.
WAITING, ATTENTION, CONTINUING, DONE = "waiting", "attention", "continuing", "done"
_OPEN_STATES = (WAITING, ATTENTION, CONTINUING)
# The requests whose rest this process is running now. One left «continuing»
# by a process that is gone was interrupted: it is told, never run again.
_AT_WORK: set[str] = set()
# The answer a resumed plan carries when its consent is given.
RESUME_REST = "resume_lre_rest"
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
_MAX_PLAN_JSON = 256 * 1024

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
    settled_at      REAL,
    plan_json       TEXT NOT NULL DEFAULT ''
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
        # A store born before the plan was kept gains its column in place.
        if not any(column["name"] == "plan_json" for column in
                   connection.execute("PRAGMA table_info(origins)")):
            connection.execute(
                "ALTER TABLE origins ADD COLUMN plan_json TEXT NOT NULL DEFAULT ''")
        with connection:
            yield connection
    finally:
        connection.close()


def remember(workload_id: str, *, owner_user_id: str, channel: str, actor: str,
             conversation_id: str, turn_id: str, query: str, tool: str,
             args: dict, final_template: str = "", target_device: str = "",
             intent_verb: str = "", expects_answer: bool = True,
             plan: dict | None = None) -> bool:
    """Record who asked for the job. The same request submitted twice is the
    same job: the first origin stays. Never raises: a job already admitted
    must not be reported as failed because its origin was not written.

    ``expects_answer`` is false for a job with a plan of its own: it has no
    single result to give back, so it is kept only to name it in its notice.
    ``plan`` is the plan of the turn when the request asks for more than its
    long step: the rest runs when the job ends.
    """
    try:
        encoded = json.dumps(args or {}, ensure_ascii=False, default=str)
        kept = json.dumps(plan, ensure_ascii=False, default=str) if plan else ""
        if (not workload_id or not owner_user_id or not channel
                or len(encoded) > _MAX_ARGS_JSON or len(kept) > _MAX_PLAN_JSON):
            return False
        now = time.time()
        with _open() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO origins(workload_id, owner_user_id, channel, "
                "actor, conversation_id, turn_id, query, tool, args_json, "
                "final_template, target_device, state, expects_answer, created_at, "
                "settled_at, intent_verb, plan_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (workload_id, owner_user_id, channel, actor or "host",
                 conversation_id or "", turn_id or "", str(query or "")[:_MAX_QUERY],
                 tool, encoded, str(final_template or "")[:_MAX_TEMPLATE],
                 target_device or "", WAITING if expects_answer else DONE,
                 1 if expects_answer else 0, now, None if expects_answer else now,
                 str(intent_verb or "")[:40], kept))
            return cursor.rowcount == 1
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        log.warning("lre_answers: origin of %s not recorded: %r", workload_id[:16], exc)
        return False


def rest_can_follow(*, owner_user_id: str, channel: str) -> bool:
    """A conversation waits for this request: its answer has where to go, so
    the steps after a long one can run when the job ends."""
    return bool(owner_user_id) and channel in {"http", "telegram"}


def remember_request(
        admitted, framework, *, catalog_by_name, owner_user_id: str, channel: str,
        actor: str, conversation_id: str, turn_id: str, query: str,
        target_device: str, intent_verb: str = "") -> None:
    """Tie a long job to the conversation that asked for it.

    Its notice can then name it with the person's own words, and a plain
    tool's result comes back as the answer. A request that asks for more than
    its long step keeps the plan of the turn: the rest runs when the job
    ends. A job with a plan of its own (several stages) has no single
    answer: it is remembered for its notice only, and nothing is promised. A
    scheduled job has no conversation.
    """
    if (not isinstance(admitted, dict) or admitted.get("ok") is not True
            or not admitted.get("workload_id")
            or not rest_can_follow(owner_user_id=owner_user_id, channel=channel)):
        return
    from durable_workloads.direct_invocation import is_intrinsically_long

    def _handed_over(step) -> bool:
        found = catalog_by_name.get(str(getattr(step, "tool", "") or ""))
        return found is not None and bool(
            is_intrinsically_long(found) or getattr(found, "lre_plan", ""))

    steps = [step for step in (getattr(framework, "steps", ()) or ())
             if str(getattr(step, "tool", "") or "") != "final_answer"]
    handed = steps if len(steps) == 1 else [
        step for step in steps if _handed_over(step)]
    if len(handed) != 1:
        return
    tool = str(getattr(handed[0], "tool", "") or "")
    executor = catalog_by_name.get(tool)
    plain_tool = executor is not None and not getattr(executor, "lre_plan", "")
    rest = len(steps) > 1 or bool(getattr(framework, "fillers", None))
    workload_id = str(admitted["workload_id"])
    recorded = remember(
        workload_id, owner_user_id=owner_user_id,
        channel=channel, actor=actor, conversation_id=conversation_id,
        turn_id=turn_id, query=query, tool=tool,
        args=dict(getattr(handed[0], "args", None) or {}),
        final_template=str(getattr(framework, "final_message", "") or ""),
        target_device=target_device, intent_verb=intent_verb,
        expects_answer=plain_tool,
        plan=framework.to_dict() if rest and plain_tool else None)
    from messages import get as message

    said = ""
    if rest:
        # The same request delivered twice is the same job: its plan is kept.
        kept = recorded or bool(
            (origin(workload_id, owner_user_id=owner_user_id) or {}).get("plan_json"))
        said = message("MSG_LRE_REST_WILL_FOLLOW" if kept else "MSG_LRE_REST_NOT_KEPT")
    elif recorded and plain_tool:
        said = message("MSG_LRE_ANSWER_WILL_FOLLOW")
    if said:
        admitted["final_message_hint"] = " ".join(part for part in (
            str(admitted.get("final_message_hint") or ""), said) if part)


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
            "SELECT * FROM origins WHERE state IN (?,?,?) ORDER BY created_at LIMIT ?",
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
            "UPDATE origins SET state=?, settled_at=? WHERE state IN (?,?,?) "
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
                lost: str = "", run=None, turn_id: str = "", parent: str = "",
                started: float | None = None):
    """The answer as a turn of its own, linked to the request that asked.

    ``run`` is the rest of the request as the engine ran it: every step it
    observed is recorded, the long one with the job's result.

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
    if run is not None:
        for observed in run.steps:
            steps.append(StepLog(
                step_num=len(steps) + 1, chosen_tool=observed.tool,
                resolved_args=dict(observed.args or {}),
                result=(dict(observed.result)
                        if isinstance(observed.result, dict) else {}),
                exec_ms=observed.latency_ms,
                execution_effect=getattr(observed, "execution_effect", None)))
    now = time.time()
    last = steps[-1].result if steps and isinstance(steps[-1].result, dict) else {}
    turn = TurnLog(
        ts_start=started or now, ts_end=now,
        turn_id=turn_id or uuid.uuid4().hex[:16],
        parent_turn_id=parent or entry["turn_id"], user_query=entry["query"],
        actor=entry["actor"] or "host", channel=entry["channel"],
        owner_user_id=entry["owner_user_id"],
        conversation_id=entry["conversation_id"],
        mode="continuation", steps=steps, final_kind=kind,
        final_message=text,
        error_class=(str(last.get("error_class") or "")
                     if run is not None and kind == "error" else ""),
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


# --- the rest of the request ---------------------------------------------------

def _fill(system, user, *, max_tokens=80, **_unused) -> str:
    """A short text the plan still has to compose, on the tier a chat turn
    uses for it; without the model the plan's own default stands."""
    try:
        from llm_router import LLMRouter
        from llm_workloads import tier_for

        reply = LLMRouter().provider(tier_for("intent.extract")).chat(
            system, user, max_tokens=max_tokens)
        return str(getattr(reply, "text", reply) or "").strip()
    except Exception as exc:  # noqa: BLE001 - the default of the plan is used
        log.warning("lre_answers: filler not composed: %r", exc)
        return ""


def _ask_before_removing(framework, entry: dict, context: dict, catalog=None):
    """After a long job nothing is deleted or moved without asking first.

    In a chat turn a few items are removed without a question, because the
    person has just asked and is looking. A job may end hours later: the
    question is asked whatever the number, with the number in it (Roberto,
    15/11/2026). The gate the chat already uses is kept; only its threshold
    and its words change."""
    from engine.dispatch import (
        _MASS_ACTION_KEY, _MASS_MUTATION_VERBS, _insert_mass_mutation_gate,
        _what_the_question_adds,
    )
    from messages import get as message

    def _words(verb, count, more=""):
        action = message(_MASS_ACTION_KEY.get(verb, "MSG_ACTION_DELETE"))
        if count is None:
            return message("MSG_CONSENT_GATE_AFTER_LONG_JOB_ANY", action=action) + more
        words = message("MSG_CONSENT_GATE_AFTER_LONG_JOB", action=action, n=count) + more
        if isinstance(count, str) and count.endswith(".@rows}"):
            # The same step, counted partition by partition: where the items
            # are, when the long step worked folder by folder. Empty otherwise.
            words += " " + count[:-len(".@rows}")] + ".@by_partition}"
        return words

    framework = _insert_mass_mutation_gate(
        framework, entry["query"], context, threshold=0, prompt_for=_words)
    steps = list(framework.steps)
    for position, step in enumerate(steps):
        args = step.args if isinstance(step.args, dict) else None
        if step.tool != "get_approval" or args is None or "guard_threshold" not in args:
            continue
        acted = next((later for later in range(position + 1, len(steps))
                      if steps[later].tool.split("_", 1)[0] in _MASS_MUTATION_VERBS), None)
        args["guard_threshold"] = 0
        if acted is not None:
            reads = steps[acted].args.get("from_step") \
                if isinstance(steps[acted].args, dict) else None
            # Which file of each group stays and any list a ceiling cut: a
            # wrong reading or a cut is seen before the yes.
            args["prompt"] = _words(
                steps[acted].tool.split("_", 1)[0], args.get("guard_count"),
                _what_the_question_adds(steps, acted, reads, catalog))
    return framework


def _removals_never_asked(framework) -> frozenset:
    """The steps that delete or move with no question before them in the plan.

    The gate is put in best-effort, as in a chat turn: a failure there leaves
    the plan as it was. Here the rule is «always ask», so a removal the gate
    did not come before is not run at all."""
    from engine.dispatch import _MASS_MUTATION_VERBS

    asked, unasked = False, set()
    for step in framework.steps:
        tool = str(step.tool or "")
        if tool == "get_approval":
            asked = True
        elif not asked and tool.split("_", 1)[0] in _MASS_MUTATION_VERBS:
            unasked.add(tool)
    return frozenset(unasked)


def _rest_of_request(entry: dict, result: dict, *, replay=(), approved: bool = False,
                     turn_id: str):
    """Run the plan the request was admitted with, the long step answered by
    the job's own result. The same engine and the same checks as a chat turn.

    ``replay`` holds, in order, what the steps before a consent already
    observed: after the consent they are not run a second time."""
    import agent_runtime
    from engine.executor import Executor
    from engine.types import Framework
    from loader import load_catalog
    from messages import get as message
    from vaglio import guard_check

    catalog = agent_runtime._engine_v2_catalog_with_builtins(
        list(load_catalog(verify=True, include_synth=True).executors.values()))
    owner, channel = entry["owner_user_id"], entry["channel"]
    context = {
        "turn_id": turn_id, "actor": entry["actor"] or "host", "channel": channel,
        "owner_user_id": owner, "user_query_raw": entry["query"],
        "conversation_id": entry["conversation_id"],
        "source_request_id": agent_runtime._opaque_source_request_id(
            f"turn:{turn_id}", owner_user_id=owner,
            conversation_id=entry["conversation_id"], channel=channel),
        "target_device": entry["target_device"], "_gate_approved": approved,
    }
    # The question is part of the plan on both runs, so the steps keep their
    # numbers: after the consent it lets the plan through instead of asking.
    framework = _ask_before_removing(
        Framework.from_dict(json.loads(entry["plan_json"])), entry,
        {**context, "_gate_approved": False}, catalog)
    unasked = _removals_never_asked(framework)
    waiting = list(replay)
    answered = []

    def _not_again(tool):
        return {"ok": False, "error_class": "continuation_failed",
                "error": message("MSG_ORCH_CONTINUATION_FAILED", detail=tool)}

    def _invoke(tool: str, args: dict) -> dict:
        if tool == entry["tool"]:
            # The job ran it, once: its result is the step's result.
            if answered:
                return _not_again(tool)
            answered.append(tool)
            # Shown as the tool declares, as when a chat turn runs it.
            shown = next((getattr(item, "presentation", None) for item in catalog
                          if getattr(item, "name", None) == tool), None)
            return {**result, "_presentation_contract": shown} if shown else dict(result)
        if tool in unasked:
            # The question could not be put before this step: it does not run.
            return {"ok": False, "error_class": "continuation_failed",
                    "error": message("MSG_LRE_REST_NEEDS_YOU")}
        if waiting:
            name, observation = waiting.pop(0)
            if name != tool:
                waiting.clear()
                return _not_again(tool)
            return dict(observation)
        return agent_runtime.invoke_tool_by_name(
            tool, args, catalog=catalog, actor=context["actor"], channel=channel,
            owner_user_id=owner, target_device=entry["target_device"] or None,
            turn_id=turn_id, source_request_id=context["source_request_id"],
            request_text=entry["query"])

    return Executor(invoke_executor=_invoke, llm_call_fast=_fill,
                    vaglio_guard=guard_check, catalog=catalog).run(
        framework, query=entry["query"], runtime_ctx=context)


def _consent_waits(entry: dict, run, *, turn_id: str) -> bool:
    """The rest stopped at a question: its answer resumes this very plan.

    What the steps before the question observed travels with the question,
    as a resumed chat turn carries its prior steps; the long step's result
    stays in the job, where it is read again."""
    import dialog_pending

    asked = run.steps[-1].result if run.steps else {}
    proposal = next(iter(asked.get("expandable_caps") or []), {})
    sender = str(proposal.get("sender_for_state") or "")
    state = dialog_pending.load_pending(
        sender, run.gate_dialog_id, owner_user_id=entry["owner_user_id"])
    if not state:
        return False
    before = state.get("on_complete") or {}
    state["on_complete"] = {
        "type": RESUME_REST,
        "owner_user_id": entry["owner_user_id"],
        "workload_id": entry["workload_id"],
        "turn_id": turn_id,
        "gate_approve_value": before.get("approve_value", "approve"),
        "replay": [[step.tool, step.result] for step in run.steps[:-1]
                   if step.tool != entry["tool"]],
    }
    dialog_pending.save_pending(sender, run.gate_dialog_id, state)
    return True


def _answer_with_the_rest(entry: dict, result: dict, *, lost: str = "", replay=(),
                          approved: bool = False, parent: str = ""):
    """The answer of the whole request, as a turn linked to what asked."""
    from messages import get as message

    import undo

    started = time.time()
    turn_id = uuid.uuid4().hex[:16]
    # Every effect of the rest, before or after a consent, is part of the
    # request: one «undo» reverses them together with the request's own.
    undo.continue_request(turn_id, entry["turn_id"])
    run = _rest_of_request(entry, result, replay=replay, approved=approved,
                           turn_id=turn_id)
    kind, text = run.final_kind or "answer", str(run.final_text or "")
    if kind == "ask":
        # The consent of the runtime is asked and its answer resumes the
        # plan. Any other question (a form of a tool, a choice) has no way to
        # resume from here yet: it is not asked, and that is said.
        consent = (run.steps and run.steps[-1].tool == "get_approval"
                   and getattr(run, "gate_dialog_id", ""))
        if not (consent and _consent_waits(entry, run, turn_id=turn_id)):
            _withdraw_question(entry, run)
            kind, text = "error", message("MSG_LRE_REST_NEEDS_YOU")
    unreplayed = None
    if kind == "error":
        # What the job found is never lost because a later step failed: it
        # is given as that step alone would be, shown as its tool declares,
        # then what happened to the rest. The turn stays an error: as an
        # answer its writer would say only that nothing was done.
        found = next((step.result for step in run.steps
                      if step.tool == entry["tool"] and step.ok), None)
        if found is None and isinstance(result, dict) and result.get("ok") is True:
            # A step written before the long one failed, so the plan never
            # reached it: the job still ran, and what it found is here.
            found = result
            # The file writer needs those records as well as the message;
            # the failed continuation has no step holding them yet.
            unreplayed = result
        failed = next((step for step in reversed(run.steps) if not step.ok), None)
        if not text and failed is not None:
            text = "«%s»: %s" % (failed.tool, failed.result.get("error")
                                 or failed.result.get("error_class") or "")
        if found is not None:
            text = "\n\n".join(part for part in (
                _answer_text({**entry, "final_template": "${step1.@table}"}, found),
                message("MSG_LRE_REST_FAILED"), text) if part)
    return _write_turn(entry, text=text, kind=kind, result=unreplayed, lost=lost,
                       run=run, turn_id=turn_id, parent=parent, started=started)


def _withdraw_question(entry: dict, run) -> None:
    """A question that cannot be answered usefully does not stay open."""
    import dialog_pending

    asked = (run.steps[-1].result if run.steps else None) or {}
    for proposal in asked.get("expandable_caps") or ():
        try:
            dialog_pending.cancel_pending(
                str(proposal.get("sender_for_state") or ""),
                str(proposal.get("dialog_id") or ""),
                owner_user_id=entry["owner_user_id"])
        except Exception as exc:  # noqa: BLE001 - it expires by itself
            log.warning("lre_answers: question not withdrawn: %r", exc)


def _job_store():
    """The store of the long jobs, opened for one short reading."""
    from durable_workloads.storage import DurableWorkloadStore

    return DurableWorkloadStore.open()


# What the units of a job asked for several partitions may add up to when
# they are read back together; beyond it the partitions not read are named.
_MAX_JOINED_BYTES = 64 * 1024 * 1024


def _partition_policy(tool: str, payloads) -> dict:
    """The partition the tool declares now, or what its own results say."""
    import partitioned_runs
    from loader import load_catalog

    try:
        declared = partitioned_runs.policy_of(
            load_catalog(verify=True, include_synth=True).executors.get(tool))
    except Exception as exc:  # noqa: BLE001 - the results still say enough to join them
        log.info("lre_answers: no partition declared for %s: %r", tool, exc)
        declared = None
    if declared is not None:
        return declared
    said = next((item for item in payloads if isinstance(item, dict)), {})
    return {"arg": str(said.get("partition_arg") or ""), "list_arg": "",
            "kind": "directory", "sums": (),
            "union": str(said.get("partition_union") or "separate")}


def _joined_partitions(entry: dict, store) -> dict | None:
    """The one result of a job that ran its tool once per partition.

    None for any other job. The units answer in no order and each for its own
    partition: they are joined in the order the job was admitted with, and a
    partition nobody answered for is a failed one with its reason, never an
    absent one (§2.8)."""
    import partitioned_runs
    from durable_workloads.partitioned_invocation import (
        EXECUTE_STAGE, PARTITIONS_STAGE,
    )
    from messages import get as message

    owner, job = entry["owner_user_id"], entry["workload_id"]
    listed, _whole = store.committed_stage_results(
        owner, job, stage_key=PARTITIONS_STAGE, limit=1, max_bytes=_MAX_PLAN_JSON * 8)
    order = [row["partition"] for row in (listed[0].get("entries") or ())
             if isinstance(row, dict) and isinstance(row.get("partition"), str)
             ] if listed and isinstance(listed[0], dict) else []
    if not order:
        return None
    payloads, complete = store.committed_stage_results(
        owner, job, stage_key=EXECUTE_STAGE, limit=min(1024, len(order)),
        max_bytes=_MAX_JOINED_BYTES)
    code = "ERR_PARTITION_UNANSWERED" if complete else "ERR_PARTITION_RESULT_TOO_LARGE"
    # The words of the units speak of one folder each: said again of all.
    for payload in payloads:
        if isinstance(payload, dict):
            payload.pop("partial_reason", None)
    return partitioned_runs.with_partial_reason(partitioned_runs.combine(
        _partition_policy(entry["tool"], payloads), payloads, order=order,
        unanswered={"error_class": "resource_unavailable", "error_code": code,
                    "error": message(code)}))


def _job_results(entry: dict, store) -> list[dict]:
    """What the long step of a request gave: one result when the job has an
    answer. A tool asked for several partitions committed one result for
    each, joined here into the one the step would have given in a turn."""
    joined = _joined_partitions(entry, store)
    if joined is not None:
        return [joined]
    return store.committed_tool_results(
        entry["owner_user_id"], entry["workload_id"], tool=entry["tool"])


def resume_after_consent(on_complete: dict, values: dict):
    """The person answered the question asked after a long job.

    Yes: the steps after the question run on the very result that was shown.
    No: nothing else is done. Returns the written turn, or None for a no."""
    approve = on_complete.get("gate_approve_value", "approve")
    if next(iter((values or {}).values()), None) != approve:
        return None
    owner = str(on_complete.get("owner_user_id") or "")
    entry = origin(str(on_complete.get("workload_id") or ""), owner_user_id=owner)
    if not entry or not entry.get("plan_json"):
        raise LookupError("the request of this question is no longer kept")
    with _job_store() as store:
        results = _job_results(entry, store)
    if len(results) != 1:
        raise LookupError("the result of the long job is no longer kept")
    replay = [(str(tool), dict(observation)) for tool, observation
              in (on_complete.get("replay") or ())]
    return _answer_with_the_rest(
        entry, results[0], replay=replay, approved=True,
        parent=str(on_complete.get("turn_id") or ""))


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


def _send_to_telegram(entry: dict, text: str, *, buttons=None) -> str:
    """"sent", "deferred" to the person's next message there, or "unpaired"."""
    from channels import OutboundMessage
    from channels.telegram import TelegramChannel
    from durable_workloads.events import resolve_telegram_recipient

    try:
        # Resolved now: the chat may have been unpaired while the job ran.
        recipient = resolve_telegram_recipient(entry["owner_user_id"])
        if not recipient:
            return "unpaired"
        reply = TelegramChannel().send(
            recipient, OutboundMessage(text=text, buttons=buttons))
        if not (isinstance(reply, dict) and reply.get("ok") is False):
            return "sent"
    except Exception as exc:  # noqa: BLE001 - the answer must not be lost
        log.warning("lre_answers: telegram send failed: %r", exc)
    # Not lost: it opens the person's next message in that chat.
    import user_notices

    user_notices.append("telegram", entry["actor"], text,
                        owner_user_id=entry["owner_user_id"])
    return "deferred"


def _send_files_to_telegram(entry: dict, turn) -> int:
    """The files of the answer as documents, after its text: a link of the
    web chat is of no use in a Telegram chat. Best effort, each on its own."""
    from channels.telegram import TelegramChannel
    from durable_workloads.events import resolve_telegram_recipient

    sent = 0
    for item in getattr(turn, "attachments", None) or []:
        if not (isinstance(item, dict) and item.get("kind") == "file" and item.get("path")):
            continue
        try:
            recipient = resolve_telegram_recipient(entry["owner_user_id"])
            reply = recipient and TelegramChannel().send_document(
                chat_id=recipient, path=item["path"], basename=item.get("basename"),
                caption=item.get("basename"))
            sent += bool(isinstance(reply, dict) and reply.get("ok"))
        except Exception as exc:  # noqa: BLE001 - the text already arrived
            log.warning("lre_answers: telegram document not sent: %r", exc)
    return sent


def _question_for_telegram(entry: dict, turn, text: str):
    """What a turn that ends with a question becomes in a Telegram chat.

    Asked there, it carries its answers as buttons. Asked in the web chat,
    its form lives in that conversation: the text says so, because a
    consent is given where everything about it can be seen."""
    import re

    asked = (turn.steps[-1].result if getattr(turn, "steps", None) else None) or {}
    proposal = next(iter(asked.get("expandable_caps") or []), None)
    if getattr(turn, "final_kind", "") != "ask" or not isinstance(proposal, dict):
        return text, None
    text = re.sub(r"\s*INLINE_FORM:\S+", "", text).strip()
    if entry["channel"] != "telegram":
        from messages import get as message

        return "\n\n".join((text, message("MSG_LRE_CONSENT_IN_PAGE"))), None
    from channels.inline_ui import keyboard_for_proposal

    buttons, _preview = keyboard_for_proposal(
        proposal, sender_candidates=[str(proposal.get("sender_for_state") or "")],
        owner_user_id=entry["owner_user_id"], turn_id=turn.turn_id)
    return text, buttons


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
    text, buttons = _question_for_telegram(
        entry, turn, _bounded_for_chat(completion.text))
    told = _send_to_telegram(entry, "\n\n".join(
        part for part in (title, text) if part), buttons=buttons)
    if told == "sent":
        _send_files_to_telegram(entry, turn)
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
    if entry.get("plan_json") and state is not WorkloadState.NEEDS_ATTENTION:
        # The request asked for more than the job: say that it was not done.
        from messages import get as message

        text = "\n\n".join(part for part in (
            text, message("MSG_LRE_REST_NOT_DONE")) if part)
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

    if entry["state"] == CONTINUING:
        if entry["workload_id"] in _AT_WORK:
            return "working"
        return _tell_interrupted(entry)
    try:
        state = store.get_workload(entry["owner_user_id"], entry["workload_id"]).state
    except Exception as exc:  # noqa: BLE001 - a job removed from the store has no answer
        log.info("lre_answers: job %s is gone: %r", entry["workload_id"][:16], exc)
        _mark(entry["workload_id"], DONE)
        return "gone"
    if state in (WorkloadState.COMPLETED, WorkloadState.COMPLETED_WITH_ERRORS):
        results = _job_results(entry, store)
        if len(results) != 1:
            # Not the single answer of a plain tool. Its notice was silenced
            # because an answer was due: how it ended is said all the same.
            return _tell_outcome(entry, store, state)
        # Partly done is done, with the rest declared.
        lost = (lost_items_note(store, entry["owner_user_id"], entry["workload_id"])
                if state is WorkloadState.COMPLETED_WITH_ERRORS else "")
        if entry.get("plan_json"):
            # The rest runs once: a pass that dies half way is told, not redone.
            _mark(entry["workload_id"], CONTINUING)
            _AT_WORK.add(entry["workload_id"])
            try:
                turn = _as_a_turn_of(entry, lambda: _answer_with_the_rest(
                    entry, results[0], lost=lost))
            finally:
                _AT_WORK.discard(entry["workload_id"])
        else:
            turn = _write_turn(entry, text=_answer_text(entry, results[0]),
                               kind="answer", result=results[0], lost=lost)
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


def _as_a_turn_of(entry: dict, work):
    """Run ``work`` under what a chat turn of this person holds: their
    lease (nothing runs for someone being erased) and their own segment in
    the Monitor."""
    from monitor_capture import segment_scope
    from user_lifecycle import owner_session

    owner = entry["owner_user_id"]
    with owner_session(owner), segment_scope(
            independent=True, owner_id=owner, channel=entry["channel"]):
        return work()


def _tell_interrupted(entry: dict) -> str:
    """The rest of the request was running when the service stopped. What it
    had done is not known here, so nothing is run a second time: it is said."""
    from messages import get as message

    turn = _write_turn(entry, text=message("MSG_LRE_REST_INTERRUPTED"),
                       kind="error", result=None)
    _mark(entry["workload_id"], DONE, answer_turn_id=turn.turn_id)
    return _push(entry, turn)


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
    with (open_store or _job_store)() as store:
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
