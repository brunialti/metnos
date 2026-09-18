"""Un guasto del servizio non si fa riparare all'utente (6/8/2026).

Turno reale `71d4ac52`: `open_sites` fallisce con `sidecar_contract_mismatch` —
il sidecar Playwright girava dal 2/8 e un modulo di confine era cambiato, quindi
la sua impronta non combaciava piu' — e la risposta all'utente e' stata «Non
posso risolvere: Operazione fallita: open_sites. Per procedere: **Aggiungi
dettagli concreti (percorso, nome, periodo)**». Cioe': un processo da riavviare,
presentato come una richiesta scritta male.

La classe c'era gia' («sidecar_down», «schema_too_old» stanno in
`OPERATIONAL_ERROR_CLASSES` da sempre); mancava solo questa. Il test non guarda
il caso: guarda la PROPRIETA' — nessun guasto operativo, presente o futuro, puo'
finire nell'azione che chiede dettagli all'utente.
"""
from __future__ import annotations

import pytest

from engine.terminator import SimpleTerminator
from engine.recovery import SimpleRecovery, classify_error, is_recoverable
from engine.types import (OPERATIONAL_ERROR_CLASSES, Intent, RunResult, StepRun,
                          result_error_detail)


def _run_fallito(classe: str) -> RunResult:
    return RunResult(
        steps=[StepRun(step_idx=1, tool="open_sites", args={},
                       result={"ok": False, "error_class": classe,
                               "error": "Operazione fallita: open_sites."},
                       ok=False, latency_ms=1)],
        final_kind="error", ok_count=0)


@pytest.mark.parametrize("classe", sorted(OPERATIONAL_ERROR_CLASSES))
def test_un_guasto_operativo_non_chiede_dettagli_all_utente(classe) -> None:
    from messages import get as _msg

    risposta = SimpleTerminator().explain(
        query="apri il sito", intent=Intent(verb="open", object="sites"),
        failed_run=_run_fallito(classe), error_class="wrong_args")
    assert _msg("MSG_TERM_WRONG_ARGS_ACTION") not in risposta.final_text, (
        f"{classe}: guasto del servizio presentato come richiesta da correggere")


def test_il_contratto_del_sidecar_e_un_guasto_operativo() -> None:
    """Regressione puntuale del turno 71d4ac52: la classe deve stare
    nell'insieme, altrimenti anche il resto della catena (recovery compresa)
    tratta un processo stantio come un piano da rifare."""
    assert "sidecar_contract_mismatch" in OPERATIONAL_ERROR_CLASSES


@pytest.mark.parametrize("classe", [
    "search_backend_unavailable",
    "search_backend_invalid",
    "search_relevance_unavailable",
])
def test_search_service_failures_do_not_trigger_query_replanning(classe):
    run = _run_fallito(classe)
    assert classe in OPERATIONAL_ERROR_CLASSES
    assert classify_error(run) == "out_of_scope"
    assert not is_recoverable(classify_error(run))


def test_empty_search_gets_specific_action_without_double_period(monkeypatch):
    import engine.terminator as term
    from messages import get as _msg

    monkeypatch.setattr(term, "_record_lacuna", lambda *args: "isolated-test")
    run = RunResult(
        steps=[StepRun(
            step_idx=1, tool="find_urls", args={"search_query": "x"},
            result={"ok": False, "error_class": "search_no_results",
                    "error": _msg("MSG_NO_RESULTS"), "entries": []},
            ok=False, latency_ms=1,
        )],
        final_kind="error", ok_count=0,
    )
    response = SimpleTerminator().explain(
        query="cerca x", intent=Intent(verb="find", object="urls"),
        failed_run=run, error_class=classify_error(run),
    )
    assert _msg("MSG_LOOP_BREAK_HINT_URLS") in response.final_text
    assert _msg("MSG_TERM_WRONG_ARGS_ACTION") not in response.final_text
    assert ".." not in response.final_text


def test_operation_failed_is_operational_and_does_not_replan():
    run = _run_fallito("operation_failed")
    assert "operation_failed" in OPERATIONAL_ERROR_CLASSES
    assert classify_error(run) == "out_of_scope"
    assert not is_recoverable(classify_error(run))

    class NoReplan:
        calls = 0

        def propose(self, **kwargs):
            self.calls += 1

    proposer = NoReplan()
    assert SimpleRecovery().recover(
        failed_run=run, query="esegui il controllo", intent=Intent(),
        pool=["open_sites"], proposer=proposer,
    ) is None
    assert proposer.calls == 0


@pytest.mark.parametrize("classe,expected", [
    ("invalid_input", "wrong_args"), ("ambiguous_input", "wrong_args"),
    ("conflict", "wrong_args"), ("unsafe_target", "wrong_args"),
    ("permission_denied", "wrong_args"), ("wrong_args", "wrong_args"),
    ("wrong_tool", "wrong_tool"), ("missing_input", "missing_input"),
])
def test_argument_error_recovery_is_unchanged(classe, expected):
    assert classe not in OPERATIONAL_ERROR_CLASSES
    assert classify_error(_run_fallito(classe)) == expected
    assert is_recoverable(expected)


@pytest.mark.parametrize("fields,expected", [
    ({"final_message_hint": "Hint", "error": "Error", "message": "Message",
      "failed": [{"error": "Item"}]}, "Hint"),
    ({"error": "Error", "message": "Message",
      "failed": [{"error": "Item"}]}, "Error"),
    ({"message": "Message", "failed": [{"error": "Item"}]}, "Message"),
    ({"failed": [{"error": "Item"}, {"error": "Item"}, {"message": "Next"}]},
     "Item; Next"),
    ({"failed": [{"error": str(i)} for i in range(5)]}, "0; 1; 2"),
])
def test_summary_does_not_replace_existing_priority(fields, expected):
    assert result_error_detail({"ok": False, "summary": "Summary", **fields}) == expected


@pytest.mark.parametrize("fields", [{}, {"failed": []}, {"failed": "invalid"},
                                    {"failed": [None, {}, {"error": " "}]}])
def test_failed_summary_is_last_resort(fields):
    assert result_error_detail({
        "ok": False, "summary": "  Causa osservata.  ", **fields,
    }) == "Causa osservata."


@pytest.mark.parametrize("fields", [{}, {"ok": True}, {"ok": None},
                                    {"ok": 0}, {"ok": "false"}])
def test_summary_requires_literal_failure(fields):
    assert result_error_detail({"summary": "Not an error", **fields}) == ""


@pytest.mark.parametrize("summary", [None, 17, True, {}, [], "", "   "])
def test_summary_requires_nonempty_text(summary):
    assert result_error_detail({"ok": False, "summary": summary}) == ""


def test_summary_redacts_before_truncation():
    # The cap crosses the secret, so truncating first would leave a fragment.
    detail = "diagnosi " * 129 + "password: " + "X" * 80
    clean = result_error_detail({"ok": False, "summary": detail})
    assert "XXXXX" not in clean
    assert "<REDACTED:cred>" in clean
    long = result_error_detail({"ok": False, "summary": "Motivo disponibile. " * 500})
    assert long.startswith("Motivo disponibile.")
    assert long.endswith("…") and len(long) <= 1201


@pytest.mark.parametrize("detail", [
    "DUPLICATE_CALL request_new_executor rejected",
    "Motivo disponibile. " * 100 + "DUPLICATE_CALL request_new_executor rejected",
])
def test_summary_internal_leak_check_precedes_truncation(detail):
    assert result_error_detail({"ok": False, "summary": detail}) == ""


@pytest.mark.parametrize("component", ["redactor", "leak_check"])
def test_summary_safety_failure_is_closed(monkeypatch, component):
    import credential_intake
    import detection_lexicon_seed_runtime_safety as safety

    def fail(*args, **kwargs):
        raise RuntimeError("controlled safety failure")

    if component == "redactor":
        monkeypatch.setattr(credential_intake, "scrub_sensitive_text", fail)
    else:
        monkeypatch.setattr(safety, "matches", fail)
    assert result_error_detail({
        "ok": False, "summary": "Accesso negato; password: secret-for-i030",
    }) == ""


def test_summary_missing_safety_lexicon_is_closed(monkeypatch):
    import detection_lexicon_seed_runtime_safety as safety

    monkeypatch.setattr(safety, "patterns", lambda concept: ())
    assert result_error_detail({"ok": False, "summary": "Causa osservata."}) == ""


def test_missing_reason_uses_generic_cause_without_invention(monkeypatch):
    import engine.terminator as term
    from messages import get as msg

    monkeypatch.setattr(term, "_record_lacuna", lambda *args: "isolated-test")
    run = _run_fallito("operation_failed")
    run.steps[0].result.pop("error")
    response = SimpleTerminator().explain(
        query="esegui il controllo", intent=Intent(), failed_run=run,
        error_class=classify_error(run),
    )
    assert response.root_cause == msg("MSG_TERM_OUT_OF_SCOPE_CAUSE")
    assert msg("MSG_TERM_WRONG_ARGS_ACTION") not in response.final_text
    assert not any(word in response.final_text.lower()
                   for word in ("permess", "permission", "offline", "errno"))
