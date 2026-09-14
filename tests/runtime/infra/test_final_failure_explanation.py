"""I-025: observed failures survive the real terminal presentation path.

Only the sudoer invocation is simulated in the ping reproduction. No command
is executed, and turn logs use an isolated directory and a test-only actor.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import agent_runtime as ar


@pytest.fixture
def make_log(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "TURN_LOG_DIR", tmp_path / "turns")

    def make(result, *, tool="admin", kind="answer", message="Fatto.",
             intent="run"):
        step = ar.StepLog(step_num=1, chosen_tool=tool, result=result)
        return ar.TurnLog(
            ts_start=1.0, user_query="esegui il controllo richiesto",
            channel="test", actor="final-failure-explanation-tests",
            owner_user_id="final-failure-explanation-tests",
            intent_verb=intent, steps=[step], final_kind=kind,
            final_message=message, error_class=result.get("error_class", ""),
        )

    return make


@pytest.fixture
def ping_failure(monkeypatch):
    import loader
    import system.admin as admin
    from safety.canonicalize import validate_argv

    validated = validate_argv(["ping", "-n", "-c", "1", "127.0.0.1"])
    decision = admin.AdminDecision(
        kind="execute_silent", argv=list(validated.argv),
        signature=str(validated.signature), validated_argv=validated,
    )

    def failed_sudoer(*args, **kwargs):
        assert args == ("sudoer",)
        assert kwargs["validated_argv"] is validated
        return SimpleNamespace(
            ok=False, status="failed", exit_code=2, stdout="",
            stderr="ping: socket: Operation not permitted", duration_ms=1,
        )

    monkeypatch.setattr(loader, "invoke_verb_unique", failed_sudoer)
    result = admin._standardize_result(admin._spawn_via_sudoer(
        decision=decision, intent_text="esegui ping", actor="host",
    ))
    # Same observed shape as turn 6de4d257b1a54155: operation_failed,
    # without a structured errno. Stderr here is a controlled reproduction,
    # not a claim that I-023 read the private turn's raw stderr.
    assert result["ok"] is False
    assert result["error_class"] == "operation_failed"
    assert "errno" not in result
    assert not result.get("error") and not result.get("final_message_hint")
    return result


@pytest.mark.parametrize("kind,message", [
    ("answer", "Fatto."), ("answer", "observed-summary"),
    ("error", ""), ("loop_break", ""), ("ask", ""),
])
def test_ping_failure_reason_survives_write(make_log, ping_failure, kind, message):
    if message == "observed-summary":
        message = ping_failure["summary"]
    log = make_log(ping_failure, kind=kind, message=message)
    log.write()
    assert "Operation not permitted" in log.final_message
    assert "Fatto." not in log.final_message
    assert "Riformula" not in log.final_message
    assert "errno" not in log.final_message
    assert "offline" not in log.final_message.lower()
    record = json.loads(next(ar.TURN_LOG_DIR.glob("*.jsonl")).read_text())
    assert record["final_message"] == log.final_message


@pytest.mark.parametrize("kind,message", [("answer", "Fatto."), ("error", "")])
def test_summary_fallback_is_not_admin_specific(make_log, kind, message):
    log = make_log({"ok": False, "summary": "Applicazione non avviata: accesso negato."},
                   tool="run_processes", kind=kind, message=message)
    log.write()
    assert "Applicazione non avviata: accesso negato." in log.final_message
    assert "Fatto." not in log.final_message


@pytest.mark.parametrize("kind,message", [("answer", "Fatto."), ("error", "")])
def test_hint_precedes_other_explanations(make_log, kind, message):
    hint = "Il controllo non è partito: accesso negato."
    log = make_log({"ok": False, "final_message_hint": hint,
                    "error": "lower_priority_error", "summary": "lower_priority_summary",
                    "validation_failures": ["lower_priority_validation"]},
                   kind=kind, message=message)
    log.write()
    assert log.final_message == hint


@pytest.mark.parametrize("field,value", [
    ("error", "Connessione rifiutata."),
    ("failed", [{"error": "Connessione rifiutata."}]),
    ("validation_failures", ["Connessione rifiutata."]),
])
def test_structured_failure_precedes_summary(make_log, field, value):
    log = make_log({"ok": False, field: value, "summary": "lower_priority_summary"},
                   kind="error", message="")
    log.write()
    assert "Connessione rifiutata." in log.final_message
    assert "lower_priority_summary" not in log.final_message


@pytest.mark.parametrize("field", ["summary", "final_message_hint", "error",
                                    "failed", "validation_failures"])
@pytest.mark.parametrize("kind,message", [("answer", "Fatto."), ("error", "")])
def test_added_explanations_redact_secrets(make_log, field, kind, message):
    detail = "Accesso negato; password: secret-for-i025"
    value = ([{"error": detail}] if field == "failed" else
             [detail] if field == "validation_failures" else detail)
    log = make_log({"ok": False, field: value}, kind=kind, message=message)
    log.write()
    assert "secret-for-i025" not in log.final_message
    assert "REDACTED:cred" in log.final_message


def test_redaction_failure_never_returns_raw_summary(make_log, monkeypatch):
    scrub = ar._scrub_credentials
    detail = "Accesso negato; password: secret-for-i025"

    def failing_scrub(text):
        if text == detail:
            raise RuntimeError("redactor unavailable")
        return scrub(text)

    monkeypatch.setattr(ar, "_scrub_credentials", failing_scrub)
    log = make_log({"ok": False, "summary": detail}, kind="error", message="")
    log.write()
    assert log.final_message
    assert "secret-for-i025" not in log.final_message
    assert "Accesso negato" not in log.final_message


def test_summary_is_bounded_after_redaction(make_log):
    log = make_log({"ok": False, "summary": "Motivo disponibile. " * 500},
                   kind="error", message="")
    log.write()
    assert "Motivo disponibile." in log.final_message
    assert len(log.final_message) < 1400
    assert "…" in log.final_message


def test_summary_redaction_precedes_cut_inside_secret(make_log):
    secret = "X" * 80
    log = make_log({"ok": False,
                    "summary": "diagnosi " * 129 + "password: " + secret},
                   kind="error", message="")
    log.write()
    assert "XXXXX" not in log.final_message
    assert "REDACTED:cred" in log.final_message


def test_each_item_error_remains_humanized_and_deduplicated(make_log):
    log = make_log({"ok": False, "failed": [
        {"error": "no_verified_channel:first"},
        {"error": "no_verified_channel:second"},
    ]}, kind="error", message="")
    log.write()
    reason = ar.msg("ERR_NO_VERIFIED_CHANNEL")
    assert not reason.startswith("<missing:")
    assert log.final_message.count(reason) == 1
    assert "no_verified_channel" not in log.final_message


def test_meta_and_finalizer_steps_cannot_supply_failure_reason(make_log):
    log = make_log({"ok": False, "summary": "Motivo osservato."},
                   kind="error", message="")
    log.steps.extend([
        ar.StepLog(step_num=2, chosen_tool="admin", error="duplicate_call_blocked",
                   result={"ok": False, "final_message_hint": "meta_only"}),
        ar.StepLog(step_num=3, chosen_tool="final_answer",
                   result={"ok": False, "final_message_hint": "narrator_only"}),
    ])
    log.write()
    assert "Motivo osservato." in log.final_message
    assert "meta_only" not in log.final_message
    assert "narrator_only" not in log.final_message


def test_internal_summary_is_not_exposed(make_log):
    log = make_log({"ok": False, "summary": "DUPLICATE_CALL request_new_executor rejected"},
                   kind="error", message="")
    log.write()
    assert log.final_message
    assert "DUPLICATE_CALL" not in log.final_message
    assert "request_new_executor" not in log.final_message


def test_absent_reason_does_not_invent_permission_or_offline(make_log):
    log = make_log({"ok": False, "error_class": "operation_failed"},
                   kind="error", message="")
    log.write()
    assert log.final_message
    assert not any(word in log.final_message.lower()
                   for word in ("permess", "permission", "offline", "errno"))


@pytest.mark.parametrize("ok", [True, None])
def test_nonfailed_summary_is_not_an_error_explanation(make_log, ok):
    log = make_log({"ok": ok, "summary": "not_a_failure_reason"})
    assert ar._compose_honest_from_last_error(log, fallback=False) == ""
    assert ar._compose_honest_from_last_error(log) == ar.msg("MSG_FINAL_FALLBACK_GENERIC")


def test_success_message_unchanged(make_log):
    log = make_log({"ok": True, "decision": "execute_silent"},
                   message="Il controllo è terminato regolarmente.")
    log.write()
    assert log.final_message == "Il controllo è terminato regolarmente."


@pytest.mark.parametrize("error_class", ["timeout", "remote_timeout"])
def test_timeout_still_reports_uncertainty(make_log, error_class):
    log = make_log({"ok": False, "error_class": error_class,
                    "summary": "Nessuna operazione eseguita."},
                   tool="delete_files", intent="delete")
    log.write()
    assert "Nessuna operazione eseguita." not in log.final_message
    assert "Fatto." not in log.final_message
    assert log.final_message == ar.msg(
        "MSG_MUTATE_TIMEOUT_UNCERTAIN", tool="delete_files", device="server")


def test_partial_effects_and_failures_remain_visible(make_log):
    log = make_log({"ok": False, "ok_count": 1, "fail_count": 1,
                    "results": [{"ok": True, "path": "completato.txt"}],
                    "failed": [{"path": "rifiutato.txt", "error": "accesso negato"}],
                    "summary": "Nessuna operazione eseguita."},
                   tool="delete_files", intent="delete")
    log.write()
    assert log.effect_counts["mutations"] == 1
    assert "rifiutato.txt" in log.final_message
    assert "accesso negato" in log.final_message
    assert "Nessuna operazione eseguita." not in log.final_message


@pytest.mark.parametrize("kind", ["answer", "error"])
def test_real_admin_cause_survives_terminator_then_write(
        make_log, ping_failure, monkeypatch, kind):
    import engine.terminator as term
    from engine.recovery import classify_error
    from engine.types import Intent, RunResult, StepRun, result_error_detail

    monkeypatch.setattr(term, "_record_lacuna", lambda *args: "isolated-i030")
    run = RunResult(steps=[StepRun(
        step_idx=1, tool="admin", args={}, result=ping_failure,
        ok=False, latency_ms=1,
    )], final_kind="error", ok_count=0)
    assert classify_error(run) == "out_of_scope"
    response = term.SimpleTerminator().explain(
        query="esegui il controllo", intent=Intent(verb="get", object="status"),
        failed_run=run, error_class=classify_error(run),
    )
    # Check BEFORE write: the unfulfilled-mutation guard must not rescue a
    # broken terminator and make this integration test spuriously green.
    assert "Operation not permitted" in response.root_cause
    assert response.root_cause == result_error_detail(ping_failure)
    assert response.suggested_action == ar.msg("MSG_CHAT_FB_RETRY")
    log = make_log(ping_failure, kind=kind, message=response.final_text, intent="get")
    assert response.root_cause in ar._compose_honest_from_last_error(log)
    log.write()
    assert log.final_message == response.final_text
    record = json.loads(next(ar.TURN_LOG_DIR.glob("*.jsonl")).read_text())
    assert record["final_message"] == response.final_text


def test_non_admin_summary_is_safe_through_terminator_and_write(make_log, monkeypatch):
    import engine.terminator as term
    from engine.recovery import classify_error
    from engine.types import Intent, RunResult, StepRun

    monkeypatch.setattr(term, "_record_lacuna", lambda *args: "isolated-i030")
    result = {"ok": False, "error_class": "operation_failed",
              "summary": "Applicazione non avviata; password: secret-for-i030"}
    run = RunResult(steps=[StepRun(
        step_idx=1, tool="run_processes", args={}, result=result,
        ok=False, latency_ms=1,
    )], final_kind="error", ok_count=0)
    response = term.SimpleTerminator().explain(
        query="esegui il controllo", intent=Intent(), failed_run=run,
        error_class=classify_error(run),
    )
    assert "Applicazione non avviata" in response.root_cause
    assert "secret-for-i030" not in response.final_text
    assert "<REDACTED:cred>" in response.final_text
    log = make_log(result, tool="run_processes", kind="error",
                   message=response.final_text, intent="get")
    assert response.root_cause in ar._compose_honest_from_last_error(log)
    log.write()
    assert log.final_message == response.final_text
