"""Onestà §2.8 dei run schedulati (12/6/2026) — bug live maintenance github.

Caso reale: query schedulata every_30m «leggi i nuovi issue aperti di
owner/repo... salva bozza e notificami» con 0 issue aperte su GitHub.
La pipeline find→filter→classify→write girava su 0 entries
(write n_written=0, store 0 righe) MA il final_message narrava «analizzato,
trovato simili, salvato bozze pronte, notificato» — falso successo §2.8 —
e il push schedulato lo consegnava ad ogni run (~20/giorno, trigger 30m).

Flusso UNIVERSALE (ADR 0172, 17/6): fetch = `find_issues_github` (skill);
memoria QA locale = `find_entries`/`write_entries` sullo store registrato
`github_issue_qa`; dedup = `compare_entries`/`filter_entries` a MONTE del
write. Le issue già trattate non rientrano nel write → run a vuoto.

Tre proprietà DI SISTEMA (indipendenti dalla formulazione del prompt §7.3):
1. `pipeline_effect_counts` + `_detect_false_success` (agent_runtime):
   il final che CLAIMA successo su pipeline vuota riceve notice correttiva.
2. `_scheduled_push_is_noop` (recurring_tasks): run schedulato a vuoto
   (0 items / 0 mutazioni effettive, nessun errore, nessun dialog) → 0 push.
3. dedup a monte: una issue già in `github_issue_qa` viene filtrata prima
   del write → write_entries su 0 record → idempotenza col loop scheduler,
   notifica UNA volta sola.

Determinismo §7.9: niente LLM, niente rete; run_turn e TelegramChannel
sono monkeypatched (nessuna notifica reale).
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import store as _store  # noqa: E402
import store_entries as se  # noqa: E402
from store_bootstrap import _ISSUE_QA  # noqa: E402

REPO = "owner/name"


@pytest.fixture()
def tmp_store(tmp_path, monkeypatch):
    """Store QA su db temporaneo via lo Store GENERICO (unico schema/owner —
    unificazione C2 21/6: github_issue_qa_store ritirato). Il flusso scrive via
    `write_entries`; guard/asserzioni leggono via `Store.find`: STESSO file."""
    db = tmp_path / "issue_qa.sqlite"
    st = _store.register(_ISSUE_QA, name="github_issue_qa", path=db)
    yield st
    _store.unregister("github_issue_qa")


@pytest.fixture()
def fake_embedder(monkeypatch):
    """Neutralizza l'embedder BGE-M3 (il flusso universale usa compare_entries,
    non più jobs.github_dedup): nessuna rete nei test."""
    mod = types.ModuleType("jobs.github_dedup")
    mod.embed_query = lambda text: None
    monkeypatch.setitem(sys.modules, "jobs.github_dedup", mod)
    return mod


def _step(tool: str, result: dict):
    return SimpleNamespace(chosen_tool=tool, result=result)


def _write(entries):
    """Scrive nello store github_issue_qa via il CRUD universale (forma del
    flusso reale). Ritorna il result di write_entries (n_written, results)."""
    return se.handle_write_entries(
        {"store": "github_issue_qa", "entries": entries,
         "key": ["repo", "issue_number"]})


# ── pipeline_effect_counts ────────────────────────────────────────────────

class TestPipelineEffectCounts:
    def test_no_steps_returns_none(self):
        from agent_runtime import pipeline_effect_counts
        assert pipeline_effect_counts([]) is None
        assert pipeline_effect_counts(None) is None

    def test_uncountable_steps_return_none(self):
        from agent_runtime import pipeline_effect_counts
        steps = [_step("get_now", {"ok": True, "now": "2026-06-12T10:00:00"}),
                 _step("final_answer", {"ok": True})]
        assert pipeline_effect_counts(steps) is None

    def test_empty_pipeline_counted(self):
        """Caso live: find 0 entries → write 0 → pipeline vuota."""
        from agent_runtime import pipeline_effect_counts
        steps = [
            _step("find_issues_github", {"ok": True, "entries": []}),
            _step("filter_entries", {"ok": True, "entries": []}),
            _step("write_entries", {"ok": True, "n_written": 0, "results": []}),
        ]
        c = pipeline_effect_counts(steps)
        assert c is not None
        assert c["items"] == 0 and c["mutations"] == 0
        assert c["mutating_attempted"] is True and c["failures"] == 0

    def test_producer_items_counted(self):
        from agent_runtime import pipeline_effect_counts
        steps = [_step("find_messages", {"ok": True, "entries": [{}, {}, {}]})]
        c = pipeline_effect_counts(steps)
        assert c["items"] == 3 and c["mutating_attempted"] is False

    def test_mutations_counted(self):
        from agent_runtime import pipeline_effect_counts
        steps = [_step("write_entries",
                       {"ok": True, "n_written": 2, "results": [{}, {}]})]
        c = pipeline_effect_counts(steps)
        assert c["mutations"] == 2

    def test_failed_step_counted_as_failure(self):
        from agent_runtime import pipeline_effect_counts
        steps = [_step("find_issues_github",
                       {"ok": False, "error": "rate_limited", "entries": []})]
        c = pipeline_effect_counts(steps)
        assert c["failures"] == 1


# ── _detect_false_success ─────────────────────────────────────────────────

class TestDetectFalseSuccess:
    EMPTY = {"countable": 3, "items": 0, "mutations": 0,
             "mutating_attempted": True, "failures": 0}

    def test_live_case_claim_on_empty_pipeline(self):
        """Il final del bug live 12/6 → detection True."""
        from agent_runtime import _detect_false_success
        msg = ("Ho analizzato le issue aperte di owner/name, trovato simili "
               "nel db, classificato e salvato le bozze come pronte. "
               "Ti ho notificato il risultato.")
        assert _detect_false_success(msg, self.EMPTY) is True

    def test_negated_claim_is_honest(self):
        from agent_runtime import _detect_false_success
        msg = "Non ho trovato issue aperte: niente da fare."
        assert _detect_false_success(msg, self.EMPTY) is False

    def test_claim_with_real_items_ok(self):
        from agent_runtime import _detect_false_success
        counts = dict(self.EMPTY, items=2, mutations=1)
        msg = "Ho analizzato 2 issue e salvato le bozze."
        assert _detect_false_success(msg, counts) is False

    def test_counts_none_no_detection(self):
        from agent_runtime import _detect_false_success
        assert _detect_false_success("Ho salvato tutto.", None) is False

    def test_failures_present_no_detection(self):
        """Run con errori: gestito dai path error-honesty, non da questo."""
        from agent_runtime import _detect_false_success
        counts = dict(self.EMPTY, failures=1)
        assert _detect_false_success("Ho salvato tutto.", counts) is False


class TestTurnLogFalseSuccessNotice:
    def test_write_replaces_false_success_with_honest_notice(self, tmp_path):
        """Regression turn 36a40c35/e591854e: il false-success SOSTITUISCE
        il narrato LLM ottimista, non lo antepone — l'utente non deve mai
        leggere «nessuna azione eseguita» seguito da «ho creato il foglio con
        i dati» nello stesso messaggio."""
        from agent_runtime import TurnLog, StepLog
        import agent_runtime as _ar
        from messages import get as _msg
        _orig = _ar.TURN_LOG_DIR
        _ar.TURN_LOG_DIR = tmp_path
        try:
            log = TurnLog(ts_start=time.time(), ts_end=time.time(),
                          user_query="leggi i nuovi issue aperti...",
                          turn_id="t_false_success", mode="local")
            s1 = StepLog(step_num=1)
            s1.chosen_tool = "find_issues_github"
            s1.result = {"ok": True, "entries": []}
            s2 = StepLog(step_num=2)
            s2.chosen_tool = "write_entries"
            s2.result = {"ok": True, "n_written": 0, "results": []}
            log.steps = [s1, s2]
            log.final_kind = "answer"
            log.final_message = ("Ho analizzato le issue aperte, salvato le "
                                 "bozze come pronte e ti ho notificato.")
            log.write()
            assert log.false_success_detected is True
            assert log.effect_counts["items"] == 0
            assert log.effect_counts["mutations"] == 0
            # SOSTITUZIONE: il final e' SOLO la notice onesta, il claim falso
            # ("analizzato"/"salvato") e' sparito dal messaggio user-facing.
            assert log.final_message == _msg("MSG_FALSE_SUCCESS_NOTICE")
            assert "analizzato" not in log.final_message
            assert "salvato" not in log.final_message
        finally:
            _ar.TURN_LOG_DIR = _orig

    def test_write_no_notice_with_real_effects(self, tmp_path):
        from agent_runtime import TurnLog, StepLog
        import agent_runtime as _ar
        _orig = _ar.TURN_LOG_DIR
        _ar.TURN_LOG_DIR = tmp_path
        try:
            log = TurnLog(ts_start=time.time(), ts_end=time.time(),
                          user_query="q", turn_id="t_real_fx", mode="local")
            s1 = StepLog(step_num=1)
            s1.chosen_tool = "find_issues_github"
            s1.result = {"ok": True, "entries": [{"number": 1}]}
            s2 = StepLog(step_num=2)
            s2.chosen_tool = "write_entries"
            s2.result = {"ok": True, "n_written": 1, "results": [{}]}
            log.steps = [s1, s2]
            log.final_kind = "answer"
            log.final_message = "Ho analizzato 1 issue e salvato la bozza."
            log.write()
            assert log.false_success_detected is False
            assert not log.final_message.startswith("⚠")
        finally:
            _ar.TURN_LOG_DIR = _orig


# ── _scheduled_push_is_noop ───────────────────────────────────────────────

def _fake_log(steps, *, final_kind="answer", caps=None,
              final_message="msg"):
    """Log minimale con effect_counts calcolato dal PATH REALE."""
    from agent_runtime import pipeline_effect_counts
    return SimpleNamespace(
        final_message=final_message, final_kind=final_kind,
        steps=steps, expandable_caps=caps or [],
        effect_counts=pipeline_effect_counts(steps), turn_id="t_fake")


class TestScheduledPushNoop:
    def test_empty_mutating_run_suppressed(self):
        from recurring_tasks import _scheduled_push_is_noop
        log = _fake_log([
            _step("find_issues_github", {"ok": True, "entries": []}),
            _step("write_entries", {"ok": True, "n_written": 0, "results": []}),
        ])
        assert _scheduled_push_is_noop(log) is True

    def test_rerun_known_issue_suppressed(self):
        """Issue ANCORA aperta ma già in github_issue_qa: find la rivede
        (1 entry) ma il dedup a monte (filter vs store) la rimuove → write
        su 0 record → niente ri-notifica."""
        from recurring_tasks import _scheduled_push_is_noop
        log = _fake_log([
            _step("find_issues_github",
                  {"ok": True, "entries": [{"number": 45}]}),
            _step("filter_entries", {"ok": True, "entries": []}),
            _step("write_entries",
                  {"ok": True, "n_written": 0, "results": []}),
        ])
        assert _scheduled_push_is_noop(log) is True

    def test_new_issue_pushes(self):
        from recurring_tasks import _scheduled_push_is_noop
        log = _fake_log([
            _step("find_issues_github",
                  {"ok": True, "entries": [{"number": 46}]}),
            _step("write_entries",
                  {"ok": True, "n_written": 1, "results": [{}]}),
        ])
        assert _scheduled_push_is_noop(log) is False

    def test_readonly_with_items_pushes(self):
        from recurring_tasks import _scheduled_push_is_noop
        log = _fake_log([
            _step("find_messages", {"ok": True, "entries": [{}, {}]}),
        ])
        assert _scheduled_push_is_noop(log) is False

    def test_readonly_zero_items_suppressed(self):
        from recurring_tasks import _scheduled_push_is_noop
        log = _fake_log([
            _step("find_messages", {"ok": True, "entries": []}),
        ])
        assert _scheduled_push_is_noop(log) is True

    def test_failures_never_suppressed(self):
        from recurring_tasks import _scheduled_push_is_noop
        log = _fake_log([
            _step("find_messages", {"ok": False, "error": "ssl"}),
        ])
        assert _scheduled_push_is_noop(log) is False

    def test_pending_dialog_never_suppressed(self):
        from recurring_tasks import _scheduled_push_is_noop
        log = _fake_log(
            [_step("write_entries", {"ok": True, "n_written": 0,
                                     "results": []})],
            caps=[{"kind": "get_inputs_response"}])
        assert _scheduled_push_is_noop(log) is False

    def test_non_answer_kind_never_suppressed(self):
        from recurring_tasks import _scheduled_push_is_noop
        log = _fake_log(
            [_step("find_messages", {"ok": True, "entries": []})],
            final_kind="error")
        assert _scheduled_push_is_noop(log) is False

    def test_uncountable_turn_never_suppressed(self):
        from recurring_tasks import _scheduled_push_is_noop
        log = _fake_log([_step("get_now", {"ok": True, "now": "..."})])
        assert _scheduled_push_is_noop(log) is False


# ── Scenario end-to-end SIMULATO (validazione del fix, nessun push reale) ──

class _CapturedTelegram:
    """Stub TelegramChannel: cattura i send, non invia nulla."""
    sent: list = []

    def send(self, chat_id, msg):
        _CapturedTelegram.sent.append((chat_id, msg))
        return {"ok": True}


def _run_scheduled(monkeypatch, steps, final_message):
    """Esegue il tratto schedulato dopo l'autorità già verificata.

    L'owner/session gate appartiene a ``_run_user_query_callback`` ed è
    esercitato separatamente. Qui si verifica il contratto di onestà del
    push, usando la callback scoped con un owner già ammesso.
    """
    import recurring_tasks as rt
    import agent_runtime as _ar
    import channels.telegram as _tg

    log = _fake_log(steps, final_message=final_message)
    monkeypatch.setattr(
        _ar, "run_turn",
        lambda q, actor=None, channel=None, owner_user_id=None: log)
    _CapturedTelegram.sent = []
    monkeypatch.setattr(_tg, "TelegramChannel", _CapturedTelegram)
    monkeypatch.setattr(rt, "_live_telegram_recipient", lambda _owner: "12345")
    record = {"name": "t_issue_maint", "label": "maintenance github",
              "query": "leggi i nuovi issue aperti di owner/name...",
              "actor": "host", "owner_user_id": "pytest-runtime-owner",
              "scheduler_name": "t_issue_maint", "channel": "telegram",
              "chat_id": 12345}
    out = rt._run_user_query_callback_scoped(record)
    return out, len(_CapturedTelegram.sent)


class TestEndToEndSimulated:
    def test_failed_read_is_pushed_but_callback_status_is_error(
            self, monkeypatch):
        """Consegna Telegram riuscita != scopo del task riuscito."""
        from scheduler_v2.models import CallbackOutcome
        steps = [_step("read_urls_html", {
            "ok": False, "entries": [], "ok_count": 0, "fail_count": 1,
            "failed": [{
                "url": "https://example.test/versions.html",
                "error": "temporary DNS failure",
                "error_class": "network",
            }],
        })]
        out, n_push = _run_scheduled(
            monkeypatch, steps,
            "Non posso leggere la pagina: temporary DNS failure.")
        assert n_push == 1
        assert isinstance(out, CallbackOutcome)
        assert out.status == "error"
        assert out.error == "temporary DNS failure"
        assert "pushed telegram" in out.output

    def test_phase1_zero_open_issues_zero_push_zero_rows(
            self, tmp_store, fake_embedder, monkeypatch):
        """Run a vuoto: 0 issue aperte → write_entries su 0 record → 0
        notifiche, 0 righe in github_issue_qa."""
        wres = _write([])
        assert wres["ok"] is True and wres["n_written"] == 0
        steps = [
            _step("find_issues_github", {"ok": True, "entries": []}),
            _step("write_entries", wres),
        ]
        out, n_push = _run_scheduled(
            monkeypatch, steps,
            "Ho analizzato le issue aperte, salvato le bozze e notificato.")
        assert n_push == 0
        assert "push suppressed" in out
        assert tmp_store.find(where={"repo": REPO}) == []

    def test_phase2_one_new_issue_one_push_one_row(
            self, tmp_store, fake_embedder, monkeypatch):
        """Issue nuova → write_entries scrive 1 record → 1 notifica
        (catturata) + 1 riga letta via Store.find (stesso db)."""
        entry = {"repo": REPO, "issue_number": 101, "title": "install fails",
                 "status": "prepared", "draft_reply": "try --check"}
        wres = _write([entry])
        assert wres["n_written"] == 1
        steps = [
            _step("find_issues_github",
                  {"ok": True, "entries": [{"number": 101}]}),
            _step("write_entries", wres),
        ]
        out, n_push = _run_scheduled(
            monkeypatch, steps, "Ho preparato 1 bozza per la issue #101.")
        assert n_push == 1
        rows = tmp_store.find(where={"repo": REPO})
        assert len(rows) == 1 and rows[0]["issue_number"] == 101

    def test_phase3_rerun_same_issue_dedup_zero_push(
            self, tmp_store, fake_embedder, monkeypatch):
        """Ri-esecuzione sulla STESSA issue ancora aperta → il dedup a monte
        (filter/compare vs store) la rimuove → write_entries su 0 record →
        0 notifiche, riga invariata (niente churn)."""
        entry = {"repo": REPO, "issue_number": 101, "title": "install fails",
                 "status": "prepared", "draft_reply": "first draft"}
        assert _write([entry])["n_written"] == 1
        # re-run: il find remoto rivede la issue (ancora open), il dedup la
        # filtra → write a vuoto.
        rerun = _write([])
        assert rerun["n_written"] == 0
        steps = [
            _step("find_issues_github",
                  {"ok": True, "entries": [{"number": 101}]}),
            _step("find_entries",
                  {"ok": True, "entries": [{"repo": REPO,
                                            "issue_number": 101}]}),
            _step("filter_entries", {"ok": True, "entries": []}),
            _step("write_entries", rerun),
        ]
        out, n_push = _run_scheduled(
            monkeypatch, steps, "Ho salvato le bozze per le issue trovate.")
        assert n_push == 0
        assert "push suppressed" in out
        rows = tmp_store.find(where={"repo": REPO})
        assert len(rows) == 1
        assert rows[0]["draft_reply"] == "first draft"  # niente churn
