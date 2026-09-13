"""Pending dialogs stay in the conversation that opened them (I-022).

Live defect (13/9/2026): a question opened in one HTTP chat was stored under
the legacy ``http:<actor>`` coordinate, shared by every chat of the actor, and
the next request in another chat was taken as its answer.  The fallback on
that coordinate now keeps only dialogs recorded for the current conversation.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

OWNER = "owner-scope"
LEGACY = "http:host"
CONV_A, CONV_B = "conv-a", "conv-b"


def _sender(conv: str) -> str:
    return f"http:{OWNER}:{conv}"


@pytest.fixture
def dp(tmp_path, monkeypatch):
    import dialog_pending
    monkeypatch.setattr(dialog_pending, "DIALOG_DIR", tmp_path / "dialogs")
    return dialog_pending


def _save(dp, dialog_id, *, conversation=None, on_complete_conversation=None,
          owner=OWNER, started_at=None):
    state = {
        "dialog_id": dialog_id,
        "title": "t",
        "dialog": [
            {"var": "first", "prompt": "Q1", "schema": {"kind": "text"}},
            {"var": "second", "prompt": "Q2", "schema": {"kind": "text"}},
        ],
        "step_index": 0,
        "values_collected": {},
        "started_at": started_at or dp._utc_now_iso(),
        "timeout_s": 600,
        "owner_user_id": owner,
        "completed": False,
        "cancelled": False,
        "on_complete": {"type": "resume"},
    }
    if conversation is not None:
        state["conversation_id"] = conversation
    if on_complete_conversation is not None:
        state["on_complete"]["conversation_id"] = on_complete_conversation
    dp.save_pending(LEGACY, dialog_id, state)


def _reply(query, conv, owner=OWNER):
    import http_routes_agent as routes
    return routes._apply_dialog_pending(
        _sender(conv), query, actor="host", channel="http",
        conversation_id=conv, owner_user_id=owner)


def _step(dp, dialog_id):
    return dp.load_pending(LEGACY, dialog_id,
                           owner_user_id=OWNER)["step_index"]


def _active(dp):
    return {d["dialog_id"] for d in dp.list_pending(
        LEGACY, owner_user_id=OWNER)}


@pytest.mark.parametrize("where", ["top", "on_complete"])
def test_other_conversation_does_not_answer(dp, where):
    kw = ({"conversation": CONV_A} if where == "top"
          else {"on_complete_conversation": CONV_A})
    _save(dp, "a" * 16, **kw)

    assert _reply("una risposta", CONV_B) is None
    assert _step(dp, "a" * 16) == 0

    assert _reply("una risposta", CONV_A) is not None
    assert _step(dp, "a" * 16) == 1


def test_cancel_in_other_conversation_leaves_dialog(dp):
    _save(dp, "a" * 16, conversation=CONV_A)

    assert _reply("annulla", CONV_B) is None
    assert _active(dp) == {"a" * 16}


def test_dialog_without_conversation_needs_request_without_one(dp):
    _save(dp, "c" * 16)

    assert _reply("una risposta", CONV_A) is None
    assert _reply("annulla", CONV_A) is None
    assert _step(dp, "c" * 16) == 0

    assert _reply("una risposta", "") is not None
    assert _step(dp, "c" * 16) == 1


def test_newer_dialog_of_other_conversation_is_not_chosen(dp):
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 30))
    _save(dp, "a" * 16, conversation=CONV_A, started_at=old + "Z")
    _save(dp, "b" * 16, conversation=CONV_B)

    assert _reply("una risposta", CONV_A) is not None
    assert (_step(dp, "a" * 16), _step(dp, "b" * 16)) == (1, 0)


def test_other_owner_is_not_visible(dp):
    _save(dp, "a" * 16, conversation=CONV_A, owner="someone-else")

    assert _reply("una risposta", CONV_A) is None


def test_tutor_probe_follows_the_same_rule(dp):
    import http_routes_agent as routes
    _save(dp, "a" * 16, conversation=CONV_A)

    assert routes._http_has_pending(_sender(CONV_B), "host", OWNER, CONV_B) is False
    assert routes._http_has_pending(_sender(CONV_A), "host", OWNER, CONV_A) is True


def _cap_expand_turn():
    """Real writer: a truncated HTTP step turned into a cap-expand question."""
    from agent_runtime import StepLog, TurnLog

    log = TurnLog(ts_start=time.time(), user_query="stato del server",
                  actor="host", channel="http", owner_user_id=OWNER,
                  conversation_id=CONV_A)
    log.final_kind = "answer"
    log.final_message = "primi 10"
    step = StepLog(step_num=1, chosen_tool="get_processes",
                   raw_args={"top": 10, "filters": []})
    step.result = {
        "ok": True, "truncated": True, "truncated_what": "processi",
        "used": 10, "available_total": 487, "cap_field": "top",
        "cap_value": 10, "entries": [{"pid": i} for i in range(10)],
    }
    log.steps.append(step)
    log._orchestrate_cap_expand_dialog(log._collect_expandable_caps()[0])
    return log


def _stub_completion(monkeypatch):
    import orchestration

    calls = []
    monkeypatch.setattr(
        orchestration, "process_completion_callback",
        lambda sender, did, **kw: calls.append((sender, did))
        or SimpleNamespace(text="allargato"))
    return calls


def test_cap_expand_question_answers_only_in_its_chat(dp, monkeypatch):
    import http_routes_agent as routes

    log = _cap_expand_turn()
    proposal = log.expandable_caps[0]
    dialog_id = proposal["dialog_id"]
    assert proposal["sender_for_state"] == LEGACY

    state = dp.load_pending(LEGACY, dialog_id, owner_user_id=OWNER)
    assert state["on_complete"]["conversation_id"] == CONV_A

    calls = _stub_completion(monkeypatch)

    assert _reply("sì", CONV_B) is None
    assert _reply("annulla", CONV_B) is None
    assert routes._http_has_pending(_sender(CONV_B), "host", OWNER, CONV_B) is False
    assert calls == []
    # The form link carries the explicit id and still reaches the dialog.
    assert routes._resolve_dialog_state(None, dialog_id)["dialog_id"] == dialog_id

    assert _reply("sì", CONV_A) == "allargato"
    assert calls == [(LEGACY, dialog_id)]


def test_pre_existing_record_completes_through_scoped_proposal(
        dp, tmp_path, monkeypatch):
    from channels import daemon
    import http_routes_agent as routes

    monkeypatch.setattr(daemon, "CAP_PENDING_DIR", tmp_path / "caps")
    log = _cap_expand_turn()
    dialog_id = log.expandable_caps[0]["dialog_id"]
    routes._save_cap_pending_if_any(
        _sender(CONV_A), "stato del server", log, owner_user_id=OWNER)
    # A record written before the writer recorded its conversation.
    state = dp.load_pending(LEGACY, dialog_id, owner_user_id=OWNER)
    del state["on_complete"]["conversation_id"]
    dp.save_pending(LEGACY, dialog_id, state)
    calls = _stub_completion(monkeypatch)

    for query in ("sì", "annulla"):
        assert _reply(query, CONV_B) is None
        assert routes._apply_cap_pending(
            _sender(CONV_B), query, "host",
            owner_user_id=OWNER) == (query, None, None)
    assert calls == [] and dialog_id in _active(dp)

    assert _reply("sì", CONV_A) is None
    _query, consumed, message = routes._apply_cap_pending(
        _sender(CONV_A), "sì", "host", owner_user_id=OWNER)
    assert message == "allargato"
    assert consumed["dialog_id"] == dialog_id
    assert calls == [(LEGACY, dialog_id)]
