"""Test della migrazione cap-expand → get_inputs (FIX 1, 6/5/2026).

Verifica che `TurnLog._orchestrate_cap_expand_dialog`:
- emetta un dialog_pending persistente al posto della stringa "rispondi sì";
- sostituisca expandable_caps con kind=get_inputs_response;
- inietti `on_complete=expand_cap_and_resume` con tutti i campi necessari
  (executor, cap_field, cap_suggested, args_suggested, preview_label).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


@pytest.fixture
def isolated_dialog_dir(tmp_path, monkeypatch):
    import dialog_pending
    monkeypatch.setattr(dialog_pending, "DIALOG_DIR",
                          tmp_path / "get_inputs")
    yield tmp_path


def _make_turnlog_with_truncated_step(executor: str = "get_processes",
                                        cap_field: str = "top",
                                        cap_value: int = 10,
                                        available_total: int = 487,
                                        used: int = 10,
                                        truncated_what: str = "processi",
                                        actor: str = "host",
                                        channel: str = "telegram"):
    from agent_runtime import TurnLog, StepLog
    log = TurnLog(ts_start=time.time(), user_query="stato del server",
                   actor=actor, channel=channel)
    log.final_kind = "answer"
    log.final_message = "Ecco i primi 10 processi…"
    step = StepLog(step_num=1, chosen_tool=executor,
                    raw_args={"top": cap_value, "filters": []})
    step.result = {
        "ok": True,
        "truncated": True,
        "truncated_what": truncated_what,
        "used": used,
        "available_total": available_total,
        "cap_field": cap_field,
        "cap_value": cap_value,
        "entries": [{"name": "python", "pid": i} for i in range(used)],
    }
    log.steps.append(step)
    return log


def test_orchestrate_cap_expand_replaces_expandable_caps(isolated_dialog_dir):
    """Dopo write(), expandable_caps[0] è get_inputs_response e dialog_pending salvato."""
    import dialog_pending
    log = _make_turnlog_with_truncated_step()
    log.ts_end = log.ts_start + 0.1
    # Simula la chiamata a write() ma senza scrivere il jsonl: invochiamo
    # i due step rilevanti (cap collect + orchestrate) direttamente.
    log.expandable_caps = log._collect_expandable_caps()
    assert log.expandable_caps[0]["kind"] == "cap_expand"
    proposal = log.expandable_caps[0]

    log._orchestrate_cap_expand_dialog(proposal)

    assert log.expandable_caps, "expandable_caps non deve essere vuoto dopo orchestration"
    assert log.expandable_caps[0]["kind"] == "get_inputs_response"
    assert "dialog_id" in log.expandable_caps[0]
    assert log.expandable_caps[0]["sender_for_state"] == "telegram:host"

    # Hint nel final_message.
    assert "Step 1/1" in log.final_message or "Allargo" in log.final_message
    # Original message preservato (orchestration appende, non sostituisce).
    assert "Ecco i primi 10 processi" in log.final_message

    # Dialog state persisted con on_complete corretto.
    state = dialog_pending.load_pending(
        log.expandable_caps[0]["sender_for_state"],
        log.expandable_caps[0]["dialog_id"],
    )
    assert state is not None
    on_complete = state["on_complete"]
    assert on_complete["type"] == "expand_cap_and_resume"
    assert on_complete["executor"] == "get_processes"
    assert on_complete["cap_field"] == "top"
    assert on_complete["cap_suggested"] >= 487  # almeno available_total
    assert on_complete["args_suggested"]["top"] == on_complete["cap_suggested"]
    assert on_complete["preview_label"] == "processi"


def test_orchestrate_cap_expand_uses_default_label_if_no_truncated_what(
        isolated_dialog_dir):
    """Senza truncated_what, preview_label = fallback i18n (§11)."""
    log = _make_turnlog_with_truncated_step(truncated_what="")
    # Override: rimuovi truncated_what dallo step result
    log.steps[0].result.pop("truncated_what", None)
    proposal = log._collect_expandable_caps()[0]
    log._orchestrate_cap_expand_dialog(proposal)

    import dialog_pending
    state = dialog_pending.load_pending(
        log.expandable_caps[0]["sender_for_state"],
        log.expandable_caps[0]["dialog_id"],
    )
    from messages import get as _msg
    assert state["on_complete"]["preview_label"] == _msg("MSG_TRUNCATED_DEFAULT_WHAT")


def test_orchestrate_cap_expand_sender_id_no_channel(isolated_dialog_dir):
    """Senza channel, sender_id = actor only (CLI / test)."""
    log = _make_turnlog_with_truncated_step(channel="")
    proposal = log._collect_expandable_caps()[0]
    log._orchestrate_cap_expand_dialog(proposal)
    assert log.expandable_caps[0]["sender_for_state"] == "host"


def test_orchestrate_cap_expand_malformed_proposal_no_op(isolated_dialog_dir):
    """Proposta senza executor → no-op, expandable_caps preservato."""
    log = _make_turnlog_with_truncated_step()
    bad_proposal = {"kind": "cap_expand"}  # nessun campo richiesto
    initial_msg = log.final_message
    log._orchestrate_cap_expand_dialog(bad_proposal)
    # final_message intatto (no orchestration eseguita).
    assert log.final_message == initial_msg


def test_truncation_is_passive_notice_no_dialog(isolated_dialog_dir, tmp_path,
                                                monkeypatch):
    """Troncamento = notifica PASSIVA §2.7 (feedback no-forced-response): niente
    stringa 'rispondi sì', niente dialog/expandable_cap 'Allargo?' bloccante a
    fine turno — solo l'avviso di quanti elementi sono stati considerati."""
    import agent_runtime
    monkeypatch.setattr(agent_runtime, "TURN_LOG_DIR", tmp_path / "turns")

    log = _make_turnlog_with_truncated_step()
    log.ts_end = log.ts_start + 0.1
    log.write()

    # Vecchie UX interattive NON devono apparire (migrazione 6/5 + §2.7).
    assert "Per avere tutti i" not in log.final_message
    assert "rispondi **sì**" not in log.final_message
    # Nessun dialog bloccante: expandable_caps vuoto per il troncamento.
    assert log.expandable_caps == []
    # Notifica passiva presente: quanti elementi considerati (§2.7).
    assert "10" in log.final_message
    assert ("Troppi" in log.final_message or "primi" in log.final_message
            or "passaggio" in log.final_message)
