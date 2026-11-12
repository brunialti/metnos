"""change_applier — daemon che applica fisicamente i change_intent in
stato ACCEPTED (ADR 0158).

Trigger scheduler v2: every_10m (reattivo, no daily — gli accept utente
devono essere applicati in tempi brevi).

Per kind:
  - create_executor       → invoca synth_request pipeline (~150s)
  - extend_executor       → manifest TOML patch + rollback_blob + re-sign
  - dedupe_executors      → alias setup
  - materialize_pipeline  → la pipeline proposta gira una volta come turno reale
  - cache_pattern         → canonical_query_log.state = active
  - reject_pattern        → rejected_patterns.jsonl append

Ogni handler:
  - idempotente (re-run safe)
  - ritorna dict `effect` salvato in change_intents.applied_effect
  - errore → mark_failed con reason (loop sicuro; retry manuale via UI)

Audit JSONL ~/.local/share/metnos/audit/change_applier.jsonl
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Callable

import config as C
from change_intents import (
    KIND_CACHE_PATTERN,
    KIND_CREATE_EXECUTOR,
    KIND_DEDUPE_EXECUTORS,
    KIND_EXTEND_EXECUTOR,
    KIND_MATERIALIZE_PIPELINE,
    KIND_REJECT_PATTERN,
    STATE_ACCEPTED,
    STATE_PROPOSED,
    ChangeIntent,
    apply_decision,
    get_intent,
    list_intents,
    mark_applied,
    mark_failed,
    update_body,
    close_until,
    waits_for_person,
)
from logging_setup import get_logger

_log = get_logger(__name__)


# --- Audit ---------------------------------------------------------------

def _audit(record: dict) -> None:
    audit_path = C.PATH_USER_DATA / "audit" / "change_applier.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), **record}
    try:
        with audit_path.open("a") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# --- Handler: create_executor --------------------------------------------

class ExternalModelConsentNeeded(Exception):
    """Every local attempt failed: only the person can allow the external
    model for this tool (ADR 0239). Carries the last refusal."""


class ClosedAfterExternalModel(Exception):
    """The external model failed too: the proposal is given up for a while."""


def external_model_available() -> bool:
    """True when the instance has an external model configured."""
    try:
        from llm_router import LLMRouter
        from llm_workloads import tier_for
        LLMRouter().provider(tier_for("synt.finalize.external"))
        return True
    except Exception:  # noqa: BLE001 - not configured is an ordinary answer
        return False


class ReviewPending(Exception):
    """Synth made a candidate that waits for the independent review.

    Nothing is applied and nothing failed: the proposal stays accepted until
    the reviewed candidate is admitted (ADR 0224) or the person withdraws it.
    """

    def __init__(self, proposal_id: str) -> None:
        self.proposal_id = proposal_id
        super().__init__(proposal_id)


def _planner_can_use(name: str) -> bool:
    """True when ``name`` is an executor the planner selects from."""
    from loader import VISIBILITY_COMPOSER, filter_for_visibility, load_catalog

    return name in filter_for_visibility(
        load_catalog(verify=True), VISIBILITY_COMPOSER).executors


def _creation_request(ci: ChangeIntent) -> tuple[str, str]:
    """The name a creation proposal asks for and the request Synth builds
    from: the one frozen when the proposal was born, never the page sentence
    (that one is rewritten at every new gap)."""
    body = ci.intent_body or {}
    target = ci.intent_target
    return (body.get("name") or target,
            body.get("intent_text") or body.get("query_sample")
            or ci.intent_summary or target)


def pending_review(ci: ChangeIntent) -> str | None:
    """The retained candidate of this proposal that waits for review, if any:
    under the name asked for or, when Synth chose another name, the one
    built from the same request."""
    name, intent_text = _creation_request(ci)
    try:
        from synt import DEFAULT_PROPOSALS_DIR
        from synth_proposal_store import birth_reason, pending_candidate
        return pending_candidate(DEFAULT_PROPOSALS_DIR, name=name,
                                 reason=birth_reason(intent_text))
    except Exception as exc:  # noqa: BLE001 - an unreadable store is not a candidate
        _log.warning("change_applier: pending review lookup failed: %r", exc)
        return None


def apply_create_executor(ci: ChangeIntent) -> dict:
    """Ask Synth for the executor and report only what really happened.

    «Applied» means the planner can use the executor now. A candidate that
    waits for the independent review raises ``ReviewPending``; a redirect to
    an existing executor or a candidate that is not active is a failure with
    its reason, never a silent success (§2.8).

    Latenza: ~150s wall per generazione full (stage 1-5). Daemon ok.
    """
    from messages import get as _msg

    expected_name, intent_text = _creation_request(ci)

    try:
        if _planner_can_use(expected_name):
            return {
                "executor_name": expected_name,
                "short_circuit": "already_in_catalog",
            }
    except Exception as exc:  # noqa: BLE001 - Synth below reports the real state
        _log.warning("change_applier: catalog check failed: %r", exc)

    waiting = pending_review(ci)
    if waiting:
        raise ReviewPending(waiting)

    from synth_request import handle_synth_request
    external = bool((ci.intent_body or {}).get("external_model_consent"))
    body = ci.intent_body or {}
    examples: list[str] = []
    if body.get("verb") and body.get("object"):
        import learning_loop
        examples = learning_loop.gap_requests(str(body["verb"]), str(body["object"]))
    result = handle_synth_request(
        {"expected_name": expected_name, "intent": intent_text, "examples": examples},
        user_query=intent_text,
        progress=None,
        verbose=False,
        external_model=external,
    )
    if result.get("review_pending"):
        raise ReviewPending(str(result.get("proposal_id") or ""))
    if not result.get("ok", True):
        # Synth says why in one of three places: an error before it starts,
        # a refusal at delivery, or the reason a stage refused the candidate.
        reason = (result.get("error") or result.get("install_error")
                  or result.get("reason") or "unknown")
        attempts = int(result.get("attempts") or 1)
        counted = f" after {attempts} attempts" if attempts > 1 else ""
        message = f"synth_request failed{counted}: {reason}"
        if external:
            raise ClosedAfterExternalModel(message)
        if result.get("attempts_exhausted") and external_model_available():
            raise ExternalModelConsentNeeded(message)
        raise RuntimeError(message)
    if result.get("redirected"):
        raise RuntimeError(_msg("MSG_CHANGE_REDIRECTED", name=expected_name,
                                existing=str(result.get("name") or "")))
    if not _planner_can_use(expected_name):
        raise RuntimeError(_msg("MSG_CHANGE_NOT_ACTIVE", name=expected_name))
    return {
        "executor_name": expected_name,
        "synth_proposal_id": result.get("proposal_id"),
        "stages_completed": result.get("stages_completed"),
        "final_state": result.get("final_state"),
    }


# --- One decision of the person, at the end (ADR 0240) --------------------

# Candidates that may wait for the person's answer at the same time: a longer
# list is again a page nobody reads.
BUILD_WAITING_MAX = 3
_ACTIVATION_STALE_S = 1800
# The two answers of the person on a candidate that is ready.
ACTIVATE = "activate"
DECLINE = "decline"


def is_candidate_decision(action: str) -> bool:
    return action in (ACTIVATE, DECLINE)


# The page is not always looked at: what waits for the person is also pushed
# to the administrator's channel (Roberto, 12/11/2026). The external-model
# question travels with its two answers; a ready tool is announced only, and
# decided on the page, where everything about it can be read.
PUSH_PREFIX = "chg:"
_PUSH_EXTERNAL = "ext"
_PUSH_CLOSE = "cls"
_PUSH_ANSWERS = {_PUSH_EXTERNAL: "external", _PUSH_CLOSE: "close"}


def pushed_answer(data: str) -> tuple[str, str] | None:
    """``(answer, proposal id)`` carried by a button of a pushed question,
    or None when the text is not one of ours."""
    import re

    parts = str(data or "").split(":")
    if (len(parts) != 3 or parts[0] + ":" != PUSH_PREFIX
            or parts[1] not in _PUSH_ANSWERS
            or re.fullmatch(r"[0-9a-f-]{8,36}", parts[2]) is None):
        return None
    return _PUSH_ANSWERS[parts[1]], parts[2]


def _push(body_key: str, ci: ChangeIntent, *, event_key: str, buttons: list | None = None) -> None:
    """One notification per event, best effort: a channel that is down never
    stops the applier, and the page shows the same thing anyway."""
    try:
        import notify_admin
        from messages import get as _msg

        notify_admin.notify(
            _msg(body_key, name=_creation_request(ci)[0]),
            kind="changes", severity="info", event_key=event_key,
            metadata={"change_intent": ci.id}, buttons=buttons)
    except Exception as exc:  # noqa: BLE001
        _log.warning("change_applier: push failed: %r", exc)


def _external_question_key(ci: ChangeIntent) -> str:
    # One question per failure: a later failure of the same proposal asks again.
    return f"change_external:{ci.id}:{ci.failed_at or ''}"


def question_answered(ci: ChangeIntent) -> None:
    """The external-model question of this proposal was answered, on the
    page or on the pushed message: the message stops offering answers. The
    page needs nothing: it shows the proposal's own state."""
    try:
        import notify_admin
        notify_admin.clear_buttons(_external_question_key(ci))
    except Exception as exc:  # noqa: BLE001 - the answer itself is recorded
        _log.warning("change_applier: pushed question not closed: %r", exc)


def waiting_signature(waiting: list, *, signer_available: bool) -> str:
    """What an open page compares to know whether what waits has changed."""
    import hashlib

    facts = [[item.get("id"), item.get("waiting_kind"), item.get("activation_note"),
              (item.get("candidate") or {}).get("proposal_id")] for item in waiting]
    return hashlib.sha256(json.dumps(
        [facts, bool(signer_available)], sort_keys=True).encode("utf-8")).hexdigest()[:16]


def push_external_model_question(ci: ChangeIntent) -> None:
    """Ask the question where the person is, with the two answers."""
    from messages import get as _msg

    current = get_intent(ci.id) or ci
    _push("MSG_CHANGE_EXTERNAL_PUSH", current,
          event_key=_external_question_key(current),
          buttons=[[
              {"text": _msg("UI_CHANGE_BTN_EXTERNAL"),
               "data": f"{PUSH_PREFIX}{_PUSH_EXTERNAL}:{ci.id}"},
              {"text": _msg("UI_CHANGE_BTN_CLOSE"),
               "data": f"{PUSH_PREFIX}{_PUSH_CLOSE}:{ci.id}"},
          ]])


def push_ready_candidate(ci: ChangeIntent, proposal_id: str) -> None:
    """Say once that a tested tool waits for the answer on the page."""
    if proposal_id:
        _push("MSG_CHANGE_READY_PUSH", ci, event_key=f"change_ready:{proposal_id}")


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _built_without_asking(ci: ChangeIntent) -> bool:
    """A gap seen in real requests, with a name of the closed vocabulary.

    Building it costs local compute and activates nothing: the person decides
    once, on the finished and tested candidate. Proposals of every other
    origin, and those that need a new word, still wait for a decision first.
    """
    if (ci.state != STATE_PROPOSED or ci.intent_kind != KIND_CREATE_EXECUTOR
            or ci.origin_family != "observation" or ci.origin_module != "learning_loop"):
        return False
    import vocabulary_dossier
    return not vocabulary_dossier.proposer_text(ci)


def accept_for_building(*, limit: int = 1) -> list[str]:
    """Start building the best gap proposals, at most ``limit`` per run and
    never more than ``BUILD_WAITING_MAX`` candidates waiting for the person."""
    building = [ci for ci in list_intents(state=STATE_ACCEPTED, limit=500)
                if ci.intent_kind == KIND_CREATE_EXECUTOR]
    room = min(int(limit), BUILD_WAITING_MAX - len(building))
    started: list[str] = []
    if room <= 0:
        return started
    for ci in list_intents(state=STATE_PROPOSED, intent_kind=KIND_CREATE_EXECUTOR,
                           limit=500, order_by="score_desc"):
        if len(started) >= room:
            break
        if not _built_without_asking(ci):
            continue
        try:
            exists = _planner_can_use(_creation_request(ci)[0])
        except Exception as exc:  # noqa: BLE001 - an unreadable catalog decides nothing
            _log.warning("change_applier: catalog check failed: %r", exc)
            continue
        if exists:
            # The gap was filled in the meantime: nothing to build or to ask.
            apply_decision(ci.id, action="reject", by="metnos",
                           reason="the capability exists now")
            _audit({"event": "gap_already_filled", "id": ci.id,
                    "target": ci.intent_target})
            continue
        apply_decision(ci.id, action="accept", by="metnos")
        started.append(ci.id)
        _audit({"event": "built_without_asking", "id": ci.id, "target": ci.intent_target})
    return started


def activation_running(ci: ChangeIntent) -> bool:
    """True while the signer is working on this proposal's candidate. A
    record left by an interrupted service stops counting after a while."""
    state = (ci.intent_body or {}).get("activation")
    if not isinstance(state, dict) or state.get("state") != "running":
        return False
    try:
        started = time.mktime(time.strptime(str(state.get("at")), "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return False
    return time.mktime(time.gmtime()) - started < _ACTIVATION_STALE_S


_OUTCOME_SHOWN_S = 900


def _recent_activation_outcome(ci: ChangeIntent) -> str | None:
    """``activated`` or ``activation_refused`` for a few minutes after the
    signer answered, then nothing: the proposal's own tab keeps the record."""
    state = (ci.intent_body or {}).get("activation")
    if not isinstance(state, dict) or state.get("state") not in ("done", "refused"):
        return None
    try:
        asked = time.mktime(time.strptime(str(state.get("at")), "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None
    if time.mktime(time.gmtime()) - asked > _ACTIVATION_STALE_S + _OUTCOME_SHOWN_S:
        return None
    return "activated" if state["state"] == "done" else "activation_refused"


def waiting_for_person(intents, *, lang: str) -> list[dict]:
    """What waits for an answer of the person, to show first on the page:
    the external-model question and the candidates ready to be activated."""
    from dataclasses import asdict

    waiting: list[dict] = []
    for ci in intents:
        kind = waits_for_person(ci)
        if kind:
            waiting.append(dict(asdict(ci), waiting_kind=kind))
            continue
        outcome = _recent_activation_outcome(ci)
        if outcome:
            # The answer to a click must be seen where the click was made.
            waiting.append(dict(asdict(ci), waiting_kind=outcome))
            continue
        if ci.state != STATE_ACCEPTED or ci.intent_kind != KIND_CREATE_EXECUTOR:
            continue
        proposal_id = pending_review(ci)
        if not proposal_id:
            continue
        try:
            import synth_activation
            from synt import DEFAULT_PROPOSALS_DIR
            card = synth_activation.candidate_card(DEFAULT_PROPOSALS_DIR, proposal_id, lang=lang)
        except Exception as exc:  # noqa: BLE001 - an unreadable candidate is not a question
            _log.warning("change_applier: candidate card unavailable: %r", exc)
            continue
        waiting.append(dict(
            asdict(ci), candidate=card,
            waiting_kind="activating" if activation_running(ci) else "activation",
            activation_note=((ci.intent_body or {}).get("activation") or {}).get("note")))
    return waiting


def decline_candidate(ci: ChangeIntent, *, by: str) -> ChangeIntent:
    """The person's «no»: the candidate goes, the proposal rests for a while."""
    import synth_activation
    from synt import DEFAULT_PROPOSALS_DIR

    proposal_id = pending_review(ci)
    if proposal_id:
        synth_activation.discard_candidate(DEFAULT_PROPOSALS_DIR, proposal_id)
    _audit({"event": "candidate_declined", "id": ci.id, "by": by,
            "proposal_id": proposal_id})
    return close_until(ci.id, reason="declined by the person")


def start_activation(ci: ChangeIntent, *, by: str) -> tuple[str, str]:
    """Record the person's «yes» before the signer is called: the page shows
    the work in progress and a second click cannot start it twice."""
    proposal_id = pending_review(ci)
    if not proposal_id:
        raise LookupError("no candidate waits for this proposal")
    if activation_running(ci):
        raise RuntimeError("activation already running")
    at = _iso_now()
    update_body(ci.id, activation={"state": "running", "by": by, "at": at,
                                   "proposal_id": proposal_id})
    _audit({"event": "activation_requested", "id": ci.id, "by": by,
            "proposal_id": proposal_id})
    return proposal_id, at


def run_activation(ci_id: str, proposal_id: str, *, by: str, at: str) -> dict:
    """Carry the «yes» to the signer and settle the proposal on its answer.

    Admitted and usable: applied. Refused: the candidate goes and the proposal
    says why, so nothing keeps waiting for an answer already given. A signer
    that is missing or silent leaves the candidate where it was.
    """
    import synth_activation
    from synt import DEFAULT_PROPOSALS_DIR

    try:
        reply = synth_activation.activate(DEFAULT_PROPOSALS_DIR, proposal_id, by=by, at=at)
    except Exception as exc:  # noqa: BLE001 - never leave the proposal «running»
        _log.exception("change_applier: activation failed: %r", exc)
        reply = {"status": "signer_failed", "error": type(exc).__name__}
    status = str(reply.get("status") or "")
    ci = get_intent(ci_id)
    name = _creation_request(ci)[0] if ci is not None else ""
    if status in ("signer_missing", "signer_failed"):
        update_body(ci_id, activation={"state": "error", "by": by, "at": at,
                                       "note": status})
        _audit({"event": "activation_not_done", "id": ci_id, "status": status,
                "error": reply.get("error")})
        return {"result": status}
    if status in ("admitted", "already_current"):
        usable = False
        try:
            usable = _planner_can_use(str(reply.get("name") or name))
        except Exception as exc:  # noqa: BLE001
            _log.warning("change_applier: catalog check failed: %r", exc)
        if usable:
            synth_activation.discard_candidate(DEFAULT_PROPOSALS_DIR, proposal_id)
            update_body(ci_id, activation={"state": "done", "by": by, "at": at})
            mark_applied(ci_id, effect={
                "executor_name": str(reply.get("name") or name), "activated_by": by,
                "review_id": reply.get("review_id")})
            _audit({"event": "activated", "id": ci_id, "by": by})
            return {"result": "applied"}
        from messages import get as _msg
        reason = _msg("MSG_CHANGE_NOT_ACTIVE", name=str(reply.get("name") or name))
    else:
        reason = synth_activation.refusal_reason(reply)
    synth_activation.discard_candidate(DEFAULT_PROPOSALS_DIR, proposal_id)
    update_body(ci_id, activation={"state": "refused", "by": by, "at": at})
    mark_failed(ci_id, reason=reason)
    _audit({"event": "activation_refused", "id": ci_id, "reason": reason[:300]})
    return {"result": "refused", "reason": reason}


# --- Handler: extend_executor --------------------------------------------

def apply_extend_executor(ci: ChangeIntent) -> dict:
    """Modifica manifest TOML in place + rollback_blob + re-sign.

    Implementazione in `change_applier_extend.extend_executor_manifest`
    (Fase 2.2). Se il modulo non e' ancora deployato, solleva
    NotImplementedError — il daemon marca FAILED con reason chiaro,
    l'utente vedra' Retry attivo quando 2.2 e' live.
    """
    try:
        from change_applier_extend import extend_executor_manifest
    except ImportError as exc:
        raise NotImplementedError(
            "extend_executor handler deferred to Fase 2.2: " + str(exc)
        )
    return extend_executor_manifest(ci)


# --- Handler: dedupe_executors -------------------------------------------

def apply_dedupe_executors(ci: ChangeIntent) -> dict:
    """Marca uno dei due executor come alias dell'altro.

    Strategia MVP: l'executor B (intent_target) viene marcato
    `deprecated_at=<now>` in executor_stats; alias write in
    `~/.local/share/metnos/executor_aliases.json` (executor_target_b →
    executor_a). Loader puo' leggere alias e route runtime.

    Idempotente: re-run no-op se gia' deprecated.
    """
    body = ci.intent_body or {}
    a = body.get("a")
    b = body.get("b") or ci.intent_target
    if not a or not b or a == b:
        raise ValueError(f"dedupe needs distinct a,b — got a={a} b={b}")
    # La lista e' la stessa SoT usata dall'ager: una dedupe automatica non
    # deve poter ritirare il seed minimo del sistema. Un eventuale override
    # umano futuro dovra' essere una decisione esplicita e auditabile, non un
    # booleano nascosto nel body di una proposta autogenerata.
    from executor_aging import PROTECTED_NAMES
    if b in PROTECTED_NAMES:
        raise PermissionError(f"cannot deprecate protected executor: {b}")
    if (C.PATH_EXECUTORS / b).is_dir():
        raise PermissionError(f"cannot deprecate handcrafted executor: {b}")
    # Provenienza e demote passano dal deposito che possiede la decisione:
    # le due istruzioni dirette qui sotto scrivevano un file esatto qualunque
    # cosa l'installazione avesse migrato, e saltavano la storia del deposito.
    from executor_lifecycle_state import recorded_source
    source = recorded_source(b)
    if not isinstance(source, str) or not source.startswith("synth"):
        raise PermissionError(
            f"cannot auto-deprecate executor without synth provenance: {b}")

    aliases_path = C.PATH_USER_DATA / "executor_aliases.json"
    aliases_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(aliases_path.read_text()) if aliases_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        existing = {}
    if existing.get(b) == a:
        return {"alias_existing": True, "alias_from": b, "alias_to": a}
    existing[b] = a
    aliases_path.write_text(json.dumps(existing, indent=2))

    # Deprecate B
    from executor_lifecycle_state import Restriction, restrict_executor
    demoted = restrict_executor(b, restriction=Restriction.DEMOTED,
                                reason=f"duplicate of {a}")

    return {"alias_from": b, "alias_to": a, "deprecated": b, "demoted": demoted}


# --- Handler: materialize_pipeline ---------------------------------------

def apply_materialize_pipeline(ci: ChangeIntent) -> dict:
    """ESEGUE la pipeline proposta UNA volta come turno reale (2/7/2026).

    Regola dei livelli (Roberto 13/6): una pipeline di tool esistenti e'
    territorio della CACHE (L1 impara dai turni reali con args e feedback),
    non di un deposito parallelo. L'accept umano = «provala»: il turno gira
    in `scheduled_turn_scope` (valgono consent-gate outbound, notify-guard
    e le guardie del vaglio), l'esito onesto finisce nell'effect e
    l'observer giudica come per ogni apply. Se il turno funziona, L0/L1
    imparano DA SOLI dal turno vero — zero evidenza sintetica.

    (La vecchia semantica — attivare la riga in multi_tool_paths via
    path_shape_hash — e' morta con la famiglia multi_tool: store senza
    writer dall'11/6, adapter ritirato 2/7. Il body telos non aveva
    comunque mai avuto lo shape_hash: ogni accept falliva.)
    """
    body = ci.intent_body or {}
    query = (body.get("suggested_query") or ci.intent_summary or "").strip()
    if not query:
        raise ValueError("materialize_pipeline needs suggested_query in body")
    from treated_issues_guard import scheduled_turn_scope
    from agent_runtime import run_turn
    with scheduled_turn_scope():
        log = run_turn(query, actor="host", channel="telos_apply")
    fk = getattr(log, "final_kind", None) or ""
    effect = {
        "ran_query": query[:200],
        "final_kind": fk,
        "turn_id": getattr(log, "turn_id", None),
        "final_message": (getattr(log, "final_message", "") or "")[:400],
        "steps": [getattr(s, "chosen_tool", None)
                  for s in (getattr(log, "steps", None) or [])][:10],
        "effect_counts": dict(getattr(log, "effect_counts", None) or {}),
        "false_success_detected": bool(
            getattr(log, "false_success_detected", False)),
    }
    if fk == "error":
        raise RuntimeError(
            f"pipeline run failed: {effect['final_message'][:150]}")
    return effect


# --- Handler: cache_pattern ----------------------------------------------

def apply_cache_pattern(ci: ChangeIntent) -> dict:
    """Marca canonical_query_log row come 'active' (promote da
    candidate/shadow). Idempotente."""
    body = ci.intent_body or {}
    canonical = body.get("canonical_query")
    tool_name = body.get("tool_name") or ci.intent_target
    if not canonical or not tool_name:
        raise ValueError("cache_pattern needs canonical_query + tool_name in body")
    db = C.DB_MNESTOMA
    if not db.exists():
        raise RuntimeError(f"mnest.sqlite missing: {db}")
    cn = sqlite3.connect(str(db), timeout=10.0)
    try:
        cur = cn.execute(
            """UPDATE canonical_query_log SET state='active'
                WHERE canonical_query=? AND tool_name=?""",
            (canonical, tool_name),
        )
        n_updated = cur.rowcount
        cn.commit()
    finally:
        cn.close()
    if n_updated == 0:
        raise RuntimeError(
            f"no canonical_query_log row matching ({canonical}, {tool_name})"
        )
    return {"canonical_query": canonical, "tool_name": tool_name,
            "state_set_to": "active", "rows_updated": n_updated}


# --- Handler: reject_pattern ---------------------------------------------

def apply_reject_pattern(ci: ChangeIntent) -> dict:
    """Append in rejected_patterns.jsonl, letto da
    `turn_feedback.rejected_pipelines_for_query`: il planner esclude quella
    pipeline per la stessa richiesta, per tutti gli utenti."""
    body = ci.intent_body or {}
    canonical = body.get("canonical_query") or ci.intent_target
    tools = body.get("tools_sequence") or []
    if not tools:
        # Nothing to avoid: recording it would be an effect in name only.
        from messages import get as _msg
        raise RuntimeError(_msg("MSG_CHANGE_REJECT_EMPTY"))
    out_path = C.PATH_USER_DATA / "rejected_patterns.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "canonical_query": canonical,
        "tools_sequence": tools,
        "n_rejections": body.get("n_rejections"),
        "applied_by": "change_applier",
        "change_intent_id": ci.id,
    }
    # Dedup: skip se identico record gia' presente
    if out_path.exists():
        try:
            with out_path.open() as fp:
                for line in fp:
                    try:
                        ex = json.loads(line)
                        if (ex.get("canonical_query") == canonical
                                and ex.get("tools_sequence") == tools):
                            return {"already_present": True,
                                    "canonical_query": canonical}
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass
    with out_path.open("a") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"canonical_query": canonical, "tools_sequence": tools,
            "appended_to": str(out_path)}


# --- Dispatcher ----------------------------------------------------------

_HANDLERS: dict[str, Callable[[ChangeIntent], dict]] = {
    KIND_CREATE_EXECUTOR:      apply_create_executor,
    KIND_EXTEND_EXECUTOR:      apply_extend_executor,
    KIND_DEDUPE_EXECUTORS:     apply_dedupe_executors,
    KIND_MATERIALIZE_PIPELINE: apply_materialize_pipeline,
    KIND_CACHE_PATTERN:        apply_cache_pattern,
    KIND_REJECT_PATTERN:       apply_reject_pattern,
}


def task_change_applier(payload: dict | None = None) -> dict:
    """Daemon job. Legge ACCEPTED, applica, transition a APPLIED o FAILED.

    Cap interno: max 20 intent per fire (evita un fire infinito).
    Long-running handler (create_executor ~150s) bloccherebbe il
    scheduler — meglio batchare e lasciare al fire successivo.
    """
    max_per_fire = (payload or {}).get("max_per_fire", 20)
    try:
        accept_for_building()
    except Exception as exc:  # noqa: BLE001 - the decided ones are applied anyway
        _log.warning("change_applier: accept_for_building failed: %r", exc)
    accepted = list_intents(state=STATE_ACCEPTED, limit=max_per_fire,
                             order_by="discovered_desc")
    applied_count = 0
    failed_count = 0
    skipped_count = 0
    details: list[dict] = []
    for ci in accepted:
        handler = _HANDLERS.get(ci.intent_kind)
        if handler is None:
            mark_failed(ci.id, reason=f"no handler for kind={ci.intent_kind}")
            failed_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "failed_no_handler"})
            _audit({"event": "no_handler", "id": ci.id, "kind": ci.intent_kind})
            continue
        try:
            effect = handler(ci)
        except ReviewPending as pending:
            # Still accepted: the next run checks again without asking Synth.
            push_ready_candidate(ci, pending.proposal_id)
            skipped_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "review_pending",
                            "proposal_id": pending.proposal_id})
            _audit({"event": "review_pending", "id": ci.id,
                    "kind": ci.intent_kind, "proposal_id": pending.proposal_id})
            continue
        except ExternalModelConsentNeeded as need:
            # What the person decided once (ADR 0240): always, never, or the
            # question each time, which the page shows first.
            import generated_tools_policy
            choice = generated_tools_policy.external_model()
            if choice == generated_tools_policy.ALWAYS:
                update_body(ci.id, external_model_consent={
                    "by": "preference", "ts": _iso_now()})
                skipped_count += 1
                details.append({"id": ci.id, "kind": ci.intent_kind,
                                "result": "external_model_by_preference"})
                _audit({"event": "external_model_by_preference", "id": ci.id,
                        "kind": ci.intent_kind})
                continue
            mark_failed(ci.id, reason=str(need))
            never = choice == generated_tools_policy.NEVER
            if never:
                close_until(ci.id, reason="external model not allowed by preference")
            else:
                update_body(ci.id, external_model_offer=True)
                push_external_model_question(ci)
            failed_count += 1
            result = "closed_by_preference" if never else "external_model_consent_needed"
            details.append({"id": ci.id, "kind": ci.intent_kind, "result": result})
            _audit({"event": result, "id": ci.id, "kind": ci.intent_kind})
            continue
        except ClosedAfterExternalModel as closed:
            mark_failed(ci.id, reason=str(closed))
            close_until(ci.id, reason=str(closed)[:500])
            failed_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "closed_after_external_model"})
            _audit({"event": "closed_after_external_model", "id": ci.id,
                    "kind": ci.intent_kind})
            continue
        except NotImplementedError as exc:
            mark_failed(ci.id, reason=f"not_yet_implemented: {exc}")
            skipped_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "skipped_not_implemented",
                            "reason": str(exc)})
            _audit({"event": "not_implemented", "id": ci.id,
                    "kind": ci.intent_kind, "reason": str(exc)})
            continue
        except Exception as exc:
            mark_failed(ci.id, reason=f"{type(exc).__name__}: {exc}")
            failed_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "failed", "error": str(exc)[:200]})
            _audit({"event": "failed", "id": ci.id, "kind": ci.intent_kind,
                    "error": str(exc)[:200]})
            continue
        mark_applied(ci.id, effect=effect)
        applied_count += 1
        details.append({"id": ci.id, "kind": ci.intent_kind,
                        "result": "applied", "effect": effect})
        _audit({"event": "applied", "id": ci.id, "kind": ci.intent_kind,
                "effect": effect})

    return {
        "ok": True,
        "n_accepted": len(accepted),
        "applied": applied_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "details": details[:10],  # cap audit log
    }


if __name__ == "__main__":
    import pprint
    pprint.pprint(task_change_applier())
